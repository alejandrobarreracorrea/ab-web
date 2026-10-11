"""ab-pagos-iniciar: recibe el webhook BOOKING_REQUESTED de Cal.com y arranca
el cobro en Wompi; tambien expone una ruta de solo lectura (GET) para que la
pagina /mentoria/pagar/ recupere los datos del checkout dado un booking_uid.

POST (webhook Cal.com, firmado con x-cal-signature-256):
    {"triggerEvent": "BOOKING_REQUESTED", "payload": {"uid": "...", "attendees": [{"email": "..."}]}}
GET ?uid=<booking_uid>:
    {"ok": true, "estado": "...", "monto_cop": ..., "amount_in_cents": ...,
     "reference": ..., "signature": ..., "public_key": ..., "currency": "COP"}
"""
import json
import os
import time
from decimal import Decimal

import boto3

import calcom
import trm
import wompi

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
SSM = boto3.client("ssm")
SES = boto3.client("sesv2")
SENDER = os.environ["SENDER"]
PRECIO_USD = float(os.environ["PRECIO_USD"])
PAGINA_PAGO_URL = os.environ["PAGINA_PAGO_URL"]
CREDENCIALES_SSM = os.environ["CREDENCIALES_SSM"]

EXPIRA_SEGUNDOS = 2 * 60 * 60
TRM_CACHE_KEY = "trm_cache"

_creds_cache = None


def _credenciales():
    global _creds_cache
    if _creds_cache is None:
        resp = SSM.get_parameter(Name=CREDENCIALES_SSM, WithDecryption=True)
        _creds_cache = json.loads(resp["Parameter"]["Value"])
    return _creds_cache


def _resp(status, payload):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(payload)}


def _trm_con_cache():
    valor = trm.obtener_trm_actual()
    now = int(time.time())
    if valor is not None:
        TABLE.put_item(Item={"booking_uid": TRM_CACHE_KEY, "valor": Decimal(str(valor)), "fecha": now})
        return valor
    cache = TABLE.get_item(Key={"booking_uid": TRM_CACHE_KEY}).get("Item")
    return float(cache["valor"]) if cache else None


def _enviar_link_pago(email, link):
    SES.send_email(
        FromEmailAddress=SENDER,
        Destination={"ToAddresses": [email]},
        Content={"Simple": {
            "Subject": {"Data": "Completa tu pago para confirmar tu mentoria - IAOps", "Charset": "UTF-8"},
            "Body": {
                "Text": {"Charset": "UTF-8", "Data": (
                    f"Hola,\n\nTu horario de mentoria quedo reservado. Para confirmarlo, "
                    f"completa el pago aqui:\n{link}\n\n"
                    f"Tienes 2 horas antes de que el cupo se libere.\n\n"
                    f"- Alejandro Barrera - IAOps")},
                "Html": {"Charset": "UTF-8", "Data": (
                    "<div style='background:#08080b;color:#f5f5f7;font-family:Arial,sans-serif;"
                    "padding:40px;border-radius:16px;max-width:520px;margin:auto'>"
                    "<p style='color:#9a8cff;font-size:13px;letter-spacing:2px;margin:0 0 18px'>"
                    "IAOPS &middot; MENTORÍA 1:1</p>"
                    "<p style='font-size:16px;margin:0 0 20px'>Tu horario quedó reservado. "
                    "Completa el pago para confirmarlo:</p>"
                    f"<p style='margin:24px 0'><a href='{link}' style='background:linear-gradient(100deg,#8b7bff,#38e0d8);"
                    "color:#fff;padding:14px 28px;border-radius:999px;text-decoration:none;"
                    "font-weight:600;display:inline-block'>Completar pago</a></p>"
                    "<p style='color:#9c9caa;font-size:13px'>Tienes 2 horas antes de que el cupo se libere.</p>"
                    "<p style='color:#66666f;font-size:12px;margin-top:28px'>- Alejandro Barrera &middot; IAOps</p></div>")},
            },
        }},
    )


def _manejar_webhook(event, creds):
    header_sig = (event.get("headers") or {}).get("x-cal-signature-256", "")
    body_raw = (event.get("body") or "").encode()
    if not calcom.validar_firma_webhook(body_raw, header_sig, creds["calcom_webhook_secret"]):
        return _resp(401, {"ok": False, "error": "firma"})

    try:
        data = json.loads(body_raw)
    except ValueError:
        return _resp(400, {"ok": False, "error": "json"})

    if not isinstance(data, dict) or data.get("triggerEvent") != "BOOKING_REQUESTED":
        return _resp(200, {"ok": True, "ignorado": True})

    payload = data.get("payload") or {}
    if not isinstance(payload, dict):
        return _resp(400, {"ok": False, "error": "payload"})
    booking_uid = payload.get("uid")
    attendees = payload.get("attendees") or []
    email = None
    if isinstance(attendees, list) and attendees and isinstance(attendees[0], dict):
        email = attendees[0].get("email")
    if not booking_uid or not email or not isinstance(email, str):
        return _resp(400, {"ok": False, "error": "payload"})
    email = email.strip().lower()

    trm_valor = _trm_con_cache()
    if not trm_valor or trm_valor <= 0:
        return _resp(500, {"ok": False, "error": "trm_no_disponible"})

    monto_cop = round(PRECIO_USD * trm_valor)
    amount_in_cents = monto_cop * 100
    reference = booking_uid
    signature = wompi.firmar_integridad(reference, amount_in_cents, "COP", creds["wompi_integrity_secret"])

    now = int(time.time())
    try:
        TABLE.put_item(
            Item={
                "booking_uid": booking_uid,
                "estado": "pendiente",
                "email": email,
                "monto_cop": monto_cop,
                "monto_usd": Decimal(str(PRECIO_USD)),
                "wompi_reference": reference,
                "wompi_signature": signature,
                "creado_en": now,
                "expira_en": now + EXPIRA_SEGUNDOS,
            },
            ConditionExpression="attribute_not_exists(booking_uid) OR estado = :pendiente",
            ExpressionAttributeValues={":pendiente": "pendiente"},
        )
    except TABLE.meta.client.exceptions.ConditionalCheckFailedException:
        return _resp(200, {"ok": True, "ya_procesado": True})

    link = f"{PAGINA_PAGO_URL}?uid={booking_uid}"
    _enviar_link_pago(email, link)
    return _resp(200, {"ok": True})


def _manejar_consulta(event):
    params = event.get("queryStringParameters") or {}
    booking_uid = params.get("uid")
    if not booking_uid:
        return _resp(400, {"ok": False, "error": "uid"})

    item = TABLE.get_item(Key={"booking_uid": booking_uid}).get("Item")
    if not item:
        return _resp(404, {"ok": False, "error": "no_encontrado"})

    monto_cop = int(item["monto_cop"])
    return _resp(200, {
        "ok": True,
        "estado": item["estado"],
        "monto_cop": monto_cop,
        "amount_in_cents": monto_cop * 100,
        "reference": item["wompi_reference"],
        "signature": item.get("wompi_signature", ""),
        "public_key": _credenciales()["wompi_public_key"],
        "currency": "COP",
    })


def handler(event, _context):
    event = event or {}
    method = ((event.get("requestContext") or {}).get("http") or {}).get("method", "")
    if method == "POST":
        return _manejar_webhook(event, _credenciales())
    if method == "GET":
        return _manejar_consulta(event)
    return _resp(405, {"ok": False, "error": "method"})
