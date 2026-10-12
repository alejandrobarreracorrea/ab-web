import hashlib
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
                "wompi_events_secret": "eventos_secreto",
                "calcom_api_key": "cal_live_x",
            }),
        )
        yield


@pytest.fixture
def modulo(entorno_aws):
    import pagos_confirmar as m
    importlib.reload(m)
    m._creds_cache = None
    return m


def _evento_wompi(reference="abc123", status="APPROVED", secret="eventos_secreto", transaction_id="tx-1"):
    timestamp = 1530291411
    data = {"transaction": {"id": transaction_id, "status": status, "reference": reference}}
    crudo = f"{transaction_id}{status}{reference}{timestamp}{secret}"
    checksum = hashlib.sha256(crudo.encode()).hexdigest()
    body = {
        "data": data,
        "signature": {
            "properties": ["transaction.id", "transaction.status", "transaction.reference"],
            "checksum": checksum,
        },
        "timestamp": timestamp,
    }
    return {"requestContext": {"http": {"method": "POST"}}, "body": json.dumps(body)}


@patch("pagos_confirmar.calcom.confirmar_reserva")
def test_pago_aprobado_confirma_la_reserva_y_marca_pagado(mock_confirmar, modulo):
    modulo.TABLE.put_item(Item={
        "booking_uid": "abc123", "estado": "pendiente", "email": "a@b.com",
        "monto_cop": 320000, "monto_usd": Decimal("80"), "wompi_reference": "abc123",
        "creado_en": int(time.time()), "expira_en": int(time.time()) + 7200,
    })

    resultado = modulo.handler(_evento_wompi(), None)

    assert resultado["statusCode"] == 200
    mock_confirmar.assert_called_once_with("abc123", "cal_live_x")
    item = modulo.TABLE.get_item(Key={"booking_uid": "abc123"}).get("Item")
    assert item["estado"] == "pagado"
    assert item["wompi_transaction_id"] == "tx-1"
    assert "expira_en" not in item  # Fix 2: TTL removido al salir de "pendiente"


@patch("pagos_confirmar.calcom.confirmar_reserva")
def test_checksum_invalido_no_confirma_nada(mock_confirmar, modulo):
    modulo.TABLE.put_item(Item={
        "booking_uid": "abc123", "estado": "pendiente", "wompi_reference": "abc123",
        "creado_en": int(time.time()), "expira_en": int(time.time()) + 7200,
    })
    evento = _evento_wompi(secret="secreto-equivocado")

    resultado = modulo.handler(evento, None)

    assert resultado["statusCode"] == 400
    mock_confirmar.assert_not_called()
    item = modulo.TABLE.get_item(Key={"booking_uid": "abc123"}).get("Item")
    assert item["estado"] == "pendiente"


@patch("pagos_confirmar.calcom.confirmar_reserva")
def test_pago_no_aprobado_se_ignora(mock_confirmar, modulo):
    modulo.TABLE.put_item(Item={
        "booking_uid": "abc123", "estado": "pendiente", "wompi_reference": "abc123",
        "creado_en": int(time.time()), "expira_en": int(time.time()) + 7200,
    })

    resultado = modulo.handler(_evento_wompi(status="DECLINED"), None)

    assert resultado["statusCode"] == 200
    mock_confirmar.assert_not_called()
    item = modulo.TABLE.get_item(Key={"booking_uid": "abc123"}).get("Item")
    assert item["estado"] == "pendiente"


@patch("pagos_confirmar.calcom.confirmar_reserva")
def test_reference_sin_item_en_dynamodb_no_falla(mock_confirmar, modulo):
    resultado = modulo.handler(_evento_wompi(reference="no-existe"), None)
    assert resultado["statusCode"] == 200
    mock_confirmar.assert_not_called()


@patch("pagos_confirmar.calcom.confirmar_reserva")
def test_webhook_reintentado_sobre_item_ya_pagado_es_idempotente(mock_confirmar, modulo):
    modulo.TABLE.put_item(Item={
        "booking_uid": "abc123", "estado": "pagado", "wompi_reference": "abc123",
        "wompi_transaction_id": "tx-1", "creado_en": int(time.time()), "expira_en": int(time.time()) + 7200,
    })

    resultado = modulo.handler(_evento_wompi(), None)

    assert resultado["statusCode"] == 200
    mock_confirmar.assert_not_called()


@patch("pagos_confirmar.SES")
def test_pago_aprobado_pero_reserva_sin_cupo_marca_pagado_sin_cupo(mock_ses, modulo):
    modulo.TABLE.put_item(Item={
        "booking_uid": "abc123", "estado": "pendiente", "email": "a@b.com", "wompi_reference": "abc123",
        "creado_en": int(time.time()), "expira_en": int(time.time()) + 7200,
    })
    import calcom
    with patch(
        "pagos_confirmar.calcom.confirmar_reserva",
        side_effect=calcom.CalComError("403: no cupo", status=403),
    ):
        resultado = modulo.handler(_evento_wompi(), None)

    assert resultado["statusCode"] == 200
    item = modulo.TABLE.get_item(Key={"booking_uid": "abc123"}).get("Item")
    assert item["estado"] == "pagado_sin_cupo"
    assert "expira_en" not in item  # Fix 2: TTL removido al salir de "pendiente"
    mock_ses.send_email.assert_called_once()  # Fix 7: alerta por correo
    destino = mock_ses.send_email.call_args.kwargs["Destination"]
    assert destino == {"ToAddresses": ["hola@alejandrobarrera.net"]}


