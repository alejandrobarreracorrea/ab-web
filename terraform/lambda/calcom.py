"""Confirmar/declinar reservas vía API v2 de Cal.com y validar la firma de
sus webhooks salientes (header x-cal-signature-256, HMAC-SHA256 del body raw).
"""
import hashlib
import hmac
import json
import urllib.error
import urllib.request

API_BASE = "https://api.cal.com/v2"
# /confirm y /decline exigen esta version exacta; una version vieja (ej.
# 2024-06-14) hace que Cal.com responda 404 "Cannot POST" por no tener
# la ruta registrada en ese snapshot de la API. Confirmado en vivo.
API_VERSION = "2026-02-25"


class CalComError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def validar_firma_webhook(body_raw: bytes, header_signature: str, secret: str) -> bool:
    if not header_signature:
        return False
    if not isinstance(body_raw, bytes):
        return False
    if not isinstance(secret, str) or not secret:
        return False
    try:
        calculado = hmac.new(secret.encode(), body_raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(calculado, header_signature.lower())
    except (TypeError, AttributeError):
        return False


def _llamar(path: str, api_key: str, body: dict | None = None) -> dict:
    url = f"{API_BASE}{path}"
    datos = json.dumps(body if body is not None else {}).encode()
    solicitud = urllib.request.Request(
        url,
        data=datos,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "cal-api-version": API_VERSION,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(solicitud, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode() if hasattr(exc, "read") else ""
        raise CalComError(f"{exc.code}: {detalle}", status=exc.code) from exc
    except urllib.error.URLError as exc:
        raise CalComError(str(exc), status=None) from exc


def confirmar_reserva(booking_uid: str, api_key: str) -> dict:
    return _llamar(f"/bookings/{booking_uid}/confirm", api_key)


def declinar_reserva(booking_uid: str, api_key: str, motivo: str) -> dict:
    return _llamar(f"/bookings/{booking_uid}/decline", api_key, body={"reason": motivo})
