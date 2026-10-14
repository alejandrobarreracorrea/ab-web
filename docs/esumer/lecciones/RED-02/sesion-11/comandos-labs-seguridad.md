# LABs sesión 11 · Ciberseguridad e IAM (copiar y pegar en el chat)


## LAB 1 · Paso 0 · bucket y usuario lectora

- Terminal de VS Code (PowerShell) → comandos de la derecha → $B = tu bucket (único: tu nombre + número)
- Consola → IAM → Usuarios → Crear usuario → lectora → sin acceso a la consola
- Permisos → Adjuntar políticas directamente → Crear política → pestaña JSON → pega la política y cambia el nombre del bucket
- Nombre de la política: solo-leer-mi-bucket → Crear → vuelve, refresca, márcala → Crear usuario
- lectora → Credenciales de seguridad → Crear clave de acceso → CLI → copia el ID y la clave secreta (solo se ven una vez)

**1 · bucket + nota (con terraform-lab)**
```
$B = "seguridad-tunombre-123"
aws s3 mb "s3://$B"
"hola Laura" > nota.txt
aws s3 cp nota.txt "s3://$B/"
```

**2 · la política (pégala en IAM → JSON)**
```
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:ListBucket", "s3:GetObject"],
    "Resource": [
      "arn:aws:s3:::seguridad-tunombre-123",
      "arn:aws:s3:::seguridad-tunombre-123/*"
    ]
  }]
}
```

## LAB 1 · Paso 1 · pruebas con --profile lectora

- aws configure --profile lectora → pega sus llaves · región us-east-1 · formato json
- sts get-caller-identity → debe decir user/lectora
- ls y cp desde el bucket → ✓ permitido: listar y leer
- subir otra.txt → ✕ AccessDenied: no tiene PutObject
- ec2 describe-instances → ✕ UnauthorizedOperation 📸

**pruebas con el perfil lectora**
```
aws configure --profile lectora
aws sts get-caller-identity --profile lectora
aws s3 ls "s3://$B" --profile lectora
aws s3 cp "s3://$B/nota.txt" leida.txt --profile lectora
aws s3 cp nota.txt "s3://$B/otra.txt" --profile lectora
aws ec2 describe-instances --profile lectora --region us-east-1
```

**limpieza (al final del LAB 1)**
```
aws s3 rb "s3://$B" --force
# consola: lectora → claves → Desactivar y Eliminar
# luego Eliminar el usuario y la política solo-leer-mi-bucket
```

## LAB 2 · Paso 0 · en tu EC2 (MobaXterm)

- MobaXterm → SSH a tu EC2 de siempre → ec2-user · tu .ppk · si está detenida: Iniciar
- grep -ci cuenta los intentos fallidos → ¿cuántos bots intentaron hoy? 😱
- tail -5 muestra usuarios e IPs → admin, root, test… desde todo el mundo
- dnf upgrade --security → instala solo los parches de seguridad
- sshd -T muestra cómo está SSH → passwordauthentication no ✓ · root sin contraseña ✓

**1 · los intentos de entrar**
```
sudo journalctl -u sshd --since "24 hours ago" | grep -ciE "invalid user|failed password"
sudo journalctl -u sshd --since "24 hours ago" | grep -iE "invalid user" | tail -5
```

**2 · parches y configuración de SSH**
```
sudo dnf check-update --security
sudo dnf upgrade -y --security
sudo sshd -T | grep -E "^(permitrootlogin|passwordauthentication|pubkeyauthentication)"
```

## LAB 2 · Paso 1 · usuario, permisos y puerto 22

- useradd laura → otra persona en el servidor, sin sudo
- archivo con 644: laura lo lee 😱 → 600: Permission denied ✓ 📸
- Consola → EC2 → tu instancia → Seguridad → tu Security Group → Editar reglas de entrada
- Regla SSH (22): origen Mi IP → Guardar → MobaXterm sigue entrando; los bots ya no
- Si mañana cambia tu IP de casa → vuelve a poner Mi IP en la regla

**usuario y permisos**
```
sudo useradd laura
id laura
echo "clave-de-la-caja: 1234" > /tmp/secreto.txt
chmod 644 /tmp/secreto.txt
sudo -u laura cat /tmp/secreto.txt
chmod 600 /tmp/secreto.txt
sudo -u laura cat /tmp/secreto.txt
ls -l /tmp/secreto.txt
```

**limpieza (al final del LAB 2)**
```
sudo userdel -r laura
rm /tmp/secreto.txt
```

## LAB 3 · rol IAM para tu EC2

- En la EC2: aws sts get-caller-identity → Unable to locate credentials ✓ (aún sin rol)
- Consola → IAM → Roles → Crear rol → Servicio de AWS → EC2 → política AmazonS3ReadOnlyAccess · nombre ec2-lee-s3
- EC2 → tu instancia → Acciones → Seguridad → Modificar el rol de IAM → escoge ec2-lee-s3 → Actualizar
- Repite los comandos → assumed-role/ec2-lee-s3 · s3 ls ✓ · mb ✕ 📸
- El último comando muestra cuándo vencen → Expiration: credenciales temporales

**1 · antes y después del rol (en la EC2)**
```
export AWS_DEFAULT_REGION=us-east-1
aws sts get-caller-identity
aws s3 ls
aws s3 mb s3://prueba-rol-tunombre-123
```

**2 · ¿cuándo vencen las credenciales?**
```
TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/iam/security-credentials/ec2-lee-s3 | grep -E "Type|Expiration"
```
