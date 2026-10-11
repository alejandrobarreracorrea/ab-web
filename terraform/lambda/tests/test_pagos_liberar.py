import importlib
import json
import os
import sys
import time
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("TABLE_NAME", "ab-pagos-test")
os.environ.setdefault("CREDENCIALES_SSM", "/ab-pagos/test/credenciales")

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
            Value=json.dumps({"calcom_api_key": "cal_live_x"}),
        )
        yield


@pytest.fixture
def modulo(entorno_aws):
    import pagos_liberar as m
    importlib.reload(m)
    return m


def _item(uid, estado, hace_segundos):
    now = int(time.time())
    return {
        "booking_uid": uid, "estado": estado, "wompi_reference": uid,
        "creado_en": now - hace_segundos, "expira_en": now - hace_segundos + 7200,
    }


@patch("pagos_liberar.calcom.declinar_reserva")
def test_libera_pendientes_vencidos(mock_declinar, modulo):
    modulo.TABLE.put_item(Item=_item("viejo", "pendiente", hace_segundos=3 * 3600))

    resultado = modulo.handler({}, None)

    assert resultado["liberados"] == 1
    mock_declinar.assert_called_once_with("viejo", "cal_live_x", "Pago no completado")
    item = modulo.TABLE.get_item(Key={"booking_uid": "viejo"}).get("Item")
    assert item["estado"] == "expirado"
    assert "expira_en" not in item  # Fix 2: TTL removido al salir de "pendiente"


@patch("pagos_liberar.calcom.declinar_reserva")
def test_no_toca_pendientes_recientes(mock_declinar, modulo):
    modulo.TABLE.put_item(Item=_item("reciente", "pendiente", hace_segundos=600))

    resultado = modulo.handler({}, None)

    assert resultado["liberados"] == 0
    mock_declinar.assert_not_called()
    item = modulo.TABLE.get_item(Key={"booking_uid": "reciente"}).get("Item")
    assert item["estado"] == "pendiente"


@patch("pagos_liberar.calcom.declinar_reserva")
def test_no_toca_items_ya_pagados(mock_declinar, modulo):
    modulo.TABLE.put_item(Item=_item("pagado", "pagado", hace_segundos=3 * 3600))

    resultado = modulo.handler({}, None)

    assert resultado["liberados"] == 0
    mock_declinar.assert_not_called()


@patch("pagos_liberar.calcom.declinar_reserva")
def test_no_toca_items_pagados_sin_cupo(mock_declinar, modulo):
    modulo.TABLE.put_item(Item=_item("sin_cupo", "pagado_sin_cupo", hace_segundos=3 * 3600))

    resultado = modulo.handler({}, None)

    assert resultado["liberados"] == 0
    mock_declinar.assert_not_called()
    item = modulo.TABLE.get_item(Key={"booking_uid": "sin_cupo"}).get("Item")
    assert item["estado"] == "pagado_sin_cupo"


@patch("pagos_liberar.calcom.declinar_reserva")
def test_item_pagado_entre_el_scan_y_el_declinar_no_se_marca_expirado(mock_declinar, modulo):
    modulo.TABLE.put_item(Item=_item("carrera", "pendiente", hace_segundos=3 * 3600))

    def _pagar_durante_el_decline(*_args, **_kwargs):
        modulo.TABLE.update_item(
            Key={"booking_uid": "carrera"},
            UpdateExpression="SET estado = :e",
            ExpressionAttributeValues={":e": "pagado"},
        )

    mock_declinar.side_effect = _pagar_durante_el_decline

    resultado = modulo.handler({}, None)

    assert resultado["liberados"] == 0
    item = modulo.TABLE.get_item(Key={"booking_uid": "carrera"}).get("Item")
    assert item["estado"] == "pagado"  # no lo piso con "expirado"


@patch("pagos_liberar.calcom.declinar_reserva", side_effect=Exception("red caida"))
def test_un_error_en_una_reserva_no_detiene_las_demas(_mock_declinar, modulo):
    modulo.TABLE.put_item(Item=_item("falla", "pendiente", hace_segundos=3 * 3600))
    modulo.TABLE.put_item(Item=_item("ok", "pendiente", hace_segundos=3 * 3600))

    with patch("pagos_liberar.calcom.declinar_reserva") as mock_declinar:
        import calcom
        def side_effect(uid, *_args, **_kwargs):
            if uid == "falla":
                # Fix 5: status=500 (transitorio) -> se deja pendiente para reintentar.
                raise calcom.CalComError("500", status=500)
        mock_declinar.side_effect = side_effect
        resultado = modulo.handler({}, None)

    assert resultado["liberados"] == 1
    assert modulo.TABLE.get_item(Key={"booking_uid": "ok"}).get("Item")["estado"] == "expirado"
    assert modulo.TABLE.get_item(Key={"booking_uid": "falla"}).get("Item")["estado"] == "pendiente"


@patch("pagos_liberar.calcom.declinar_reserva")
def test_error_4xx_al_declinar_marca_expirado_y_cuenta(mock_declinar, modulo):
    """Fix 5: si Cal.com responde 4xx (la reserva ya no existe/cambio de su lado),
    no hay nada que reintentar: se libera el item de todos modos."""
    import calcom
    modulo.TABLE.put_item(Item=_item("ya-no-existe", "pendiente", hace_segundos=3 * 3600))
    mock_declinar.side_effect = calcom.CalComError("404: not found", status=404)

    resultado = modulo.handler({}, None)

    assert resultado["liberados"] == 1
    item = modulo.TABLE.get_item(Key={"booking_uid": "ya-no-existe"}).get("Item")
    assert item["estado"] == "expirado"
    assert "expira_en" not in item


@patch("pagos_liberar.calcom.declinar_reserva")
def test_error_5xx_al_declinar_deja_pendiente_y_no_cuenta(mock_declinar, modulo):
    """Fix 5: un 5xx (o sin status, URLError) es transitorio: se deja pendiente
    para que el proximo ciclo del cron lo reintente."""
    import calcom
    modulo.TABLE.put_item(Item=_item("falla-500", "pendiente", hace_segundos=3 * 3600))
    mock_declinar.side_effect = calcom.CalComError("502: bad gateway", status=502)

    resultado = modulo.handler({}, None)

    assert resultado["liberados"] == 0
    item = modulo.TABLE.get_item(Key={"booking_uid": "falla-500"}).get("Item")
    assert item["estado"] == "pendiente"
    assert "expira_en" in item
