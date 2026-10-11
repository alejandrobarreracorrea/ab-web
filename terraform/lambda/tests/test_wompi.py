import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import wompi


def test_firmar_integridad_calcula_sha256_esperado():
    firma = wompi.firmar_integridad("abc123", 3200000, "COP", "secreto")
    esperado = hashlib.sha256(b"abc1233200000COPsecreto").hexdigest()
    assert firma == esperado


def test_validar_checksum_acepta_firma_correcta():
    secret = "prod_events_test"
    timestamp = 1530291411
    data = {"transaction": {"id": "1234-1610641025-49201", "status": "APPROVED", "amount_in_cents": 44900}}
    crudo = "1234-1610641025-49201APPROVED44900" + str(timestamp) + secret
    checksum = hashlib.sha256(crudo.encode()).hexdigest()
    evento = {
        "data": data,
        "signature": {
            "properties": ["transaction.id", "transaction.status", "transaction.amount_in_cents"],
            "checksum": checksum,
            "timestamp": timestamp,
        },
    }
    assert wompi.validar_checksum(evento, secret) is True


def test_validar_checksum_rechaza_firma_incorrecta():
    evento = {
        "data": {"transaction": {"id": "x", "status": "APPROVED", "amount_in_cents": 1}},
        "signature": {
            "properties": ["transaction.id", "transaction.status", "transaction.amount_in_cents"],
            "checksum": "0" * 64,
            "timestamp": 123,
        },
    }
    assert wompi.validar_checksum(evento, "secreto") is False


def test_validar_checksum_rechaza_payload_incompleto():
    assert wompi.validar_checksum({}, "secreto") is False
    assert wompi.validar_checksum({"signature": {}}, "secreto") is False


def test_validar_checksum_rechaza_propiedad_faltante_en_data():
    evento = {
        "data": {"transaction": {"id": "x"}},  # falta "status"
        "signature": {
            "properties": ["transaction.id", "transaction.status"],
            "checksum": "0" * 64,
            "timestamp": 123,
        },
    }
    assert wompi.validar_checksum(evento, "secreto") is False


def test_validar_checksum_rechaza_evento_none():
    assert wompi.validar_checksum(None, "secreto") is False


def test_validar_checksum_rechaza_evento_no_dict():
    assert wompi.validar_checksum([], "secreto") is False


def test_validar_checksum_rechaza_signature_no_dict():
    evento = {"signature": "bad string", "data": {}}
    assert wompi.validar_checksum(evento, "secreto") is False
