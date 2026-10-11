# Pago de la mentoría 1:1 con Wompi — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar el cobro nativo de PayPal en Cal.com por un cobro con Wompi (PSE,
Nequi, tarjeta, Bancolombia), manteniendo el 100% del agendamiento (calendario,
disponibilidad, Meet) en Cal.com.

**Architecture:** Nuevo componente serverless aislado `ab-pagos-*` (3 Lambdas Python
3.12 + 1 tabla DynamoDB + 1 secret SSM), mismo patrón que `ab-leads`. La reserva en
Cal.com queda `pending` (`confirmationPolicy`, activado por API) hasta que un webhook
de Wompi confirma el pago; el link de pago se manda por correo (SES) porque el redirect
nativo de Cal.com está bloqueado para el plan actual.

**Tech Stack:** Terraform (AWS: Lambda, DynamoDB, SSM Parameter Store, EventBridge,
SES), Python 3.12 (stdlib + boto3, sin dependencias de terceros en runtime — Lambda sin
layers), pytest + moto para tests, HTML/JS vanilla para la página de pago.

**Spec:** `docs/superpowers/specs/2026-10-11-mentoria-pago-wompi-design.md`

## Global Constraints

- Prefijo de todos los recursos nuevos: `ab-pagos-*` (igual criterio que `ab-leads-*`).
- Runtime Python 3.12, sin librerías de terceros en runtime (usar `urllib.request` de
  la stdlib para HTTP saliente — el repo no tiene Lambda Layers configurados).
- Un solo rol IAM compartido `ab-pagos-exec-${environment}` para las tres Lambdas.
- Credenciales en un único parámetro SSM SecureString
  `/ab-pagos/${environment}/credenciales` (JSON); nunca se exponen al navegador.
- `confirmationPolicy` del event type "mentoria-1-1" (id `6948287`) se activa por
  `PATCH /v2/event-types/6948287` — no existe en el dashboard del plan actual de Cal.com.
- `successRedirectUrl` **no se usa** (bloqueado server-side para el plan actual); el
  link de pago se envía por correo vía SES.
- `wompi_reference` **es** el `booking_uid` de Cal.com — sin tabla de mapeo adicional.
- Precio de la mentoría: **USD 80** (variable Terraform `mentoria_precio_usd`, default
  `80`; el valor configurado en Cal.com hoy es 20, solo de prueba — no se lee de ahí).
- Ventana de pago: 2 horas desde `creado_en` antes de liberar el cupo.
- Todo el desarrollo y las pruebas corren contra el **sandbox de Wompi** (cuenta
  `IAOPSLATAM` en revisión); el go-live a producción es solo cambiar el valor del
  secret SSM cuando Wompi apruebe la cuenta — sin tocar código ni Terraform.
- Handlers idempotentes frente a reintentos de webhook (Wompi reintenta hasta HTTP 200).

## Review Focus

- Webhook `BOOKING_REQUESTED` duplicado para la misma reserva (reintento de red de
  Cal.com) no debe reenviar el correo de pago ni pisar un ítem ya `pagado`/`expirado`
  con uno `pendiente` nuevo.
- Webhook de Wompi con un `reference` que no existe en DynamoDB (booking borrado, dato
  corrupto, intento de falsificación) no debe lanzar una excepción no controlada — debe
  responder 200 e ignorar.
- `pagos-liberar` no debe declinar una reserva que se pagó justo entre el `scan` y el
  `decline` (condición de carrera entre el cron y el webhook de pago).
- El email del asistente que llega en el payload de Cal.com puede traer mayúsculas o
  espacios — debe normalizarse antes de usarse como destinatario en SES.
- La TRM devuelta por `datos.gov.co` puede venir corrupta (0, negativa o no numérica) —
  no debe generar un cobro de $0 o inválido en Wompi.

---

## Estructura de archivos

```
terraform/pagos.tf                       # recursos AWS de este componente (nuevo)
terraform/lambda/wompi.py                # firma de integridad + validación de checksum (nuevo)
terraform/lambda/calcom.py               # confirm/decline + validación de firma webhook (nuevo)
terraform/lambda/trm.py                  # consulta TRM oficial (nuevo)
terraform/lambda/pagos_iniciar.py        # handler: webhook Cal.com + GET de checkout (nuevo)
terraform/lambda/pagos_confirmar.py      # handler: webhook Wompi (nuevo)
terraform/lambda/pagos_liberar.py        # handler: cron de limpieza (nuevo)
terraform/lambda/requirements-dev.txt    # pytest, moto, boto3 (nuevo, solo dev)
terraform/lambda/tests/test_wompi.py     # (nuevo)
terraform/lambda/tests/test_calcom.py    # (nuevo)
terraform/lambda/tests/test_trm.py       # (nuevo)
terraform/lambda/tests/test_pagos_iniciar.py    # (nuevo)
terraform/lambda/tests/test_pagos_confirmar.py  # (nuevo)
terraform/lambda/tests/test_pagos_liberar.py    # (nuevo)
site-iaops/mentoria/pagar/index.html     # página del widget de pago (nuevo)
site-iaops/mentoria/index.html           # actualizar copy (modificar)
.github/workflows/deploy.yml             # nuevos TF_VAR_*, placeholder, pytest en CI (modificar)
```

---

### Task 1: Helper `wompi.py` — firma de integridad y validación de checksum

**Files:**
- Create: `terraform/lambda/wompi.py`
- Create: `terraform/lambda/requirements-dev.txt`
- Test: `terraform/lambda/tests/test_wompi.py`

**Interfaces:**
- Produces: `wompi.firmar_integridad(reference: str, amount_in_cents: int, currency: str, secret: str) -> str`
- Produces: `wompi.validar_checksum(evento: dict, events_secret: str) -> bool`

- [ ] **Step 1: Crear el entorno de desarrollo Python**

```bash
mkdir -p terraform/lambda/tests
cd terraform/lambda
python3 -m venv .venv
source .venv/bin/activate
```

- [ ] **Step 2: Escribir `requirements-dev.txt`**

```
boto3>=1.34
pytest>=8.0
moto>=5.0
```

Instalar:
```bash
pip install -r requirements-dev.txt
```

- [ ] **Step 3: Escribir el test que falla**

`terraform/lambda/tests/test_wompi.py`:
```python
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
```

- [ ] **Step 4: Ejecutar y verificar que falla**

```bash
cd terraform/lambda
pytest tests/test_wompi.py -v
```
Expected: `ModuleNotFoundError: No module named 'wompi'`

- [ ] **Step 5: Implementar `wompi.py`**

