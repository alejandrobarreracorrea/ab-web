import hashlib
import hmac
import importlib
import json
import os
import sys
import time
from decimal import Decimal
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("TABLE_NAME", "ab-pagos-test")
os.environ.setdefault("CREDENCIALES_SSM", "/ab-pagos/test/credenciales")
os.environ.setdefault("SENDER", "Alejandro <hola@alejandrobarrera.net>")
os.environ.setdefault("PRECIO_USD", "80")
os.environ.setdefault("PAGINA_PAGO_URL", "https://iaopslatam.com/mentoria/pagar/")

REGION = "us-east-1"


@pytest.fixture
def entorno_aws():
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name=REGION)
        dynamodb.create_table(
            TableName="ab-pagos-test",
            KeySchema=[{"AttributeName": "booking_uid", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "booking_uid", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        ssm = boto3.client("ssm", region_name=REGION)
        ssm.put_parameter(
            Name="/ab-pagos/test/credenciales",
            Type="SecureString",
            Value=json.dumps({
                "wompi_public_key": "pub_test_x",
                "wompi_private_key": "prv_test_x",
                "wompi_integrity_secret": "integridad_secreta",
                "wompi_events_secret": "eventos_secreto",
                "calcom_api_key": "cal_live_x",
                "calcom_webhook_secret": "webhook_secreto",
            }),
        )
        yield


@pytest.fixture
def modulo(entorno_aws):
    import pagos_iniciar as m
    importlib.reload(m)  # recrea TABLE/SSM/SES contra el mock_aws ya activo
    m._creds_cache = None
    return m


def _firmar(body_bytes, secret):
    return hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()


def _evento_webhook(uid="abc123", email="Cliente@Example.com  "):
    body = json.dumps({
        "triggerEvent": "BOOKING_REQUESTED",
        "payload": {"uid": uid, "attendees": [{"email": email}]},
    }).encode()
    firma = _firmar(body, "webhook_secreto")
    return {
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"x-cal-signature-256": firma},
        "body": body.decode(),
    }


@patch("pagos_iniciar.trm.obtener_trm_actual", return_value=4000.0)
@patch("pagos_iniciar.SES")
def test_webhook_booking_requested_guarda_pendiente_y_envia_correo(mock_ses, _mock_trm, modulo):
    resultado = modulo.handler(_evento_webhook(), None)

    assert resultado["statusCode"] == 200
    item = modulo.TABLE.get_item(Key={"booking_uid": "abc123"}).get("Item")
    assert item["estado"] == "pendiente"
    assert item["monto_cop"] == 320000  # 80 USD * 4000 TRM
    assert item["email"] == "cliente@example.com"  # normalizado
    mock_ses.send_email.assert_called_once()


@patch("pagos_iniciar.trm.obtener_trm_actual", return_value=4000.0)
@patch("pagos_iniciar.SES")
def test_webhook_con_firma_invalida_no_guarda_nada_ni_envia_correo(mock_ses, _mock_trm, modulo):
    evento = _evento_webhook(uid="x")
    evento["headers"]["x-cal-signature-256"] = "firma-incorrecta"

    resultado = modulo.handler(evento, None)

    assert resultado["statusCode"] == 401
    assert modulo.TABLE.get_item(Key={"booking_uid": "x"}).get("Item") is None
    mock_ses.send_email.assert_not_called()


@patch("pagos_iniciar.trm.obtener_trm_actual", return_value=4000.0)
@patch("pagos_iniciar.SES")
def test_webhook_duplicado_no_reenvia_correo_si_ya_esta_pagado(mock_ses, _mock_trm, modulo):
    modulo.TABLE.put_item(Item={
        "booking_uid": "abc123", "estado": "pagado", "email": "cliente@example.com",
        "monto_cop": 320000, "monto_usd": Decimal("80"), "wompi_reference": "abc123",
        "wompi_signature": "firma", "creado_en": int(time.time()), "expira_en": int(time.time()) + 7200,
    })

    resultado = modulo.handler(_evento_webhook(), None)

    assert resultado["statusCode"] == 200
    item = modulo.TABLE.get_item(Key={"booking_uid": "abc123"}).get("Item")
    assert item["estado"] == "pagado"  # no se sobrescribio
    mock_ses.send_email.assert_not_called()


@patch("pagos_iniciar.trm.obtener_trm_actual", return_value=None)
def test_webhook_sin_trm_y_sin_cache_responde_500(_mock_trm, modulo):
    resultado = modulo.handler(_evento_webhook(uid="sintrm"), None)
    assert resultado["statusCode"] == 500
    assert modulo.TABLE.get_item(Key={"booking_uid": "sintrm"}).get("Item") is None


@patch("pagos_iniciar.trm.obtener_trm_actual", return_value=None)
@patch("pagos_iniciar.SES")
def test_webhook_usa_trm_cacheada_si_la_consulta_en_vivo_falla(mock_ses, _mock_trm, modulo):
    modulo.TABLE.put_item(Item={"booking_uid": modulo.TRM_CACHE_KEY, "valor": Decimal("4500"), "fecha": int(time.time())})

    resultado = modulo.handler(_evento_webhook(uid="concache"), None)

    assert resultado["statusCode"] == 200
    item = modulo.TABLE.get_item(Key={"booking_uid": "concache"}).get("Item")
    assert item["monto_cop"] == 360000  # 80 * 4500


def test_get_devuelve_datos_de_checkout_para_reserva_pendiente(modulo):
    modulo.TABLE.put_item(Item={
        "booking_uid": "abc123", "estado": "pendiente", "email": "a@b.com",
        "monto_cop": 320000, "monto_usd": Decimal("80"), "wompi_reference": "abc123",
        "wompi_signature": "firma-guardada", "creado_en": int(time.time()), "expira_en": int(time.time()) + 7200,
    })
    event = {"requestContext": {"http": {"method": "GET"}}, "queryStringParameters": {"uid": "abc123"}}

    resultado = modulo.handler(event, None)
    cuerpo = json.loads(resultado["body"])

    assert resultado["statusCode"] == 200
    assert cuerpo["estado"] == "pendiente"
    assert cuerpo["amount_in_cents"] == 32000000
    assert cuerpo["signature"] == "firma-guardada"
    assert cuerpo["public_key"] == "pub_test_x"


def test_get_sin_uid_devuelve_400(modulo):
    event = {"requestContext": {"http": {"method": "GET"}}, "queryStringParameters": {}}
    resultado = modulo.handler(event, None)
    assert resultado["statusCode"] == 400


def test_get_uid_inexistente_devuelve_404(modulo):
    event = {"requestContext": {"http": {"method": "GET"}}, "queryStringParameters": {"uid": "no-existe"}}
    resultado = modulo.handler(event, None)
    assert resultado["statusCode"] == 404


def test_metodo_no_soportado_devuelve_405(modulo):
    event = {"requestContext": {"http": {"method": "DELETE"}}}
    resultado = modulo.handler(event, None)
    assert resultado["statusCode"] == 405
