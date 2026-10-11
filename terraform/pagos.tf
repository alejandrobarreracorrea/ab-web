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

# Rotacion de credenciales en produccion: se hace actualizando los GitHub
# Secrets (WOMPI_PUBLIC_KEY, WOMPI_PRIVATE_KEY, WOMPI_INTEGRITY_SECRET,
# WOMPI_EVENTS_SECRET, CALCOM_API_KEY, CALCOM_WEBHOOK_SECRET) y re-corriendo el
# pipeline — nunca editando este parametro SSM a mano desde la consola de AWS,
# porque el siguiente "terraform apply" lo revertiria silenciosamente al valor
# que tengan los GitHub Secrets en ese momento.
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

# ---------------------------------------------------------------------------
# Lambda pagos-iniciar: webhook BOOKING_REQUESTED de Cal.com + GET de solo
# lectura para que /mentoria/pagar/ recupere los datos del checkout.
# ---------------------------------------------------------------------------

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
      TABLE_NAME           = aws_dynamodb_table.pagos.name
      CREDENCIALES_SSM     = aws_ssm_parameter.pagos_credenciales.name
      CREDENCIALES_VERSION = aws_ssm_parameter.pagos_credenciales.version
      SENDER               = "Alejandro Barrera · IAOps <hola@${var.domain_name}>"
      PRECIO_USD           = tostring(var.mentoria_precio_usd)
      PAGINA_PAGO_URL      = "https://iaopslatam.com/mentoria/pagar/"
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

# ---------------------------------------------------------------------------
# Lambda pagos-confirmar: webhook transaction.updated de Wompi; confirma la
# reserva en Cal.com y marca el item pagado en DynamoDB.
# ---------------------------------------------------------------------------

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
      TABLE_NAME           = aws_dynamodb_table.pagos.name
      CREDENCIALES_SSM     = aws_ssm_parameter.pagos_credenciales.name
      CREDENCIALES_VERSION = aws_ssm_parameter.pagos_credenciales.version
      SENDER               = "Alejandro Barrera · IAOps <hola@${var.domain_name}>"
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

# ---------------------------------------------------------------------------
# Lambda pagos-liberar: cron EventBridge (cada 15 min) que declina en
# Cal.com las reservas pendientes con mas de 2 horas sin pagarse y las
# marca expiradas, liberando el cupo.
# ---------------------------------------------------------------------------

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
      TABLE_NAME           = aws_dynamodb_table.pagos.name
      CREDENCIALES_SSM     = aws_ssm_parameter.pagos_credenciales.name
      CREDENCIALES_VERSION = aws_ssm_parameter.pagos_credenciales.version
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