```python
"""Firma de integridad para el checkout de Wompi y validación del checksum
de los webhooks entrantes (eventos). Documentación: docs.wompi.co/en/docs/colombia/.
"""
import hashlib
import hmac


def firmar_integridad(reference: str, amount_in_cents: int, currency: str, secret: str) -> str:
    crudo = f"{reference}{amount_in_cents}{currency}{secret}"
    return hashlib.sha256(crudo.encode()).hexdigest()


def validar_checksum(evento: dict, events_secret: str) -> bool:
    firma = evento.get("signature") or {}
    properties = firma.get("properties") or []
    checksum_esperado = (firma.get("checksum") or "").lower()
    timestamp = firma.get("timestamp")
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
```

- [ ] **Step 6: Ejecutar y verificar que pasa**

```bash
pytest tests/test_wompi.py -v
```
Expected: 5 passed

- [ ] **Step 7: Commit**

```bash
cd /Users/alejandro/Essionix/ab-web
git add terraform/lambda/wompi.py terraform/lambda/requirements-dev.txt terraform/lambda/tests/test_wompi.py
git commit -m "feat(pagos): helper de firma de integridad y checksum de Wompi"
```

---

### Task 2: Helper `calcom.py` — confirmar/declinar reserva y validar firma de webhook

**Files:**
- Create: `terraform/lambda/calcom.py`
- Test: `terraform/lambda/tests/test_calcom.py`

**Interfaces:**
- Consumes: ninguna (solo stdlib)
- Produces: `calcom.CalComError(Exception)`
- Produces: `calcom.validar_firma_webhook(body_raw: bytes, header_signature: str, secret: str) -> bool`
- Produces: `calcom.confirmar_reserva(booking_uid: str, api_key: str) -> dict`
- Produces: `calcom.declinar_reserva(booking_uid: str, api_key: str, motivo: str) -> dict`

- [ ] **Step 1: Escribir el test que falla**

`terraform/lambda/tests/test_calcom.py`:
```python
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
    assert solicitud.get_header("Cal-api-version") == "2024-06-14"


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
```

- [ ] **Step 2: Ejecutar y verificar que falla**

```bash
pytest tests/test_calcom.py -v
```
Expected: `ModuleNotFoundError: No module named 'calcom'`

- [ ] **Step 3: Implementar `calcom.py`**

```python
"""Confirmar/declinar reservas vía API v2 de Cal.com y validar la firma de
sus webhooks salientes (header x-cal-signature-256, HMAC-SHA256 del body raw).
"""
import hashlib
import hmac
import json
import urllib.error
import urllib.request

API_BASE = "https://api.cal.com/v2"
API_VERSION = "2024-06-14"


class CalComError(Exception):
    pass


def validar_firma_webhook(body_raw: bytes, header_signature: str, secret: str) -> bool:
    if not header_signature:
        return False
    calculado = hmac.new(secret.encode(), body_raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(calculado, header_signature.lower())


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
        raise CalComError(f"{exc.code}: {detalle}") from exc
    except urllib.error.URLError as exc:
        raise CalComError(str(exc)) from exc


def confirmar_reserva(booking_uid: str, api_key: str) -> dict:
    return _llamar(f"/bookings/{booking_uid}/confirm", api_key)


def declinar_reserva(booking_uid: str, api_key: str, motivo: str) -> dict:
    return _llamar(f"/bookings/{booking_uid}/decline", api_key, body={"reason": motivo})
```

- [ ] **Step 4: Ejecutar y verificar que pasa**

```bash
pytest tests/test_calcom.py -v
```
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/alejandro/Essionix/ab-web
git add terraform/lambda/calcom.py terraform/lambda/tests/test_calcom.py
git commit -m "feat(pagos): helper de confirmacion/declinacion de reservas en Cal.com"
```

---

### Task 3: Helper `trm.py` — tasa de cambio oficial con manejo de fallo

**Files:**
- Create: `terraform/lambda/trm.py`
- Test: `terraform/lambda/tests/test_trm.py`

**Interfaces:**
- Produces: `trm.obtener_trm_actual() -> float | None`

- [ ] **Step 1: Escribir el test que falla**

`terraform/lambda/tests/test_trm.py`:
```python
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
```

- [ ] **Step 2: Ejecutar y verificar que falla**

```bash
pytest tests/test_trm.py -v
```
Expected: `ModuleNotFoundError: No module named 'trm'`

- [ ] **Step 3: Implementar `trm.py`**

```python
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
```

- [ ] **Step 4: Ejecutar y verificar que pasa**

```bash
pytest tests/test_trm.py -v
```
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/alejandro/Essionix/ab-web
git add terraform/lambda/trm.py terraform/lambda/tests/test_trm.py
git commit -m "feat(pagos): helper de consulta de la TRM oficial"
```

---

### Task 4: Terraform base — DynamoDB, rol IAM, secret SSM, variables sensibles

**Files:**
- Create: `terraform/pagos.tf`
- Modify: `.github/workflows/deploy.yml:~17` (env del workflow, nuevos secrets)

**Interfaces:**
- Produces (Terraform): `aws_dynamodb_table.pagos`, `aws_iam_role.pagos_exec`,
  `aws_ssm_parameter.pagos_credenciales`, `var.mentoria_precio_usd`
- Consumes: `local.tags`, `var.environment`, `var.aws_region`, `var.domain_name`,
  `data.aws_caller_identity.current`, `aws_sesv2_email_identity.site` (de `ses.tf`,
  ya existen en el repo)

- [ ] **Step 1: Crear `terraform/pagos.tf` con la base de datos, el rol y el secret**

