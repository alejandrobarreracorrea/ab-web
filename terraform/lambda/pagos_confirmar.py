"""ab-pagos-confirmar: recibe el webhook transaction.updated de Wompi, valida
el checksum y confirma la reserva en Cal.com cuando el pago fue aprobado.
"""
import json
import os

import boto3

import calcom
import wompi

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
SSM = boto3.client("ssm")
CREDENCIALES_SSM = os.environ["CREDENCIALES_SSM"]

_creds_cache = None


def _credenciales():
    global _creds_cache
    if _creds_cache is None:
        resp = SSM.get_parameter(Name=CREDENCIALES_SSM, WithDecryption=True)
        _creds_cache = json.loads(resp["Parameter"]["Value"])
    return _creds_cache


def _resp(status, payload):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(payload)}


def handler(event, _context):
    event = event or {}
    method = ((event.get("requestContext") or {}).get("http") or {}).get("method", "")
    if method != "POST":
        return _resp(405, {"ok": False, "error": "method"})

    creds = _credenciales()
    try:
        cuerpo = json.loads(event.get("body") or "{}")
    except ValueError:
        return _resp(400, {"ok": False, "error": "json"})

    if not wompi.validar_checksum(cuerpo, creds["wompi_events_secret"]):
        return _resp(400, {"ok": False, "error": "checksum"})

    transaccion = (cuerpo.get("data") or {}).get("transaction") or {}
    estado_pago = transaccion.get("status")
    reference = transaccion.get("reference")
    transaction_id = transaccion.get("id")
    if estado_pago != "APPROVED" or not reference:
        return _resp(200, {"ok": True, "ignorado": True})

    item = TABLE.get_item(Key={"booking_uid": reference}).get("Item")
    if not item:
        return _resp(200, {"ok": True, "ignorado": True})
    if item["estado"] != "pendiente":
        return _resp(200, {"ok": True, "ya_procesado": True})

    try:
        calcom.confirmar_reserva(reference, creds["calcom_api_key"])
    except calcom.CalComError:
        TABLE.update_item(
            Key={"booking_uid": reference},
            UpdateExpression="SET estado = :e, wompi_transaction_id = :tid",
            ExpressionAttributeValues={":e": "pagado_sin_cupo", ":tid": transaction_id},
        )
        return _resp(200, {"ok": True, "pagado_sin_cupo": True})

    TABLE.update_item(
        Key={"booking_uid": reference},
        UpdateExpression="SET estado = :e, wompi_transaction_id = :tid",
        ExpressionAttributeValues={":e": "pagado", ":tid": transaction_id},
    )
    return _resp(200, {"ok": True})
