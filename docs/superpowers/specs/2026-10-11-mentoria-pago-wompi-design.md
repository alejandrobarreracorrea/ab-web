# Pago de la mentoría 1:1 con Wompi, agendamiento en Cal.com — diseño

**Doc:** 2026-10-11 · componente nuevo `ab-pagos-*`, mismo patrón que `ab-leads-*`
(Terraform + Lambda + DynamoDB, pipeline OIDC existente de `ab-web`).

## 1. Problema

Hoy `/mentoria` enlaza directo a `cal.com/alejandrobarrera/mentoria-1-1` (**USD 20**,
confirmado en vivo vía API — no USD 80 como se asumió en la primera versión de este
documento) y el pago ocurre dentro del flujo nativo de Cal.com con **PayPal**
(`paymentOption: "ON_BOOKING"`, cobro al momento de reservar). En la práctica eso
excluye a quien no tiene PayPal/tarjeta internacional — una fracción importante de la
audiencia colombiana/LATAM del programa.

**Objetivo:** seguir agendando el 100% en Cal.com (calendario, disponibilidad, Zoom/Meet,
reprogramaciones sin tocar) pero mover el **cobro** a Wompi (PSE, Nequi, tarjetas,
Bancolombia), de forma transparente — en la propia marca `iaopslatam.com`, sin saltar a
un checkout ajeno.

## 2. Decisiones ya tomadas (brainstorming 2026-10-11)

| Decisión | Valor |
|---|---|
| Cuenta Wompi | Producción lista (llaves `pub_prod_`/`prv_prod_` + Integrity Secret ya emitidas) |
| Nivel de automatización | Totalmente automatizado (sin intervención manual por reserva) |
| Precio | USD 20 (confirmado en vivo); el cobro real en Wompi se calcula en COP al momento de la reserva |
| Fuente de la TRM | Automática, API oficial `datos.gov.co` (dataset `32sa-8pi3`, Superintendencia Financiera), sin autenticación |
| Arquitectura | Opción A: tres Lambdas pequeñas y enfocadas, componente aislado de `ab-leads` |

## 2.1 Validación en vivo contra la cuenta real de Cal.com (2026-10-11)

Antes de seguir, se probó cada supuesto del diseño contra la cuenta real de Cal.com
(`app.cal.com`, plan **Individual/gratis**), vía UI y vía API v2 (`PATCH
/v2/event-types/6948287`, revertido inmediatamente después de cada prueba; la API key
de prueba se revocó al terminar). Resultados:

| Supuesto | UI (plan gratis) | API v2 | Conclusión |
|---|---|---|---|
| `confirmationPolicy` (requires confirmation) | No existe el toggle en ninguna de las 12 pestañas del event type | **Acepta `{"type":"always","blockUnconfirmedBookingsInBooker":true}` → HTTP 200** | **Sí es posible, gratis — pero solo configurable vía API, no desde el dashboard** |
| `successRedirectUrl` (redirect on booking) | Bloqueado con candado "Actualizar a Equipos" (~USD 12/mes/usuario) | **Rechazado: HTTP 403 `forbidden_error`, "Redirect on booking is a feature for team plan users"** | **No es posible sin plan Teams — bloqueo real de servidor, no solo de UI** |
| Apps de pago nativas disponibles | Cal Pay, PayPal, Stripe, BTCPayServer, HitPay | — | Wompi no es una de ellas; confirma que no hay integración nativa posible |
| Webhooks y sus triggers | Disponibles sin restricción de plan | — | `BOOKING_CREATED`, `BOOKING_REQUESTED`, `BOOKING_PAYMENT_INITIATED`, `BOOKING_PAID`, etc. todos disponibles |

**Consecuencia para el diseño:** se mantiene el flujo original "pending → confirmar tras
pago" (§5), activando `confirmationPolicy` por API en vez de por el dashboard. Pero como
`successRedirectUrl` sí está bloqueado de verdad, **no hay redirect del navegador** hacia
`/mentoria/pagar/`: en su lugar, `pagos-iniciar` envía el link de pago **por correo**
(SES) apenas crea el ítem `pendiente` — ver §5, paso 4.

## 3. Arquitectura y componentes