def test_pago_tardio_sobre_item_expirado_confirma_la_reserva_y_marca_pagado(modulo):
    """Fix 1: un item "expirado" (pago llego tarde) debe intentar confirmar en
    Cal.com igual que uno "pendiente", y terminar "pagado" si Cal.com acepta."""
    modulo.TABLE.put_item(Item={
        "booking_uid": "abc123", "estado": "expirado", "wompi_reference": "abc123",
        "creado_en": int(time.time()), "expira_en": int(time.time()) + 7200,
    })
    with patch("pagos_confirmar.calcom.confirmar_reserva") as mock_confirmar:
        resultado = modulo.handler(_evento_wompi(), None)

    assert resultado["statusCode"] == 200
    mock_confirmar.assert_called_once_with("abc123", "cal_live_x")
    item = modulo.TABLE.get_item(Key={"booking_uid": "abc123"}).get("Item")
    assert item["estado"] == "pagado"


@patch("pagos_confirmar.SES")
def test_pago_tardio_sobre_item_expirado_sin_cupo_marca_pagado_sin_cupo(mock_ses, modulo):
    """Fix 1: mismo caso pero Cal.com ya no puede confirmar (4xx) -> pagado_sin_cupo."""
    modulo.TABLE.put_item(Item={
        "booking_uid": "abc123", "estado": "expirado", "email": "a@b.com", "wompi_reference": "abc123",
        "creado_en": int(time.time()), "expira_en": int(time.time()) + 7200,
    })
    import calcom
    with patch(
        "pagos_confirmar.calcom.confirmar_reserva",
        side_effect=calcom.CalComError("409: ya no existe", status=409),
    ):
        resultado = modulo.handler(_evento_wompi(), None)

    assert resultado["statusCode"] == 200
    item = modulo.TABLE.get_item(Key={"booking_uid": "abc123"}).get("Item")
    assert item["estado"] == "pagado_sin_cupo"
    mock_ses.send_email.assert_called_once()


@pytest.mark.parametrize("status_transitorio", [500, 502, None])
def test_calcom_error_transitorio_no_cambia_estado_y_responde_500(status_transitorio, modulo):
    """Fix 4: una falla transitoria de Cal.com (5xx o sin respuesta HTTP) no debe
    marcar pagado_sin_cupo permanentemente; Wompi debe reintentar el webhook."""
    modulo.TABLE.put_item(Item={
        "booking_uid": "abc123", "estado": "pendiente", "wompi_reference": "abc123",
        "creado_en": int(time.time()), "expira_en": int(time.time()) + 7200,
    })
    import calcom
    with patch(
        "pagos_confirmar.calcom.confirmar_reserva",
        side_effect=calcom.CalComError("falla transitoria", status=status_transitorio),
    ):
        resultado = modulo.handler(_evento_wompi(), None)

    assert resultado["statusCode"] == 500
    item = modulo.TABLE.get_item(Key={"booking_uid": "abc123"}).get("Item")
    assert item["estado"] == "pendiente"  # sin cambios
    assert "expira_en" in item


@patch("pagos_confirmar.calcom.confirmar_reserva")
def test_signature_ausente_no_crashea(mock_confirmar, modulo):
    body = {"data": {"transaction": {"id": "tx-1", "status": "APPROVED", "reference": "abc123"}}}
    evento = {"requestContext": {"http": {"method": "POST"}}, "body": json.dumps(body)}

    resultado = modulo.handler(evento, None)

    assert resultado["statusCode"] == 400
    mock_confirmar.assert_not_called()


@patch("pagos_confirmar.calcom.confirmar_reserva")
def test_data_no_es_diccionario_no_crashea(mock_confirmar, modulo):
    body = {"data": "no-es-un-dict", "signature": {
        "properties": ["transaction.id"], "checksum": "x", "timestamp": 1530291411,
    }}
    evento = {"requestContext": {"http": {"method": "POST"}}, "body": json.dumps(body)}

    resultado = modulo.handler(evento, None)

    assert resultado["statusCode"] == 400
    mock_confirmar.assert_not_called()


@patch("pagos_confirmar.calcom.confirmar_reserva")
def test_body_no_es_json_no_crashea(mock_confirmar, modulo):
    evento = {"requestContext": {"http": {"method": "POST"}}, "body": "esto no es json"}

    resultado = modulo.handler(evento, None)

    assert resultado["statusCode"] == 400
    mock_confirmar.assert_not_called()


@patch("pagos_confirmar.calcom.confirmar_reserva")
def test_metodo_distinto_de_post_no_crashea(mock_confirmar, modulo):
    evento = {"requestContext": {"http": {"method": "GET"}}}

    resultado = modulo.handler(evento, None)

    assert resultado["statusCode"] == 405
    mock_confirmar.assert_not_called()


@patch("pagos_confirmar.calcom.confirmar_reserva")
def test_evento_sin_request_context_no_crashea(mock_confirmar, modulo):
    resultado = modulo.handler({}, None)
    assert resultado["statusCode"] == 405
    mock_confirmar.assert_not_called()
