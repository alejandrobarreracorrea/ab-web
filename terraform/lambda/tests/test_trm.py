import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import trm


@patch("trm.urllib.request.urlopen")
def test_obtener_trm_actual_devuelve_valor_mas_reciente(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(
        [{"valor": "4123.45", "vigenciadesde": "2026-10-11T00:00:00.000"}]
    ).encode()
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    assert trm.obtener_trm_actual() == 4123.45


@patch("trm.urllib.request.urlopen")
def test_obtener_trm_actual_devuelve_none_si_falla_la_red(mock_urlopen):
    mock_urlopen.side_effect = OSError("timeout")
    assert trm.obtener_trm_actual() is None


@patch("trm.urllib.request.urlopen")
def test_obtener_trm_actual_devuelve_none_si_respuesta_vacia(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = b"[]"
    mock_urlopen.return_value.__enter__.return_value = mock_resp
    assert trm.obtener_trm_actual() is None


@patch("trm.urllib.request.urlopen")
def test_obtener_trm_actual_devuelve_none_si_el_valor_no_es_numerico(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps([{"valor": "no-es-un-numero"}]).encode()
    mock_urlopen.return_value.__enter__.return_value = mock_resp
    assert trm.obtener_trm_actual() is None


@patch("trm.urllib.request.urlopen")
def test_obtener_trm_actual_devuelve_none_si_el_valor_es_cero_o_negativo(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps([{"valor": "0"}]).encode()
    mock_urlopen.return_value.__enter__.return_value = mock_resp
    assert trm.obtener_trm_actual() is None

    mock_resp.read.return_value = json.dumps([{"valor": "-100.5"}]).encode()
    assert trm.obtener_trm_actual() is None