Todo vive en el repo `ab-web` (no es un repo nuevo), prefijo de recursos `ab-pagos-*`,
mismo patrón que `terraform/leads.tf`:

```
terraform/pagos.tf                  # recursos nuevos (este documento)
terraform/lambda/pagos_iniciar.py   # handler 1
terraform/lambda/pagos_confirmar.py # handler 2
terraform/lambda/pagos_liberar.py   # handler 3
terraform/lambda/wompi.py           # helper compartido: firma, llamadas API Wompi
terraform/lambda/calcom.py          # helper compartido: confirm/decline vía API Cal.com
site-iaops/mentoria/pagar/index.html # página del widget de pago
```

### 3.1 Lambda `pagos-iniciar`
- Trigger: Function URL (auth `NONE`, valida la firma propia del webhook de Cal.com en
  el handler — ver §6).
- Recibe el webhook `BOOKING_REQUESTED` de Cal.com.
- Responsabilidades: leer credenciales de SSM, obtener la TRM del día, calcular el monto
  en COP, construir la firma de integridad de Wompi, guardar el ítem `pendiente` en
  DynamoDB.
- Expone también una ruta de **solo lectura** (mismo Function URL, método GET) para que
  la página `/mentoria/pagar/` recupere `{monto_cop, reference, signature, public_key}`
  dado un `booking_uid` — así la firma de integridad nunca se calcula ni se expone en el
  navegador.

### 3.2 Página `site-iaops/mentoria/pagar/index.html`
- Vanilla HTML/JS (sin build), mismo estilo del resto de `site-iaops`.
- Lee `?uid=<booking_uid>` de la URL, hace GET a `pagos-iniciar`, monta el Widget de Wompi
  con los datos recibidos.
- Si el `booking_uid` no existe o ya fue resuelto (`pagado`/`expirado`), muestra el
  estado correspondiente en vez del widget (evita reintentos de pago sobre algo ya
  cerrado).

### 3.3 Lambda `pagos-confirmar`
- Trigger: Function URL (auth `NONE`, valida `X-Event-Checksum` de Wompi en el handler).
- Recibe el webhook `transaction.updated` de Wompi.
- Si `status = APPROVED` y la `reference` coincide con un ítem `pendiente`: llama
  `POST /v2/bookings/{uid}/confirm` en Cal.com y marca el ítem `pagado`.
- Idempotente: si el ítem ya está `pagado`, responde 200 sin volver a llamar a Cal.com
  (Wompi reintenta webhooks hasta recibir 200).

### 3.4 Lambda `pagos-liberar`
- Trigger: EventBridge Schedule, cada 15 minutos (invocación directa, no Function URL).
- Escanea DynamoDB por ítems `pendiente` con más de 2 horas desde `creado_en`.
- Llama `POST /v2/bookings/{uid}/decline` con `{"reason": "Pago no completado"}`,
  libera el cupo, marca el ítem `expirado`.

### 3.5 DynamoDB `ab-pagos-${environment}`
- PK: `booking_uid` (string).
- Atributos: `estado` (`pendiente` | `pagado` | `expirado` | `pagado_sin_cupo`), `email`,
  `monto_cop`, `monto_usd`, `wompi_reference`, `wompi_transaction_id`, `creado_en`
  (epoch), `expira_en` (epoch, atributo de TTL de DynamoDB).
- Ítem especial `trm_cache` (PK fija `"trm_cache"`): guarda la última TRM obtenida con
  éxito (`valor`, `fecha`), para cuando `datos.gov.co` no responda.
- `point_in_time_recovery` habilitado, igual que `ab-leads`.

### 3.6 IAM
- Un solo rol compartido `ab-pagos-exec-${environment}` para las tres Lambdas (mismo
  criterio que `ab-leads`, que usa un rol único para su Lambda): las tres operan sobre la
  misma tabla y el mismo secret, así que separar roles no reduce superficie de ataque
  real, solo añade recursos Terraform.
- Permisos: `dynamodb:PutItem/GetItem/UpdateItem/Scan` solo sobre `ab-pagos-${environment}`;
  `ssm:GetParameter` + `kms:Decrypt` solo sobre el parámetro de credenciales (§4);
  `logs:*` acotado a `/aws/lambda/ab-pagos-*`.

