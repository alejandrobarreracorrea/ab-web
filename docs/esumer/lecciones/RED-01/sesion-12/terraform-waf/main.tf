# =====================================================================
#  Rojo vs. azul: tu web protegida con WAF · Esumer · Cloud Computing, Redes y Ciberseguridad
#  Bucket S3 PRIVADO + CloudFront (HTTPS) + AWS WAF con reglas administradas por AWS.
#  Solo se prueba contra TU propia web, en TU propia cuenta.
#
#  Uso (terminal de VS Code, carpeta con main.tf e index.html):
#    terraform init
#    terraform apply                         (modo "observar": el WAF cuenta, no bloquea)
#    terraform apply -var="modo=bloquear"    (ahora el WAF bloquea con 403)
#    terraform apply -var-file="bloqueo.tfvars"   (lista negra: te bloqueas a ti)
#    terraform destroy                       (al final: el WAF cobra por hora)
# =====================================================================

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

provider "aws" {
  region = "us-east-1" # el WAF de CloudFront siempre vive en us-east-1
}

# ---------- 0 · El interruptor del equipo azul
variable "modo" {
  type        = string
  default     = "observar"
  description = "observar = el WAF solo cuenta los ataques · bloquear = los frena con 403"
  validation {
    condition     = contains(["observar", "bloquear"], var.modo)
    error_message = "modo debe ser observar o bloquear."
  }
}

# ---------- 1 · El bucket: la bodega de objetos (nombre único en el mundo)
resource "aws_s3_bucket" "web" {
  bucket_prefix = "tienda-waf-" # AWS le agrega un sufijo para que sea único
  force_destroy = true      # permite borrarlo aunque tenga archivos (solo para el lab)
  tags          = { Name = "tienda-waf" }
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


# ---------- La lista negra: IPs que el equipo azul decide bloquear
variable "ips_bloqueadas" {
  type        = list(string)
  default     = []
  description = "IPs con /32, por ejemplo [\"181.50.10.20/32\"]"
}

resource "aws_wafv2_ip_set" "lista_negra" {
  name               = "lista-negra"
  scope              = "CLOUDFRONT"
  ip_address_version = "IPV4"
  addresses          = var.ips_bloqueadas
}

# ---------- 5 · El escudo: AWS WAF con reglas que AWS mantiene al día
resource "aws_wafv2_web_acl" "escudo" {
  name  = "escudo-tienda"
  scope = "CLOUDFRONT"
  default_action {
    allow {}
  }

  # Regla 0: la lista negra de IPs (siempre bloquea)
  rule {
    name     = "lista-negra"
    priority = 0
    action {
      block {}
    }
    statement {
      ip_set_reference_statement {
        arn = aws_wafv2_ip_set.lista_negra.arn
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "lista-negra"
      sampled_requests_enabled   = true
    }
  }

  # Regla 1: ataques comunes (XSS, rutas raras, tamaños sospechosos)
  rule {
    name     = "ataques-comunes"
    priority = 1
    override_action {
      dynamic "count" {
        for_each = var.modo == "observar" ? [1] : []
        content {}
      }
      dynamic "none" {
        for_each = var.modo == "bloquear" ? [1] : []
        content {}
      }
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesCommonRuleSet"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "ataques-comunes"
      sampled_requests_enabled   = true
    }
  }

  # Regla 2: inyección SQL
  rule {
    name     = "inyeccion-sql"
    priority = 2
    override_action {
      dynamic "count" {
        for_each = var.modo == "observar" ? [1] : []
        content {}
      }
      dynamic "none" {
        for_each = var.modo == "bloquear" ? [1] : []
        content {}
      }
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesSQLiRuleSet"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "inyeccion-sql"
      sampled_requests_enabled   = true
    }
  }

  # Regla 3: demasiadas peticiones desde una misma IP (frena bots y fuerza bruta)
  rule {
    name     = "limite-por-ip"
    priority = 3
    action {
      dynamic "count" {
        for_each = var.modo == "observar" ? [1] : []
        content {}
      }
      dynamic "block" {
        for_each = var.modo == "bloquear" ? [1] : []
        content {}
      }
    }
    statement {
      rate_based_statement {
        limit                 = 100 # peticiones por IP
        evaluation_window_sec = 60  # en 1 minuto
        aggregate_key_type    = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "limite-por-ip"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "escudo-tienda"
    sampled_requests_enabled   = true
  }
}

# ---------- 6 · CloudFront: la vitrina con candado (HTTPS + certificado) y el escudo puesto
resource "aws_cloudfront_distribution" "cdn" {
  enabled             = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  comment             = "Mi web en S3 con HTTPS y WAF"
  web_acl_id          = aws_wafv2_web_acl.escudo.arn # el escudo va delante de la web

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

# ---------- 7 · Permiso: SOLO esta distribución de CloudFront puede leer el bucket
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

# ---------- 8 · Lo que Terraform te muestra al terminar
output "tu_web_https" {
  value = "https://${aws_cloudfront_distribution.cdn.domain_name}"
}

output "directo_al_bucket" {
  value       = "https://${aws_s3_bucket.web.bucket_regional_domain_name}/index.html"
  description = "Ábrelo: debe dar AccessDenied (403). El bucket es privado."
}

output "modo_waf" {
  value = var.modo
}
