"""Consulta la TRM oficial (Superintendencia Financiera, dataset 32sa-8pi3 de
datos.gov.co, sin autenticacion). Devuelve None si falla, para que el llamador
decida usar un valor cacheado.
"""
import json
import urllib.error
import urllib.request

TRM_URL = (
    "https://www.datos.gov.co/resource/32sa-8pi3.json"
    "?$order=vigenciadesde%20DESC&$limit=1"
)


def obtener_trm_actual() -> float | None:
    try:
        with urllib.request.urlopen(TRM_URL, timeout=8) as resp:
            datos = json.loads(resp.read())
    except (OSError, urllib.error.URLError, ValueError):
        return None

    if not datos:
        return None
    try:
        valor = float(datos[0]["valor"])
    except (KeyError, ValueError, TypeError):
        return None
    return valor if valor > 0 else None