```hcl
# ---------------------------------------------------------------------------
# Pago de la mentoria 1:1 con Wompi, agendamiento sin tocar en Cal.com.
# Prefijo de recursos: ab-pagos-*. Ver docs/superpowers/specs/2026-10-11-
# mentoria-pago-wompi-design.md para el diseño completo.
# ---------------------------------------------------------------------------

variable "mentoria_precio_usd" {
  description = "Precio de la mentoria 1:1 en USD, usado para calcular el cobro en COP"
  type        = number
  default     = 80
}

variable "wompi_public_key" {
  type      = string
  sensitive = true
}

variable "wompi_private_key" {
  type      = string
  sensitive = true
}

variable "wompi_integrity_secret" {
  type      = string
  sensitive = true
}

variable "wompi_events_secret" {
  type      = string
  sensitive = true
}

variable "calcom_api_key" {
  type      = string
  sensitive = true
}

variable "calcom_webhook_secret" {
  type      = string
  sensitive = true
}

resource "aws_dynamodb_table" "pagos" {
  name         = "ab-pagos-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "booking_uid"

  attribute {
    name = "booking_uid"
    type = "S"
  }

  ttl {
    attribute_name = "expira_en"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = local.tags
}

resource "aws_ssm_parameter" "pagos_credenciales" {
  name = "/ab-pagos/${var.environment}/credenciales"
  type = "SecureString"
  value = jsonencode({
    wompi_public_key       = var.wompi_public_key
    wompi_private_key      = var.wompi_private_key
    wompi_integrity_secret = var.wompi_integrity_secret
    wompi_events_secret    = var.wompi_events_secret
    calcom_api_key         = var.calcom_api_key
    calcom_webhook_secret  = var.calcom_webhook_secret
  })
  tags = local.tags
}

resource "aws_iam_role" "pagos_exec" {
  name = "ab-pagos-exec-${var.environment}"
  tags = local.tags

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "pagos_exec" {
  name = "ab-pagos-exec-${var.environment}"
  role = aws_iam_role.pagos_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "DatosPagos"
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:Scan"]
        Resource = aws_dynamodb_table.pagos.arn
      },
      {
        Sid      = "LeerCredenciales"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = aws_ssm_parameter.pagos_credenciales.arn
      },
      {
        Sid      = "DescifrarCredenciales"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = "arn:aws:kms:${var.aws_region}:${data.aws_caller_identity.current.account_id}:alias/aws/ssm"
      },
      {
        Sid      = "EnviarCorreoPago"
        Effect   = "Allow"
        Action   = ["ses:SendEmail"]
        Resource = aws_sesv2_email_identity.site.arn
      },
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/ab-pagos-*"
      },
    ]
  })
}
```

- [ ] **Step 2: Validar sintaxis Terraform localmente**

```bash
cd terraform
terraform init -backend=false
terraform validate
```
Expected: `Success! The configuration is valid.`
(Fallará con "Reference to undeclared resource" para las Lambdas de las tareas 5-7 —
 eso es esperado hasta que se agreguen; este validate es solo para confirmar que
 `pagos.tf` en sí mismo no tiene errores de sintaxis. Si falla por otra razón, revisar
 antes de seguir.)

- [ ] **Step 3: Declarar los 6 nuevos GitHub Secrets en el repo (acción manual, fuera de código)**

En GitHub → Settings → Secrets and variables → Actions → New repository secret, crear
(con los valores reales de `comercios.wompi.co` → Desarrollo → Programadores, y de
Cal.com → Settings → Developer → API Keys para `CALCOM_API_KEY`;
`CALCOM_WEBHOOK_SECRET` se define en el paso 8 de la Task 5, al crear el webhook):

- `WOMPI_PUBLIC_KEY`
- `WOMPI_PRIVATE_KEY`
- `WOMPI_INTEGRITY_SECRET`
- `WOMPI_EVENTS_SECRET`
- `CALCOM_API_KEY`
- `CALCOM_WEBHOOK_SECRET`

- [ ] **Step 4: Inyectar los secrets como `TF_VAR_*` en el pipeline**

Modificar `.github/workflows/deploy.yml`, en el step `Terraform apply` del job `infra`
(agregar al bloque `env:` existente, junto a `TF_VAR_environment`):

```yaml
      - name: Terraform apply
        working-directory: ${{ env.TF_WORKING_DIR }}
        env:
          TF_VAR_environment: ${{ env.ENVIRONMENT }}
          TF_VAR_enable_custom_domain: ${{ env.ENABLE_CUSTOM_DOMAIN }}
          TF_VAR_enable_iaopslatam: ${{ env.ENABLE_IAOPSLATAM }}
          TF_VAR_enable_aisre_redirect: ${{ env.ENABLE_AISRE_REDIRECT }}
          TF_VAR_wompi_public_key: ${{ secrets.WOMPI_PUBLIC_KEY }}
          TF_VAR_wompi_private_key: ${{ secrets.WOMPI_PRIVATE_KEY }}
          TF_VAR_wompi_integrity_secret: ${{ secrets.WOMPI_INTEGRITY_SECRET }}
          TF_VAR_wompi_events_secret: ${{ secrets.WOMPI_EVENTS_SECRET }}
          TF_VAR_calcom_api_key: ${{ secrets.CALCOM_API_KEY }}
          TF_VAR_calcom_webhook_secret: ${{ secrets.CALCOM_WEBHOOK_SECRET }}
        run: terraform apply -auto-approve -input=false
```

- [ ] **Step 5: Commit**

```bash
cd /Users/alejandro/Essionix/ab-web
git add terraform/pagos.tf .github/workflows/deploy.yml
git commit -m "feat(pagos): base Terraform de ab-pagos (DynamoDB, rol IAM, secret SSM)"
```

---

### Task 5: Lambda `pagos-iniciar` — webhook Cal.com + checkout de lectura

**Files:**
- Create: `terraform/lambda/pagos_iniciar.py`
- Modify: `terraform/pagos.tf` (agregar recursos de esta Lambda)
- Test: `terraform/lambda/tests/test_pagos_iniciar.py`

**Interfaces:**
- Consumes: `wompi.firmar_integridad`, `calcom.validar_firma_webhook`, `trm.obtener_trm_actual`
- Produces: `pagos_iniciar.handler(event, context) -> dict` (formato Lambda Function URL)
- Produces (DynamoDB, consumido por Task 6 y 7): ítem con claves
  `booking_uid, estado, email, monto_cop, monto_usd, wompi_reference, wompi_signature,
  creado_en, expira_en`

- [ ] **Step 1: Escribir el test que falla**

`terraform/lambda/tests/test_pagos_iniciar.py`:
```python
import hashlib
import hmac
import importlib
import json
import os
import sys
import time
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
        "monto_cop": 320000, "monto_usd": 80.0, "wompi_reference": "abc123",
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
    modulo.TABLE.put_item(Item={"booking_uid": modulo.TRM_CACHE_KEY, "valor": 4500.0, "fecha": int(time.time())})

    resultado = modulo.handler(_evento_webhook(uid="concache"), None)

    assert resultado["statusCode"] == 200
    item = modulo.TABLE.get_item(Key={"booking_uid": "concache"}).get("Item")
    assert item["monto_cop"] == 360000  # 80 * 4500


def test_get_devuelve_datos_de_checkout_para_reserva_pendiente(modulo):
    modulo.TABLE.put_item(Item={
        "booking_uid": "abc123", "estado": "pendiente", "email": "a@b.com",
        "monto_cop": 320000, "monto_usd": 80.0, "wompi_reference": "abc123",
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
```

