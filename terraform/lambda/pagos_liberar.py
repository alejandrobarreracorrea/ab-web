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
        except calcom.CalComError:
            continue

        try:
            TABLE.update_item(
                Key={"booking_uid": booking_uid},
                UpdateExpression="SET estado = :e",
                ConditionExpression="estado = :p",
                ExpressionAttributeValues={":e": "expirado", ":p": "pendiente"},
            )
            liberados += 1
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    return {"ok": True, "liberados": liberados}
