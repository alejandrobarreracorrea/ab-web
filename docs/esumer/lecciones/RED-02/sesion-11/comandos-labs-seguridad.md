# Laboratorios sesión 11 · 3 retos de seguridad (copiar y pegar en el chat)

_Todo contra tus propios recursos, en tu propia cuenta. Terraform del LAB 3 validado: plan = 8 to add._


## LAB 1 · Hash con tus manos

- Terminal de VS Code (PowerShell) → carpeta nueva lab-hash
- Crea el archivo y saca su huella → copia los primeros 8 caracteres
- Cambia 45 por 4 y repite → compara: ¿cuánto cambió la huella?
- Pregunta → ¿por qué la tienda guarda huellas y no claves? 📸

**en PowerShell**
```
mkdir $HOME\lab-hash; cd $HOME\lab-hash
"camiseta,45" > precio.txt
Get-FileHash precio.txt -Algorithm SHA256
"camiseta,4" > precio.txt
Get-FileHash precio.txt -Algorithm SHA256
```

## LAB 2 · Paso 0 · rol para Systems Manager

- Consola → IAM → Roles → Crear rol → Servicio de AWS → EC2
- Permisos: AmazonSSMManagedInstanceCore → nombre: ec2-ssm → Crear rol
- EC2 → tu instancia → Acciones → Seguridad → Modificar el rol de IAM → elige ec2-ssm → Actualizar
- Espera 1–3 minutos → o reinicia el agente desde MobaXterm (comando de la derecha)

**opcional · en MobaXterm, dentro de la EC2**
```
sudo systemctl restart amazon-ssm-agent
sudo systemctl status amazon-ssm-agent --no-pager
```

## LAB 2 · Paso 1 · entra sin .ppk y cierra el 22

- EC2 → tu instancia → Conectar → pestaña Session Manager → Conectar → se abre una terminal en el navegador
- Escribe los comandos de la derecha → ssm-user: entraste sin llave ni puerto
- Security Group → Editar reglas de entrada → elimina la regla 22 → Guardar: 0 reglas de entrada 🔒
- Prueba MobaXterm → ya no conecta ✕ · Session Manager sí ✓ 📸
- Systems Manager → Session Manager → Historial → cada sesión queda registrada

**en la terminal del navegador**
```
whoami
sudo su - ec2-user
hostname
cat /etc/os-release | head -2
```

**si mañana necesitas MobaXterm otra vez**
```
# Security Group → Agregar regla: SSH (22) · origen: Mi IP
```

## LAB 3 · Paso 0 · la tienda con escudo

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

## LAB 3 · Equipo rojo

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

## LAB 3 · Equipo azul

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

> Al terminar: `terraform destroy` (LAB 3).