- [ ] **Step 2: Ejecutar y verificar que falla**

```bash
cd terraform/lambda
pytest tests/test_pagos_iniciar.py -v
```
Expected: `ModuleNotFoundError: No module named 'pagos_iniciar'`

- [ ] **Step 3: Implementar `pagos_iniciar.py`**

```python
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
        TABLE.put_item(Item={"booking_uid": TRM_CACHE_KEY, "valor": valor, "fecha": now})
        return valor
    cache = TABLE.get_item(Key={"booking_uid": TRM_CACHE_KEY}).get("Item")
    return cache["valor"] if cache else None


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

    if data.get("triggerEvent") != "BOOKING_REQUESTED":
        return _resp(200, {"ok": True, "ignorado": True})

    payload = data.get("payload") or {}
    booking_uid = payload.get("uid")
    attendees = payload.get("attendees") or []
    email = (attendees[0].get("email") if attendees else None)
    if not booking_uid or not email:
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
                "monto_usd": PRECIO_USD,
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
    method = (event.get("requestContext", {}).get("http", {}) or {}).get("method", "")
    if method == "POST":
        return _manejar_webhook(event, _credenciales())
    if method == "GET":
        return _manejar_consulta(event)
    return _resp(405, {"ok": False, "error": "method"})
```

- [ ] **Step 4: Ejecutar y verificar que pasa**

```bash
pytest tests/test_pagos_iniciar.py -v
```
Expected: 9 passed

- [ ] **Step 5: Agregar los recursos Terraform de esta Lambda a `pagos.tf`**

Agregar al final de `terraform/pagos.tf`:

```hcl
data "archive_file" "pagos_iniciar" {
  type        = "zip"
  output_path = "${path.module}/build/pagos_iniciar.zip"

  source {
    content  = file("${path.module}/lambda/pagos_iniciar.py")
    filename = "pagos_iniciar.py"
  }
  source {
    content  = file("${path.module}/lambda/wompi.py")
    filename = "wompi.py"
  }
  source {
    content  = file("${path.module}/lambda/calcom.py")
    filename = "calcom.py"
  }
  source {
    content  = file("${path.module}/lambda/trm.py")
    filename = "trm.py"
  }
}

resource "aws_lambda_function" "pagos_iniciar" {
  function_name    = "ab-pagos-iniciar-${var.environment}"
  role             = aws_iam_role.pagos_exec.arn
  runtime          = "python3.12"
  handler          = "pagos_iniciar.handler"
  filename         = data.archive_file.pagos_iniciar.output_path
  source_code_hash = data.archive_file.pagos_iniciar.output_base64sha256
  timeout          = 15
  memory_size      = 128
  tags             = local.tags

  environment {
    variables = {
      TABLE_NAME        = aws_dynamodb_table.pagos.name
      CREDENCIALES_SSM  = aws_ssm_parameter.pagos_credenciales.name
      SENDER            = "Alejandro Barrera · IAOps <hola@${var.domain_name}>"
      PRECIO_USD        = tostring(var.mentoria_precio_usd)
      PAGINA_PAGO_URL   = "https://iaopslatam.com/mentoria/pagar/"
    }
  }
}

resource "aws_lambda_function_url" "pagos_iniciar" {
  function_name      = aws_lambda_function.pagos_iniciar.function_name
  authorization_type = "NONE"

  cors {
    allow_origins = ["https://iaopslatam.com", "https://www.iaopslatam.com"]
    allow_methods = ["GET", "POST"]
    allow_headers = ["content-type"]
    max_age       = 86400
  }
}

resource "aws_lambda_permission" "pagos_iniciar_url" {
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.pagos_iniciar.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "pagos_iniciar_url_invoke" {
  statement_id  = "AllowPublicFunctionUrlInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pagos_iniciar.function_name
  principal     = "*"
}

output "pagos_iniciar_url" {
  description = "Function URL de pagos-iniciar (webhook Cal.com + GET de checkout)"
  value       = aws_lambda_function_url.pagos_iniciar.function_url
}
```

- [ ] **Step 6: Validar Terraform**

```bash
cd terraform
terraform init -backend=false
terraform validate
```
Expected: `Success! The configuration is valid.`

- [ ] **Step 7: Commit**

```bash
cd /Users/alejandro/Essionix/ab-web
git add terraform/lambda/pagos_iniciar.py terraform/lambda/tests/test_pagos_iniciar.py terraform/pagos.tf
git commit -m "feat(pagos): Lambda pagos-iniciar (webhook Cal.com + checkout de lectura)"
```

- [ ] **Step 8: (acción manual, no código) Crear el webhook en Cal.com**

Después del primer `terraform apply` de esta tarea (necesitas la Function URL del
output `pagos_iniciar_url`):

1. `app.cal.com` → evento "Mentoria 1:1" → pestaña Webhooks → Nuevo.
2. URL del suscriptor: la Function URL del output.
3. Dejar solo el trigger `Reserva solicitada` (`BOOKING_REQUESTED`) marcado (quitar los
   demás con la "×" de cada chip).
4. En "Secreto", generar un valor aleatorio largo y guardarlo como el GitHub Secret
   `CALCOM_WEBHOOK_SECRET` (Task 4, Step 3) — debe coincidir exactamente con el que
   quede en el secret SSM tras el siguiente `terraform apply`.

---

### Task 6: Lambda `pagos-confirmar` — webhook de Wompi

**Files:**
- Create: `terraform/lambda/pagos_confirmar.py`
- Modify: `terraform/pagos.tf` (agregar recursos de esta Lambda)
- Test: `terraform/lambda/tests/test_pagos_confirmar.py`

**Interfaces:**
- Consumes: `wompi.validar_checksum`, `calcom.confirmar_reserva`, `calcom.CalComError`
- Consumes (DynamoDB, de Task 5): ítem con `estado`, `wompi_reference`
- Produces: `pagos_confirmar.handler(event, context) -> dict`

- [ ] **Step 1: Escribir el test que falla**

