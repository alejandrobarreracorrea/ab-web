# LAB sesión 11 · Rojo vs. azul con WAF (copiar y pegar en el chat)

_Solo contra tu propia tienda, en tu propia cuenta. Terraform validado: plan = 8 to add (2026-10-14). Archivos en `terraform-waf/`._


## Paso 0 · la tienda con escudo

- IAM → terraform-lab → Agregar permisos → AWSWAFFullAccess → (S3 y CloudFront ya los tiene de clases pasadas)
- mkdir $HOME\tienda-wafcode $HOME\tienda-waf → carpeta nueva, abierta en VS Code
- Crea main.tf e index.html → pon tu nombre en index.html · Ctrl+S
- terraform init · terraform apply → Plan: 8 to add → yes · CloudFront tarda 3–5 min
- Abre tu_web_https → tu tienda con 🛡️ · el WAF está en observar

**main.tf  (S3 privado + CloudFront + WAF)** → ver `terraform-waf/`

**index.html** → ver `terraform-waf/`

**en la terminal de VS Code**
```
terraform init
terraform apply
```

## Equipo rojo · la ráfaga

- $W = tu tu_web_https → cópialo de la salida de terraform apply
- Una petición normal → 200 ✓
- La ráfaga: 150 peticiones seguidas → todas 200: el WAF está en observar
- Consola → WAF & Shield → Web ACLs → región Global (CloudFront) → escudo-tienda → pestaña de tráfico: ves limite-por-ip en COUNT 📸
- El WAF ya vio el ataque → pero todavía no lo frena

**en la terminal de VS Code (PowerShell)**
```
$W = "https://dxxxxxxxxxx.cloudfront.net"
curl.exe -s -o NUL -w "%{http_code}`n" "$W/?buscar=camiseta"
1..150 | ForEach-Object { curl.exe -s -o NUL -w "%{http_code} " "$W/" }
```

## Equipo azul · bloquear y lista negra

- terraform apply -var="modo=bloquear" → tarda segundos: no toca CloudFront
- Rojo repite la ráfaga → después de ~100: 403 ✕ (puede tardar hasta 1 min en reaccionar) 📸
- checkip te dice tu IP pública → ponla en bloqueo.tfvars con /32
- apply -var-file y abre tu tienda → 403 Request blocked: te bloqueaste a ti 😄
- Al terminar → terraform destroy: el WAF cobra por hora

**1 · bloquear**
```
terraform apply -var="modo=bloquear"
1..150 | ForEach-Object { curl.exe -s -o NUL -w "%{http_code} " "$W/" }
```

**2 · lista negra (crea bloqueo.tfvars)**
```
curl.exe -s https://checkip.amazonaws.com
```

**bloqueo.tfvars**
```
modo           = "bloquear"
ips_bloqueadas = ["TU.IP.PUBLICA/32"]
```

**3 · aplicar y limpiar**
```
terraform apply -var-file="bloqueo.tfvars"
terraform destroy
```
