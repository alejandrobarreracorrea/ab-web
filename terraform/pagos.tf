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