`terraform/lambda/tests/test_pagos_confirmar.py`:
```python
import hashlib
import importlib
import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

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
            "timestamp": timestamp,
        },
    }
    return {"requestContext": {"http": {"method": "POST"}}, "body": json.dumps(body)}


@patch("pagos_confirmar.calcom.confirmar_reserva")
def test_pago_aprobado_confirma_la_reserva_y_marca_pagado(mock_confirmar, modulo):
    modulo.TABLE.put_item(Item={
        "booking_uid": "abc123", "estado": "pendiente", "email": "a@b.com",
        "monto_cop": 320000, "monto_usd": 80.0, "wompi_reference": "abc123",
        "creado_en": int(time.time()), "expira_en": int(time.time()) + 7200,
    })

    resultado = modulo.handler(_evento_wompi(), None)

    assert resultado["statusCode"] == 200
    mock_confirmar.assert_called_once_with("abc123", "cal_live_x")
    item = modulo.TABLE.get_item(Key={"booking_uid": "abc123"}).get("Item")
    assert item["estado"] == "pagado"
    assert item["wompi_transaction_id"] == "tx-1"


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


def test_pago_aprobado_pero_reserva_sin_cupo_marca_pagado_sin_cupo(modulo):
    modulo.TABLE.put_item(Item={
        "booking_uid": "abc123", "estado": "pendiente", "wompi_reference": "abc123",
        "creado_en": int(time.time()), "expira_en": int(time.time()) + 7200,
    })
    import calcom
    with patch("pagos_confirmar.calcom.confirmar_reserva", side_effect=calcom.CalComError("403: no cupo")):
        resultado = modulo.handler(_evento_wompi(), None)

    assert resultado["statusCode"] == 200
    item = modulo.TABLE.get_item(Key={"booking_uid": "abc123"}).get("Item")
    assert item["estado"] == "pagado_sin_cupo"
```

- [ ] **Step 2: Ejecutar y verificar que falla**

```bash
pytest tests/test_pagos_confirmar.py -v
```
Expected: `ModuleNotFoundError: No module named 'pagos_confirmar'`

- [ ] **Step 3: Implementar `pagos_confirmar.py`**

```python
"""ab-pagos-confirmar: recibe el webhook transaction.updated de Wompi, valida
el checksum y confirma la reserva en Cal.com cuando el pago fue aprobado.
"""
import json
import os

import boto3

import calcom
import wompi

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
SSM = boto3.client("ssm")
CREDENCIALES_SSM = os.environ["CREDENCIALES_SSM"]

_creds_cache = None


def _credenciales():
    global _creds_cache
    if _creds_cache is None:
        resp = SSM.get_parameter(Name=CREDENCIALES_SSM, WithDecryption=True)
        _creds_cache = json.loads(resp["Parameter"]["Value"])
    return _creds_cache


def _resp(status, payload):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(payload)}


def handler(event, _context):
    method = (event.get("requestContext", {}).get("http", {}) or {}).get("method", "")
    if method != "POST":
        return _resp(405, {"ok": False, "error": "method"})

    creds = _credenciales()
    try:
        cuerpo = json.loads(event.get("body") or "{}")
    except ValueError:
        return _resp(400, {"ok": False, "error": "json"})

    if not wompi.validar_checksum(cuerpo, creds["wompi_events_secret"]):
        return _resp(400, {"ok": False, "error": "checksum"})

    transaccion = (cuerpo.get("data") or {}).get("transaction") or {}
    estado_pago = transaccion.get("status")
    reference = transaccion.get("reference")
    transaction_id = transaccion.get("id")
    if estado_pago != "APPROVED" or not reference:
        return _resp(200, {"ok": True, "ignorado": True})

    item = TABLE.get_item(Key={"booking_uid": reference}).get("Item")
    if not item:
        return _resp(200, {"ok": True, "ignorado": True})
    if item["estado"] != "pendiente":
        return _resp(200, {"ok": True, "ya_procesado": True})

    try:
        calcom.confirmar_reserva(reference, creds["calcom_api_key"])
    except calcom.CalComError:
        TABLE.update_item(
            Key={"booking_uid": reference},
            UpdateExpression="SET estado = :e, wompi_transaction_id = :tid",
            ExpressionAttributeValues={":e": "pagado_sin_cupo", ":tid": transaction_id},
        )
        return _resp(200, {"ok": True, "pagado_sin_cupo": True})

    TABLE.update_item(
        Key={"booking_uid": reference},
        UpdateExpression="SET estado = :e, wompi_transaction_id = :tid",
        ExpressionAttributeValues={":e": "pagado", ":tid": transaction_id},
    )
    return _resp(200, {"ok": True})
```

- [ ] **Step 4: Ejecutar y verificar que pasa**

```bash
pytest tests/test_pagos_confirmar.py -v
```
Expected: 6 passed

- [ ] **Step 5: Agregar los recursos Terraform de esta Lambda a `pagos.tf`**

```hcl
data "archive_file" "pagos_confirmar" {
  type        = "zip"
  output_path = "${path.module}/build/pagos_confirmar.zip"

  source {
    content  = file("${path.module}/lambda/pagos_confirmar.py")
    filename = "pagos_confirmar.py"
  }
  source {
    content  = file("${path.module}/lambda/wompi.py")
    filename = "wompi.py"
  }
  source {
    content  = file("${path.module}/lambda/calcom.py")
    filename = "calcom.py"
  }
}

resource "aws_lambda_function" "pagos_confirmar" {
  function_name    = "ab-pagos-confirmar-${var.environment}"
  role             = aws_iam_role.pagos_exec.arn
  runtime          = "python3.12"
  handler          = "pagos_confirmar.handler"
  filename         = data.archive_file.pagos_confirmar.output_path
  source_code_hash = data.archive_file.pagos_confirmar.output_base64sha256
  timeout          = 15
  memory_size      = 128
  tags             = local.tags

  environment {
    variables = {
      TABLE_NAME       = aws_dynamodb_table.pagos.name
      CREDENCIALES_SSM = aws_ssm_parameter.pagos_credenciales.name
    }
  }
}

resource "aws_lambda_function_url" "pagos_confirmar" {
  function_name      = aws_lambda_function.pagos_confirmar.function_name
  authorization_type = "NONE"
}

resource "aws_lambda_permission" "pagos_confirmar_url" {
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.pagos_confirmar.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "pagos_confirmar_url_invoke" {
  statement_id  = "AllowPublicFunctionUrlInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pagos_confirmar.function_name
  principal     = "*"
}

output "pagos_confirmar_url" {
  description = "Function URL de pagos-confirmar (webhook Wompi)"
  value       = aws_lambda_function_url.pagos_confirmar.function_url
}
```

- [ ] **Step 6: Validar Terraform**

```bash
cd terraform
terraform init -backend=false
terraform validate
```
Expected: `Success! The configuration is valid.`

- [ ] **Step 7: Commit**

```bash
cd /Users/alejandro/Essionix/ab-web
git add terraform/lambda/pagos_confirmar.py terraform/lambda/tests/test_pagos_confirmar.py terraform/pagos.tf
git commit -m "feat(pagos): Lambda pagos-confirmar (webhook Wompi)"
```