## 4. Credenciales (el secret)

Primera vez que el pipeline de `ab-web` inyecta credenciales de un tercero — hoy
`deploy.yml` no usa ningún GitHub Secret (todo lo sensible, como `session_secret` de
`ab-leads`, lo genera Terraform con `random_password`).

**Flujo:**

1. **GitHub Secrets** del repo (nuevos, a crear antes de implementar):
   `WOMPI_PUBLIC_KEY`, `WOMPI_PRIVATE_KEY`, `WOMPI_INTEGRITY_SECRET`,
   `WOMPI_EVENTS_SECRET`, `CALCOM_API_KEY`, `CALCOM_WEBHOOK_SECRET`.
2. El job `infra` de `deploy.yml` los pasa como `TF_VAR_*` (variables Terraform marcadas
   `sensitive = true`) al paso `terraform apply`, igual que ya pasa `TF_VAR_environment`.
3. Terraform los escribe en **un solo parámetro** SSM Parameter Store
   `/ab-pagos/${environment}/credenciales` (tipo `SecureString`, cifrado con la KMS key
   por defecto de SSM), como un único valor JSON:
   ```json
   {
     "wompi_public_key": "...",
     "wompi_private_key": "...",
     "wompi_integrity_secret": "...",
     "wompi_events_secret": "...",
     "calcom_api_key": "...",
     "calcom_webhook_secret": "..."
   }
   ```
4. Cada Lambda lo lee una vez por cold start (`ssm:GetParameter` con `WithDecryption`) y
   lo cachea en memoria del contexto de ejecución — no se vuelve a pedir en invocaciones
   calientes.

**Por qué un solo parámetro JSON y no seis sueltos:** una sola llamada a SSM por cold
start en vez de seis, y un solo recurso IAM que autorizar. Es el mismo criterio que ya
usa el proyecto hermano (`STAFF_BOOTSTRAP_PASSWORD` en Essionix Atlas es un secret único
por servicio).

**Nunca en el navegador:** la firma de integridad de Wompi (`wompi_integrity_secret`) y
la validación del webhook (`wompi_events_secret`, `calcom_webhook_secret`) se usan
exclusivamente server-side, dentro de las Lambdas. El frontend solo recibe
`wompi_public_key`, el monto y la firma ya calculada — nunca los secretos.

## 5. Flujo end-to-end

1. El event type "mentoria-1-1" en Cal.com pasa a modo **"Requires confirmation"**,
   configurado por **llamada a la API** (`PATCH /v2/event-types/6948287` con
   `confirmationPolicy: {"type":"always","blockUnconfirmedBookingsInBooker":true}`) —
   confirmado que esto funciona en el plan gratis (§2.1); no existe en el dashboard, así
   que debe hacerse así y documentarse, no buscarse en la UI.
2. Alguien reserva un horario → Cal.com crea la reserva en estado `pending` y dispara el
   webhook `BOOKING_REQUESTED` hacia `pagos-iniciar`.
3. `pagos-iniciar`: lee el secret, consulta la TRM del día en `datos.gov.co` (o usa
   `trm_cache` si falla), calcula `monto_cop = 20 × TRM` redondeado, arma la firma de
   integridad de Wompi, guarda el ítem `pendiente` en DynamoDB (`expira_en = ahora + 2h`),
   **envía por correo (SES) al email del reservante el link
   `iaopslatam.com/mentoria/pagar/?uid=<booking_uid>`** (mismo mecanismo que ya usa
   `ab-leads` para la verificación), y responde 200 a Cal.com.
   `successRedirectUrl` **no se usa**: confirmado en vivo que ese campo está bloqueado
   por el servidor para cuentas fuera del plan Teams (§2.1), así que no hay redirect del
   navegador — todo pasa por el correo.
4. El visitante abre el correo y entra a `/mentoria/pagar/?uid=<booking_uid>`.
5. La página hace GET a `pagos-iniciar` con el `uid` de la reserva, recibe los datos del
   checkout y monta el Widget de Wompi. El visitante paga con PSE, Nequi, tarjeta o
   Bancolombia (según lo que tengas habilitado en el comercio).
6. Wompi procesa la transacción y dispara el webhook `transaction.updated` hacia
   `pagos-confirmar`.
