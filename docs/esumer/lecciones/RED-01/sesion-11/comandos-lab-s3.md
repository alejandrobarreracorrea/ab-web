# LAB S3 · tu web con HTTPS (copiar y pegar en el chat)

_Terraform validado (`terraform validate` + `terraform plan` → **6 to add**), 2026-10-07. Todo en tu PC: VS Code + su terminal (PowerShell)._

**Paso 0 · Permisos para tu llave (consola de AWS)**
IAM → Usuarios → `terraform-lab` → Permisos → Agregar permisos → Adjuntar políticas directamente → `AmazonS3FullAccess` y `CloudFrontFullAccess` → Agregar permisos.

**Paso 1 · S3 con la CLI**
```powershell
$b = "prueba-cli-" + (Get-Random); $b
aws s3 mb "s3://$b"
Set-Content hola.txt "Hola desde la CLI"
aws s3 cp hola.txt "s3://$b/"
aws s3 ls "s3://$b/"
aws s3 presign "s3://$b/hola.txt" --expires-in 300
aws s3 rb "s3://$b" --force
```
> El enlace del `presign` funciona 5 minutos; después dice AccessDenied.

**LAB A · Consola: tu web a clics, con enlace directo (forma 2: bucket público + enlace directo)**
1. Consola → **S3** → **Crear bucket** → nombre `web-laura-TUNOMBRE-123` · región **us-east-1**.
2. En *Configuración de bloqueo de acceso público*: **desmarca “Bloquear todo el acceso público”** → marca “Reconozco…” → **Crear bucket**.
3. Entra al bucket → **Cargar** → **Agregar archivos** → `index.html` → **Cargar**.
4. **Permisos** → **Política del bucket** → **Editar** → pega (cambia el nombre) → **Guardar cambios**:
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "LecturaPublica",
    "Effect": "Allow",
    "Principal": "*",
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::web-laura-TUNOMBRE-123/*"
  }]
}
```
5. Clic en `index.html` → copia la **URL del objeto** → ábrela (🔒 200). Bórrale `/index.html` y recarga → **403 AccessDenied**.
6. **Limpieza:** selecciona el bucket → **Vaciar** (escribe *borrar de forma permanente*) → **Eliminar** (escribe el nombre).

> Por qué “forma 2”: tiene candado, pero el bucket queda público, hay que escribir /index.html y no sirve con dominio propio. La forma correcta es el LAB B (Terraform: CloudFront + bucket privado).

---

**LAB B · Terraform (forma 3)**

**Paso 2 · Tu web en S3 con HTTPS (Terraform)**
```powershell
mkdir $HOME\mi-web-s3
code $HOME\mi-web-s3
```
Crea `main.tf`:
```hcl
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
```
Crea `index.html` (cambia TU NOMBRE):
```html
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mi web en S3</title>
  <style>
    body { margin:0; min-height:100vh; display:grid; place-items:center; font-family:system-ui,sans-serif;
           background:linear-gradient(135deg,#0e2841,#1d4f80); color:#fff; text-align:center; }
    h1 { font-size:2.6rem; margin:0 0 .4rem; }
    p  { font-size:1.15rem; opacity:.9; }
    .candado { font-size:4rem; }
  </style>
</head>
<body>
  <main>
    <div class="candado">🔒</div>
    <h1>Hola desde S3 + CloudFront</h1>
    <p>Esta página vive en un bucket privado y llega a ti por HTTPS.</p>
    <p><b>Hecha por:</b> TU NOMBRE</p>
  </main>
</body>
</html>
```
En la terminal:
```powershell
terraform init
terraform apply
```
→ `Plan: 6 to add` → `yes`. CloudFront tarda **3–5 minutos**. Abre `tu_web_https` (🔒) y `directo_al_bucket` (debe decir **AccessDenied**).
Cambia algo en `index.html` → Ctrl+S → `terraform apply` → `1 to change` → recarga.

**Paso 3 · Destroy**
```powershell
terraform destroy
```
→ `6 destroyed` (CloudFront tarda unos minutos en borrarse).

> Si `AccessDenied` al hacer apply: faltan los permisos del paso 0.
> Si `BucketAlreadyExists` en la CLI: vuelve a generar `$b` (el nombre es único en el mundo).
> Si la página dice `AccessDenied` también por CloudFront: espera 1–2 minutos (la política del bucket se está aplicando) y recarga.
> `.gitignore`: el mismo de la clase pasada (nunca `terraform.tfstate` ni `.terraform/`).