- [ ] **Step 8: (acción manual, no código) Crear el webhook en Wompi**

Después del `terraform apply` de esta tarea (necesitas la Function URL del output
`pagos_confirmar_url`), en `comercios.wompi.co` → Desarrollo → Programadores →
"URL de Eventos": pegar la Function URL y Guardar.

---

### Task 7: Lambda `pagos-liberar` — cron de limpieza

**Files:**
- Create: `terraform/lambda/pagos_liberar.py`
- Modify: `terraform/pagos.tf` (agregar recursos de esta Lambda + EventBridge)
- Test: `terraform/lambda/tests/test_pagos_liberar.py`

**Interfaces:**
- Consumes: `calcom.declinar_reserva`, `calcom.CalComError`
- Consumes (DynamoDB, de Task 5): ítems con `estado = "pendiente"`, `creado_en`
- Produces: `pagos_liberar.handler(event, context) -> dict`

- [ ] **Step 1: Escribir el test que falla**

`terraform/lambda/tests/test_pagos_liberar.py`:
```python
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
                raise calcom.CalComError("500")
        mock_declinar.side_effect = side_effect
        resultado = modulo.handler({}, None)

    assert resultado["liberados"] == 1
    assert modulo.TABLE.get_item(Key={"booking_uid": "ok"}).get("Item")["estado"] == "expirado"
    assert modulo.TABLE.get_item(Key={"booking_uid": "falla"}).get("Item")["estado"] == "pendiente"
```

- [ ] **Step 2: Ejecutar y verificar que falla**

```bash
pytest tests/test_pagos_liberar.py -v
```
Expected: `ModuleNotFoundError: No module named 'pagos_liberar'`

- [ ] **Step 3: Implementar `pagos_liberar.py`**

```python
"""ab-pagos-liberar: cron (EventBridge) que declina en Cal.com las reservas
pendientes de pago con mas de 2 horas sin pagarse, liberando el cupo.
"""
import json
import os
import time

import boto3
from botocore.exceptions import ClientError

import calcom

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
SSM = boto3.client("ssm")
CREDENCIALES_SSM = os.environ["CREDENCIALES_SSM"]

EXPIRA_SEGUNDOS = 2 * 60 * 60


def _credenciales():
    resp = SSM.get_parameter(Name=CREDENCIALES_SSM, WithDecryption=True)
    return json.loads(resp["Parameter"]["Value"])


def handler(_event, _context):
    creds = _credenciales()
    now = int(time.time())
    liberados = 0

    respuesta = TABLE.scan(
        FilterExpression="estado = :p AND creado_en < :limite",
        ExpressionAttributeValues={":p": "pendiente", ":limite": now - EXPIRA_SEGUNDOS},
    )
    for item in respuesta.get("Items", []):
        booking_uid = item["booking_uid"]
        try:
            calcom.declinar_reserva(booking_uid, creds["calcom_api_key"], "Pago no completado")
        except calcom.CalComError:
            continue

        try:
            TABLE.update_item(
                Key={"booking_uid": booking_uid},
                UpdateExpression="SET estado = :e",
                ConditionExpression="estado = :p",
                ExpressionAttributeValues={":e": "expirado", ":p": "pendiente"},
            )
            liberados += 1
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    return {"ok": True, "liberados": liberados}
```

- [ ] **Step 4: Ejecutar y verificar que pasa**

```bash
pytest tests/test_pagos_liberar.py -v
```
Expected: 5 passed

- [ ] **Step 5: Agregar los recursos Terraform de esta Lambda a `pagos.tf`**

```hcl
data "archive_file" "pagos_liberar" {
  type        = "zip"
  output_path = "${path.module}/build/pagos_liberar.zip"

  source {
    content  = file("${path.module}/lambda/pagos_liberar.py")
    filename = "pagos_liberar.py"
  }
  source {
    content  = file("${path.module}/lambda/calcom.py")
    filename = "calcom.py"
  }
}

resource "aws_lambda_function" "pagos_liberar" {
  function_name    = "ab-pagos-liberar-${var.environment}"
  role             = aws_iam_role.pagos_exec.arn
  runtime          = "python3.12"
  handler          = "pagos_liberar.handler"
  filename         = data.archive_file.pagos_liberar.output_path
  source_code_hash = data.archive_file.pagos_liberar.output_base64sha256
  timeout          = 30
  memory_size      = 128
  tags             = local.tags

  environment {
    variables = {
      TABLE_NAME       = aws_dynamodb_table.pagos.name
      CREDENCIALES_SSM = aws_ssm_parameter.pagos_credenciales.name
    }
  }
}

resource "aws_cloudwatch_event_rule" "pagos_liberar" {
  name                = "ab-pagos-liberar-${var.environment}"
  schedule_expression = "rate(15 minutes)"
  tags                = local.tags
}

resource "aws_cloudwatch_event_target" "pagos_liberar" {
  rule = aws_cloudwatch_event_rule.pagos_liberar.name
  arn  = aws_lambda_function.pagos_liberar.arn
}

resource "aws_lambda_permission" "pagos_liberar_schedule" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pagos_liberar.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.pagos_liberar.arn
}
```

- [ ] **Step 6: Validar Terraform**

```bash
cd terraform
terraform init -backend=false
terraform validate
```
Expected: `Success! The configuration is valid.`

- [ ] **Step 7: Commit**

```bash
cd /Users/alejandro/Essionix/ab-web
git add terraform/lambda/pagos_liberar.py terraform/lambda/tests/test_pagos_liberar.py terraform/pagos.tf
git commit -m "feat(pagos): Lambda pagos-liberar (cron de limpieza de pendientes vencidos)"
```

---

### Task 8: Página `site-iaops/mentoria/pagar/` — widget de Wompi

**Files:**
- Create: `site-iaops/mentoria/pagar/index.html`

**Interfaces:**
- Consumes: `GET __PAGOS_ENDPOINT__?uid=<booking_uid>` (de Task 5), respuesta
  `{ok, estado, monto_cop, amount_in_cents, reference, signature, public_key, currency}`

- [ ] **Step 1: Crear la página**

