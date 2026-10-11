"""ab-pagos-confirmar: recibe el webhook transaction.updated de Wompi, valida
el checksum y confirma la reserva en Cal.com cuando el pago fue aprobado.
"""
import json
import os
import re

import boto3

import calcom
import wompi

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
SSM = boto3.client("ssm")
SES = boto3.client("sesv2")
CREDENCIALES_SSM = os.environ["CREDENCIALES_SSM"]
SENDER = os.environ["SENDER"]

_creds_cache = None


def _credenciales():
    global _creds_cache
    if _creds_cache is None:
        resp = SSM.get_parameter(Name=CREDENCIALES_SSM, WithDecryption=True)
        _creds_cache = json.loads(resp["Parameter"]["Value"])
    return _creds_cache


def _resp(status, payload):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(payload)}


def _alertar_pagado_sin_cupo(booking_uid, email_cliente, transaction_id):
    match = re.search(r"<(.+)>", SENDER)
    destino = match.group(1) if match else SENDER
    SES.send_email(
        FromEmailAddress=SENDER,
        Destination={"ToAddresses": [destino]},
        Content={"Simple": {
            "Subject": {"Data": "Pago sin cupo — revisar manualmente", "Charset": "UTF-8"},
            "Body": {
                "Text": {"Charset": "UTF-8", "Data": (
                    f"El pago de la reserva booking_uid={booking_uid} fue aprobado por Wompi "
                    f"(wompi_transaction_id={transaction_id}) pero Cal.com no confirmo la reserva "
                    f"(sin cupo disponible).\n\n"
                    f"Cliente: {email_cliente}\n\n"
                    f"Revisar manualmente: reagendar o reembolsar desde el dashboard de Wompi.")},
            },
        }},
    )


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
    if item["estado"] in ("pagado", "pagado_sin_cupo"):
        return _resp(200, {"ok": True, "ya_procesado": True})

    try:
        calcom.confirmar_reserva(reference, creds["calcom_api_key"])
    except calcom.CalComError as exc:
        if exc.status is None or exc.status >= 500:
            # Falla transitoria de Cal.com (5xx o red): no tocamos el estado del
            # item (puede seguir pendiente o expirado) y devolvemos 500 para que
            # Wompi reintente la entrega del webhook mas tarde.
            return _resp(500, {"ok": False, "error": "calcom_temporal"})

        TABLE.update_item(
            Key={"booking_uid": reference},
            UpdateExpression="SET estado = :e, wompi_transaction_id = :tid REMOVE expira_en",
            ExpressionAttributeValues={":e": "pagado_sin_cupo", ":tid": transaction_id},
        )
        print(f"ALERTA pagado_sin_cupo: booking_uid={reference} transaction_id={transaction_id}")
        _alertar_pagado_sin_cupo(reference, item.get("email"), transaction_id)
        return _resp(200, {"ok": True, "pagado_sin_cupo": True})

    TABLE.update_item(
        Key={"booking_uid": reference},
        UpdateExpression="SET estado = :e, wompi_transaction_id = :tid REMOVE expira_en",
        ExpressionAttributeValues={":e": "pagado", ":tid": transaction_id},
    )
    return _resp(200, {"ok": True})
