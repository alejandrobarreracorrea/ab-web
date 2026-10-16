# =====================================================================
#  La tienda serverless de Laura · Esumer · Cloud Computing, Redes y Ciberseguridad
#
#  FRONT  ../front  → S3 (privado) + CloudFront (HTTPS)
#  BACK   ../back   → API Gateway → Lambda (Python) → DynamoDB
#
#  Uso (terminal de VS Code, DENTRO de esta carpeta terraform):
#    terraform init
#    terraform apply      (unos 4 minutos: CloudFront es el que más tarda)
#    terraform destroy    (al final de la clase)
# =====================================================================

terraform {
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 6.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.0" }
  }
}

provider "aws" {
  region = "us-east-1"
}

locals {
  front = "${path.module}/../front"
  back  = "${path.module}/../back"
  tipos = {
    html = "text/html; charset=utf-8"
    css  = "text/css; charset=utf-8"
    js   = "application/javascript; charset=utf-8"
    svg  = "image/svg+xml"
    png  = "image/png"
    jpg  = "image/jpeg"
  }
}

# ======================= DATOS ========================================
# 1 · La tabla DynamoDB (llave de partición + llave de orden)
resource "aws_dynamodb_table" "pedidos" {
  name         = "tienda-pedidos"
  billing_mode = "PAY_PER_REQUEST" # serverless: pagas por petición
  hash_key     = "cliente"         # llave de partición
  range_key    = "fecha"           # llave de orden

  attribute {
    name = "cliente"
    type = "S"
  }
  attribute {
    name = "fecha"
    type = "S"
  }
}

# ======================= BACK =========================================
# 2 · El permiso de la Lambda (rol): solo guardar y leer ESTA tabla
resource "aws_iam_role" "lambda" {
  name = "tienda-serverless-lambda"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "tabla" {
  name = "solo-tabla-pedidos"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["dynamodb:PutItem", "dynamodb:Query", "dynamodb:Scan"], Resource = aws_dynamodb_table.pedidos.arn }]
  })
}

resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole" # escribir en CloudWatch Logs
}

# 3 · El cerebro: la Lambda (sale de ../back/app.py)
data "archive_file" "back" {
  type        = "zip"
  source_file = "${local.back}/app.py"
  output_path = "${path.module}/back.zip"
}

resource "aws_lambda_function" "back" {
  function_name    = "tienda-serverless"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "app.handler" # archivo app.py → función handler
  memory_size      = 128
  timeout          = 10
  filename         = data.archive_file.back.output_path
  source_code_hash = data.archive_file.back.output_base64sha256
  environment {
    variables = { TABLA = aws_dynamodb_table.pedidos.name }
  }
}

# 4 · La puerta: API Gateway (HTTP API)
resource "aws_apigatewayv2_api" "api" {
  name          = "tienda-serverless-api"
  protocol_type = "HTTP"
  target        = aws_lambda_function.back.arn
  cors_configuration {
    allow_origins = ["https://${aws_cloudfront_distribution.cdn.domain_name}"] # solo la página de Laura
    allow_methods = ["GET", "POST"]
    allow_headers = ["content-type"]
  }
}

resource "aws_lambda_permission" "api" {
  statement_id  = "api-gateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.back.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*"
}

# ======================= FRONT ========================================
# 5 · El bucket S3 PRIVADO con la página
resource "aws_s3_bucket" "front" {
  bucket_prefix = "tienda-laura-"
  force_destroy = true # permite borrarlo aunque tenga archivos (solo para el lab)
}

resource "aws_s3_bucket_public_access_block" "front" {
  bucket                  = aws_s3_bucket.front.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# sube TODOS los archivos de ../front (index.html, estilos.css, app.js…)
resource "aws_s3_object" "front" {
  for_each     = fileset(local.front, "**")
  bucket       = aws_s3_bucket.front.id
  key          = each.value
  source       = "${local.front}/${each.value}"
  etag         = filemd5("${local.front}/${each.value}")
  content_type = lookup(local.tipos, reverse(split(".", each.value))[0], "application/octet-stream")
}

# config.js: le dice al front cuál es la dirección de TU API
resource "aws_s3_object" "config" {
  bucket       = aws_s3_bucket.front.id
  key          = "config.js"
  content      = "window.API_URL = \"${aws_apigatewayv2_api.api.api_endpoint}\";\n"
  content_type = "application/javascript; charset=utf-8"
}

# 6 · CloudFront: HTTPS al mundo; solo él puede leer el bucket (OAC)
resource "aws_cloudfront_origin_access_control" "oac" {
  name                              = "oac-${aws_s3_bucket.front.id}"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "cdn" {
  enabled             = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  comment             = "Tienda serverless de Laura"

  origin {
    domain_name              = aws_s3_bucket.front.bucket_regional_domain_name
    origin_id                = "s3-tienda"
    origin_access_control_id = aws_cloudfront_origin_access_control.oac.id
  }

  default_cache_behavior {
    target_origin_id       = "s3-tienda"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # CachingDisabled: ves tus cambios al instante
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

data "aws_iam_policy_document" "solo_cloudfront" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.front.arn}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.cdn.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "front" {
  bucket     = aws_s3_bucket.front.id
  policy     = data.aws_iam_policy_document.solo_cloudfront.json
  depends_on = [aws_s3_bucket_public_access_block.front]
}

# ======================= LO QUE TE MUESTRA AL TERMINAR ================
output "tu_tienda" {
  value = "https://${aws_cloudfront_distribution.cdn.domain_name}"
}

output "tu_api" {
  value = aws_apigatewayv2_api.api.api_endpoint
}