`site-iaops/mentoria/pagar/index.html`:
```html
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Completa tu pago — IAOps LATAM</title>
  <meta name="robots" content="noindex" />
  <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet" />
  <link rel="stylesheet" href="/styles.css" />
</head>
<body>
  <header class="nav">
    <div class="shell nav__inner">
      <a class="nav__brand" href="/">IAOps <b>LATAM</b></a>
    </div>
  </header>
  <main class="shell page">
    <span class="kicker">Mentoría 1:1 · pago</span>
    <h1>Completa tu <span class="grad">pago</span></h1>
    <p class="lead" id="mensaje">Cargando los datos de tu reserva…</p>
    <div id="monto" style="display:none;margin:24px 0;font-family:var(--mono);font-size:1.4rem;"></div>
    <div id="wompi-widget"></div>
  </main>
  <footer class="footer">
    <div class="shell footer__inner">
      <span>© <span id="year">2026</span> IAOps LATAM</span>
    </div>
  </footer>
  <script>
    (function () {
      var ENDPOINT = "__PAGOS_ENDPOINT__";
      var params = new URLSearchParams(window.location.search);
      var uid = params.get("uid");
      var mensaje = document.getElementById("mensaje");
      var montoEl = document.getElementById("monto");
      var widgetEl = document.getElementById("wompi-widget");

      document.getElementById("year").textContent = new Date().getFullYear();

      if (!uid) {
        mensaje.textContent = "Falta el identificador de tu reserva. Revisa el link del correo.";
        return;
      }

      fetch(ENDPOINT + "?uid=" + encodeURIComponent(uid))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data.ok) {
            mensaje.textContent = "No encontramos esa reserva. Si el link es viejo, vuelve a reservar en /mentoria/.";
            return;
          }
          if (data.estado === "pagado") {
            mensaje.textContent = "Tu pago ya está confirmado. Revisa tu correo para el link de la reunión.";
            return;
          }
          if (data.estado === "expirado" || data.estado === "pagado_sin_cupo") {
            mensaje.textContent = "Este cupo ya no está disponible. Vuelve a reservar en /mentoria/.";
            return;
          }

          var cop = (data.monto_cop || 0).toLocaleString("es-CO");
          mensaje.textContent = "Paga con PSE, Nequi, tarjeta o Bancolombia para confirmar tu horario.";
          montoEl.textContent = "$" + cop + " COP";
          montoEl.style.display = "block";

          var script = document.createElement("script");
          script.src = "https://checkout.wompi.co/widget.js";
          script.setAttribute("data-render", "button");
          script.setAttribute("data-public-key", data.public_key);
          script.setAttribute("data-currency", data.currency);
          script.setAttribute("data-amount-in-cents", String(data.amount_in_cents));
          script.setAttribute("data-reference", data.reference);
          script.setAttribute("data-signature:integrity", data.signature);
          widgetEl.appendChild(script);
        })
        .catch(function () {
          mensaje.textContent = "Hubo un problema cargando tu reserva. Intenta de nuevo en un momento.";
        });
    })();
  </script>
</body>
</html>
```

- [ ] **Step 2: Verificar visualmente en local**

```bash
cd site-iaops
python3 -m http.server 8090
```
Abrir `http://localhost:8090/mentoria/pagar/?uid=prueba` en el navegador — debe mostrar
el mensaje de carga y luego (al fallar el fetch contra `__PAGOS_ENDPOINT__`, que aún no
es una URL real) el mensaje de error de "Hubo un problema cargando tu reserva" — eso
confirma que el JS corre sin errores de sintaxis. Revisar la consola del navegador: no
debe haber errores de JS (los de red/fetch fallido son esperados en esta prueba local).

- [ ] **Step 3: Commit**

```bash
cd /Users/alejandro/Essionix/ab-web
git add site-iaops/mentoria/pagar/index.html
git commit -m "feat(pagos): pagina de pago con el widget de Wompi"
```

---

### Task 9: Actualizar el copy de `/mentoria/`

**Files:**
- Modify: `site-iaops/mentoria/index.html:42` y `:55`

**Interfaces:** ninguna (solo contenido estático)

- [ ] **Step 1: Editar el párrafo principal**

En `site-iaops/mentoria/index.html`, línea 42, cambiar:
```html
      <p class="lead">Sesiones de trabajo con Alejandro Barrera (Cloud Architect, 10+ años, 14 certificaciones) sobre TU ruta, TU infraestructura y TUS agentes. Reservas y pagas en Cal.com.</p>
```
por:
```html
      <p class="lead">Sesiones de trabajo con Alejandro Barrera (Cloud Architect, 10+ años, 14 certificaciones) sobre TU ruta, TU infraestructura y TUS agentes. Reserva en Cal.com; te enviamos el link de pago (PSE, Nequi, tarjeta) por correo.</p>
```

- [ ] **Step 2: Editar el pie de la sección final**

Línea 55, cambiar:
```html
      <p class="privacy" style="margin-top:16px;">Pago seguro al reservar · equipos y empresas: <a href="https://alejandrobarrera.net/contacto/" style="color:var(--accent);">escríbenos</a></p>
```
por:
```html
      <p class="privacy" style="margin-top:16px;">Pago seguro con Wompi, por correo tras reservar · equipos y empresas: <a href="https://alejandrobarrera.net/contacto/" style="color:var(--accent);">escríbenos</a></p>
```

- [ ] **Step 3: Verificar visualmente**

```bash
cd site-iaops
python3 -m http.server 8090
```
Abrir `http://localhost:8090/mentoria/` y confirmar que el texto nuevo se ve bien
(sin desbordar el layout, sin romper el acento de color en el link).

- [ ] **Step 4: Commit**

```bash
cd /Users/alejandro/Essionix/ab-web
git add site-iaops/mentoria/index.html
git commit -m "content(mentoria): actualizar copy de pago a Wompi"
```

---

### Task 10: Pipeline — placeholder de endpoint y pytest en CI

**Files:**
- Modify: `.github/workflows/deploy.yml` (job `infra`: outputs + pytest; job `deploy`: placeholder)

**Interfaces:**
- Consumes: outputs Terraform `pagos_iniciar_url` (de Task 5)
- Consumes: `terraform/lambda/tests/` (de Tasks 1-3, 5-7) y `terraform/lambda/requirements-dev.txt` (de Task 1)

- [ ] **Step 1: Agregar el output de `pagos_iniciar_url` al job `infra`**

En `.github/workflows/deploy.yml`, dentro de `jobs.infra.outputs`, agregar:
```yaml
      PAGOS_INICIAR_URL: ${{ steps.tf-output.outputs.pagos_iniciar_url }}
```

Y en el step `Exportar outputs de Terraform`, agregar la línea:
```yaml
          echo "pagos_iniciar_url=$(terraform output -raw pagos_iniciar_url)" >> "$GITHUB_OUTPUT"
```

- [ ] **Step 2: Correr pytest antes de `terraform apply`, en el job `infra`**

