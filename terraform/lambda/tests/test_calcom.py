import hashlib
import hmac
import json
import os
import sys
import urllib.error
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import calcom


def test_validar_firma_webhook_acepta_firma_correcta():
    secret = "wh_secret"
    body = b'{"triggerEvent":"BOOKING_REQUESTED"}'
    firma = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert calcom.validar_firma_webhook(body, firma, secret) is True


def test_validar_firma_webhook_rechaza_firma_incorrecta():
    body = b'{"a":1}'
    assert calcom.validar_firma_webhook(body, "deadbeef", "secret") is False


def test_validar_firma_webhook_rechaza_firma_vacia():
    assert calcom.validar_firma_webhook(b"{}", "", "secret") is False


def test_validar_firma_webhook_rechaza_secret_none():
    assert calcom.validar_firma_webhook(b"{}", "somehash", None) is False


def test_validar_firma_webhook_rechaza_secret_vacio():
    assert calcom.validar_firma_webhook(b"{}", "somehash", "") is False


def test_validar_firma_webhook_rechaza_body_no_bytes():
    assert calcom.validar_firma_webhook("not-bytes-a-string", "somehash", "secret") is False


@patch("calcom.urllib.request.urlopen")
def test_confirmar_reserva_llama_endpoint_correcto(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"status": "success"}).encode()
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    resultado = calcom.confirmar_reserva("abc123", "cal_live_x")

    assert resultado == {"status": "success"}
    solicitud = mock_urlopen.call_args[0][0]
    assert solicitud.full_url == "https://api.cal.com/v2/bookings/abc123/confirm"
    assert solicitud.get_header("Authorization") == "Bearer cal_live_x"
    assert solicitud.get_header("Cal-api-version") == "2026-02-25"


@patch("calcom.urllib.request.urlopen")
def test_declinar_reserva_envia_motivo_en_el_body(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"status": "success"}).encode()
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    calcom.declinar_reserva("abc123", "cal_live_x", "Pago no completado")

    solicitud = mock_urlopen.call_args[0][0]
    assert solicitud.full_url == "https://api.cal.com/v2/bookings/abc123/decline"
    cuerpo = json.loads(solicitud.data)
    assert cuerpo == {"reason": "Pago no completado"}


@patch("calcom.urllib.request.urlopen")
def test_confirmar_reserva_lanza_calcom_error_en_http_error(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.HTTPError(
        url="x", code=403, msg="Forbidden", hdrs=None, fp=MagicMock(read=lambda: b'{"error":"no cupo"}')
    )
    try:
        calcom.confirmar_reserva("abc123", "cal_live_x")
        assert False, "debia lanzar CalComError"
    except calcom.CalComError as exc:
        assert "403" in str(exc)
        assert exc.status == 403


@patch("calcom.urllib.request.urlopen")
def test_calcom_error_de_url_error_tiene_status_none(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.URLError("timed out")
    try:
        calcom.confirmar_reserva("abc123", "cal_live_x")
        assert False, "debia lanzar CalComError"
    except calcom.CalComError as exc:
        assert exc.status is None
