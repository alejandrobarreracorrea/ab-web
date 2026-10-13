# =====================================================================
#  Tu página web en S3 con HTTPS · Esumer · Cloud Computing, Redes y Ciberseguridad
#  Bucket S3 PRIVADO + CloudFront (certificado HTTPS) + Origin Access Control.
#  Nadie entra directo al bucket: solo CloudFront puede leerlo.
#
#  Uso (en la terminal de VS Code, carpeta con main.tf e index.html):
#    terraform init
#    terraform apply      (CloudFront tarda 3-5 minutos en quedar listo)
#    terraform destroy    (al final; borrar CloudFront también tarda unos minutos)
# =====================================================================

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

provider "aws" {
  region = "us-east-1"
}

# ---------- 1 · El bucket: la bodega de objetos (nombre único en el mundo)
resource "aws_s3_bucket" "web" {
  bucket_prefix = "mi-web-" # AWS le agrega un sufijo para que sea único
  force_destroy = true      # permite borrarlo aunque tenga archivos (solo para el lab)
  tags          = { Name = "mi-web-s3" }
}

# ---------- 2 · Candado: bloqueo de acceso público (el bucket queda privado)
resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------- 3 · El objeto: tu página (sale del archivo index.html de esta carpeta)
resource "aws_s3_object" "index" {
  bucket       = aws_s3_bucket.web.id
  key          = "index.html"
  source       = "index.html"
  etag         = filemd5("index.html") # si cambias el archivo, Terraform lo vuelve a subir
  content_type = "text/html; charset=utf-8"
}

# ---------- 4 · La credencial de CloudFront para leer el bucket (OAC)
resource "aws_cloudfront_origin_access_control" "oac" {
  name                              = "oac-${aws_s3_bucket.web.id}"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# ---------- 5 · CloudFront: la vitrina con candado (HTTPS + certificado)
resource "aws_cloudfront_distribution" "cdn" {
  enabled             = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  comment             = "Mi web en S3 con HTTPS"

  origin {
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_id                = "s3-mi-web"
    origin_access_control_id = aws_cloudfront_origin_access_control.oac.id
  }

  default_cache_behavior {
    target_origin_id       = "s3-mi-web"
    viewer_protocol_policy = "redirect-to-https" # http -> https siempre
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # CachingDisabled: ves tus cambios al instante (en producción se usa caché)
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true # certificado HTTPS de *.cloudfront.net
  }
}

# ---------- 6 · Permiso: SOLO esta distribución de CloudFront puede leer el bucket
data "aws_iam_policy_document" "solo_cloudfront" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.web.arn}/*"]
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

resource "aws_s3_bucket_policy" "web" {
  bucket     = aws_s3_bucket.web.id
  policy     = data.aws_iam_policy_document.solo_cloudfront.json
  depends_on = [aws_s3_bucket_public_access_block.web]
}

# ---------- 7 · Lo que Terraform te muestra al terminar
output "tu_web_https" {
  value = "https://${aws_cloudfront_distribution.cdn.domain_name}"
}

output "directo_al_bucket" {
  value       = "https://${aws_s3_bucket.web.bucket_regional_domain_name}/index.html"
  description = "Ábrelo: debe dar AccessDenied (403). El bucket es privado."
}