Agregar un step nuevo justo antes de `Terraform apply` (después de `Terraform
validate`):
```yaml
      - name: Pruebas unitarias de las Lambdas de pagos
        working-directory: terraform/lambda
        run: |
          python3 -m venv .venv
          .venv/bin/pip install --quiet -r requirements-dev.txt
          .venv/bin/pytest tests/ -v
```

- [ ] **Step 3: Pasar el output al job `deploy` e inyectarlo en el HTML**

En `jobs.deploy.env`, agregar junto a los demás:
```yaml
      PAGOS_INICIAR_URL: ${{ needs.infra.outputs.PAGOS_INICIAR_URL }}
```

En el step `Cache-busting de CSS/JS (versionar por commit)`, agregar al `sed`:
```yaml
      - name: Cache-busting de CSS/JS (versionar por commit)
        run: |
          VER="${GITHUB_SHA::8}"
          find "${SITE_DIR}" site-iaops -name "*.html" -print0 | xargs -0 sed -i \
            -e "s|/styles\.css\"|/styles.css?v=${VER}\"|g" \
            -e "s|/script\.js\"|/script.js?v=${VER}\"|g" \
            -e "s|__REGISTER_ENDPOINT__|${REGISTER_URL}|g" \
            -e "s|__PAGOS_ENDPOINT__|${PAGOS_INICIAR_URL}|g"
          echo "Assets versionados con ?v=${VER} y endpoints inyectados"
```

- [ ] **Step 4: Validar el YAML**

```bash
cd /Users/alejandro/Essionix/ab-web
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/deploy.yml'))" && echo "YAML valido"
```
Expected: `YAML valido`
(Si falla por falta del paquete `yaml`: `pip install --user pyyaml` y reintentar, o
revisar la indentación a mano contra el resto del archivo.)

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "ci(pagos): correr pytest antes de desplegar e inyectar el endpoint de pagos"
```

- [ ] **Step 6: Push y verificar el pipeline en vivo**

```bash
git push
gh run watch
```
Confirmar que el job `infra` pasa (incluye las pruebas unitarias nuevas) y que el job
`deploy` termina exitosamente. Si `pytest` falla en CI pero pasaba en local, revisar
que `requirements-dev.txt` esté commiteado y sin typos.

---

### Task 11: Checklist operativo final (fuera de código — ejecutar en orden)

Esta tarea no tiene tests automatizados porque son pasos de configuración manual en
paneles externos (Cal.com, Wompi) y una prueba end-to-end humana. Ejecutar en este
orden, después de que Tasks 1-10 estén desplegadas en producción (AWS):

- [ ] **Paso 1: Activar `confirmationPolicy` en el event type real**

Con una API key de Cal.com propia (no la de producción del secret SSM — generar una
temporal en Settings → Developer → API Keys, como se hizo durante la validación del
spec, y revocarla al terminar):

```bash
export CAL_KEY='cal_live_...'  # la key temporal que generes
curl -s -X PATCH \
  -H "Authorization: Bearer ${CAL_KEY}" \
  -H "cal-api-version: 2024-06-14" \
  -H "Content-Type: application/json" \
  -d '{"confirmationPolicy": {"type": "always", "blockUnconfirmedBookingsInBooker": true}}' \
  "https://api.cal.com/v2/event-types/6948287"
```
Verificar con un GET al mismo endpoint que `confirmationPolicy` quedó `{"type":
"always", ...}` (no `{"disabled": true}`).

- [ ] **Paso 2: Desactivar el pago nativo de PayPal en el evento**

En `app.cal.com` → evento "Mentoria 1:1" → pestaña "Pagos y asientos" → apagar el
toggle "Reserva de pago" → Guardar. (Esto es en el dashboard — a diferencia de
`confirmationPolicy`, desactivar el pago sí está disponible en la UI del plan actual,
ya validado durante el diseño.)

- [ ] **Paso 3: Actualizar el precio real en Cal.com**

En la misma pestaña, antes de apagar el pago, o en "Configuración del evento" si el
campo sigue editable tras apagarlo: confirmar que el precio quede en USD 80 (hoy dice
20 — es metadata que ya no determina el cobro real una vez Wompi toma el control, pero
debe quedar correcta para que el checkout del propio Cal.com, si algún día se reactiva,
no muestre un valor viejo).

- [ ] **Paso 4: Confirmar que los webhooks de Task 5 y Task 6 ya están creados**

Si no se hicieron en su momento, hacerlos ahora (ver Task 5 Step 8 y Task 6 Step 8).

- [ ] **Paso 5: Revocar la API key temporal de Cal.com usada en el Paso 1**

`app.cal.com` → Settings → Developer → API Keys → eliminar la key temporal.

- [ ] **Paso 6: Prueba end-to-end manual en sandbox**

1. Ir a `https://cal.com/alejandrobarrera/mentoria-1-1` e iniciar una reserva real con
   un correo tuyo.
2. Confirmar que Cal.com muestra "pendiente de confirmación" (no agenda directo).
3. Revisar que llegue el correo de `pagos-iniciar` con el link a `/mentoria/pagar/`.
4. Abrir el link, confirmar que se ve el monto en COP y el botón de Wompi.
5. Pagar con una tarjeta de prueba de Wompi sandbox (ver
   `docs.wompi.co/en/docs/colombia/tarjetas-de-prueba/` para números de prueba).
6. Confirmar que la reserva pasa a `accepted` en Cal.com (revisar en
   `app.cal.com` → Reservas) y que llega la invitación de calendario con el Meet.
7. Revisar en DynamoDB (consola AWS o `aws dynamodb get-item`) que el ítem quedó
   `estado = "pagado"` con `wompi_transaction_id` presente.

- [ ] **Paso 7: Prueba de expiración (opcional pero recomendada)**

Repetir el Paso 6 hasta el punto 3 (sin pagar) y, en DynamoDB, editar manualmente
`creado_en` del ítem a `now - 7300` (más de 2 horas atrás) para no esperar. Invocar la
Lambda `ab-pagos-liberar-pdn` manualmente desde la consola AWS (o
`aws lambda invoke --function-name ab-pagos-liberar-pdn /dev/stdout`) y confirmar que
la reserva en Cal.com pasa a rechazada y el ítem queda `estado = "expirado"`.

- [ ] **Paso 8: Avisar cuando Wompi apruebe la cuenta (seguimiento, no una acción ahora)**

Cuando llegue el aviso de aprobación de Wompi: reemplazar los 4 GitHub Secrets
`WOMPI_*` por los valores `_prod_` reales y volver a correr el pipeline
(`gh workflow run deploy.yml` o un push cualquiera) — es el único cambio necesario
para pasar a cobrar dinero real.
