"""Firma de integridad para el checkout de Wompi y validación del checksum
de los webhooks entrantes (eventos). Documentación: docs.wompi.co/en/docs/colombia/.
"""
import hashlib
import hmac


def firmar_integridad(reference: str, amount_in_cents: int, currency: str, secret: str) -> str:
    crudo = f"{reference}{amount_in_cents}{currency}{secret}"
    return hashlib.sha256(crudo.encode()).hexdigest()


def validar_checksum(evento: dict, events_secret: str) -> bool:
    if not isinstance(evento, dict):
        return False
    if not isinstance(events_secret, str) or not events_secret:
        return False

    firma = evento.get("signature") or {}
    if not isinstance(firma, dict):
        return False

    properties = firma.get("properties") or []
    checksum_esperado = (firma.get("checksum") or "").lower()
    # El timestamp va en la raiz del evento (hermano de "signature"), no
    # anidado dentro de "signature". Ver docs.wompi.co/en/docs/colombia/eventos/.
    timestamp = evento.get("timestamp")
    if not properties or not checksum_esperado or timestamp is None:
        return False

    data = evento.get("data") or {}
    valores = []
    for path in properties:
        valor = data
        for clave in path.split("."):
            if not isinstance(valor, dict) or clave not in valor:
                return False
            valor = valor[clave]
        valores.append(str(valor))

    crudo = "".join(valores) + str(timestamp) + events_secret
    calculado = hashlib.sha256(crudo.encode()).hexdigest()
    return hmac.compare_digest(calculado, checksum_esperado)
