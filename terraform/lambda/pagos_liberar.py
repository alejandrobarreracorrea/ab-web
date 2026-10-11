"""ab-pagos-liberar: cron (EventBridge) que declina en Cal.com las reservas
pendientes de pago con mas de 2 horas sin pagarse, liberando el cupo.
"""
import json
import os
import time

import boto3
from botocore.exceptions import ClientError

import calcom

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
SSM = boto3.client("ssm")
CREDENCIALES_SSM = os.environ["CREDENCIALES_SSM"]

EXPIRA_SEGUNDOS = 2 * 60 * 60

_creds_cache = None


def _credenciales():
    global _creds_cache
    if _creds_cache is None:
        resp = SSM.get_parameter(Name=CREDENCIALES_SSM, WithDecryption=True)
        _creds_cache = json.loads(resp["Parameter"]["Value"])
    return _creds_cache


def _marcar_expirado(booking_uid):
    """Intenta marcar el item como expirado (y lo saca de TTL). Devuelve True si
    lo marco, False si no (p.ej. ya se pago entre el scan y este intento)."""
    try:
        TABLE.update_item(
            Key={"booking_uid": booking_uid},
            UpdateExpression="SET estado = :e REMOVE expira_en",
            ConditionExpression="estado = :p",
            ExpressionAttributeValues={":e": "expirado", ":p": "pendiente"},
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return False


def handler(_event, _context):
    creds = _credenciales()
    now = int(time.time())
    liberados = 0

    respuesta = TABLE.scan(
        FilterExpression="estado = :p AND creado_en < :limite",
        ExpressionAttributeValues={":p": "pendiente", ":limite": now - EXPIRA_SEGUNDOS},
    )
    for item in respuesta.get("Items", []):
        booking_uid = item["booking_uid"]
        try:
            calcom.declinar_reserva(booking_uid, creds["calcom_api_key"], "Pago no completado")
        except calcom.CalComError as exc:
            if exc.status is not None and 400 <= exc.status < 500:
                # La reserva ya no existe/cambio del lado de Cal.com (4xx): no hay
                # nada que reintentar, liberamos el cupo de todos modos.
                if _marcar_expirado(booking_uid):
                    liberados += 1
                continue
            # Falla transitoria (5xx o sin respuesta HTTP): dejamos el item
            # pendiente para reintentar en el proximo ciclo.
            print(f"pagos-liberar: fallo transitorio al declinar {booking_uid}, reintenta en el proximo ciclo")
            continue

        if _marcar_expirado(booking_uid):
            liberados += 1

    return {"ok": True, "liberados": liberados}