7. `pagos-confirmar` valida `X-Event-Checksum`; si `status = APPROVED` y la `reference`
   coincide con un ítem `pendiente`, llama `POST /v2/bookings/{uid}/confirm` en Cal.com
   (header `cal-api-version`, `Authorization: Bearer <calcom_api_key>`) y marca el ítem
   `pagado`. Cal.com confirma la reserva, crea el Meet/Zoom y notifica como siempre.
8. Si nadie paga en 2 horas, `pagos-liberar` declina la reserva vía Cal.com y libera el
   cupo.

## 6. Manejo de errores y edge cases

- **Webhook de Cal.com sin firma válida** (secret configurado al crear el webhook en el
  dashboard de Cal.com): `pagos-iniciar` responde 401, no crea nada en DynamoDB.
- **Webhook de Wompi con checksum inválido**: `pagos-confirmar` responde 400, no confirma
  nada en Cal.com. Evita que alguien falsifique un "pago aprobado".
- **Idempotencia**: Wompi reintenta webhooks hasta recibir 200. Un segundo aviso sobre un
  ítem ya `pagado` responde 200 sin volver a llamar a Cal.com.
- **Pago tardío** (el ítem ya está `expirado` cuando llega la aprobación de Wompi):
  `pagos-confirmar` intenta igual `POST /confirm`; si Cal.com lo rechaza porque el slot
  ya no está disponible, el ítem se marca `pagado_sin_cupo` para resolución manual
  (reagendar o reembolsar desde el dashboard de Wompi). No se automatiza el reembolso —
  se espera que sean casos raros.
- **Caída de `datos.gov.co`**: se usa el valor de `trm_cache` (última TRM exitosa) en vez
  de bloquear la reserva. Si tampoco hay `trm_cache` (primera ejecución sin ningún éxito
  previo), `pagos-iniciar` responde error 500 y la reserva queda sin cobro iniciado —
  caso que solo puede ocurrir antes del primer booking real tras el deploy.

## 7. Testing

- **Unitario**: cada handler recibe sus dependencias (cliente DynamoDB, cliente HTTP,
  credenciales) de forma inyectable; pruebas con `moto` (mock de DynamoDB/SSM) sin tocar
  AWS real. Cubrir: firma válida/inválida, checksum válido/inválido, idempotencia del
  webhook de pago, cálculo de TRM con y sin `trm_cache`.
- **Integración manual en sandbox de Wompi**: mismas rutas de API, llaves `pub_test_` /
  `prv_test_` en vez de `_prod_`. Permite probar el flujo completo (firma, widget,
  webhook) sin mover dinero real, cambiando solo el secret antes del go-live.
- **End-to-end manual pre-lanzamiento**: reservar con el widget en modo test, confirmar
  que el booking pasa a `accepted` solo tras el webhook de pago (nunca antes), y verificar
  que `pagos-liberar` libera un pendiente simulando `creado_en` viejo.

## 8. Cambios operativos fuera de Terraform

- Cal.com: activar `confirmationPolicy` en el event type "mentoria-1-1" vía API
  (`PATCH /v2/event-types/6948287`, ver §5 paso 1 — no está disponible en el dashboard
  de este plan) + desactivar la app de pago nativa (PayPal) del evento, ya que el pago
  ahora lo gestiona Wompi fuera de Cal.com + webhook `BOOKING_REQUESTED` apuntando a la
  Function URL de `pagos-iniciar` con su secret configurado.
- Wompi: webhook `transaction.updated` apuntando a la Function URL de `pagos-confirmar`.
- Seis GitHub Secrets nuevos en el repo (§4), creados antes del primer `terraform apply`
  de este componente.
- Actualizar el copy de `/mentoria` (hoy dice "Reservas y pagas en Cal.com") para reflejar
  que el pago es con Wompi y que el link de pago llega por correo tras reservar.

## 9. Fuera de alcance (este diseño)

- Reembolsos automáticos.
- Cualquier producto del catálogo de pago que no sea la mentoría 1:1 (el mismo riel se
  podría reutilizar después, pero no se diseña aquí).
- Facturación electrónica DIAN (si aplica más adelante, es un componente aparte).
