# LAB RDS · la base de pedidos de Laura (copiar y pegar en el chat)

_Terraform validado (`terraform validate` + `terraform plan` → **12 to add**; MySQL 8.0 en db.t3.micro con gp3 disponible en us-east-1), 2026-10-09. Todo en tu PC: VS Code + su terminal (PowerShell), y MobaXterm para entrar a la tienda._

**Paso 0 · Permiso de RDS para tu llave (consola)**
IAM → Usuarios → `terraform-lab` → Permisos → Agregar permisos → Adjuntar políticas directamente → `AmazonRDSFullAccess` → Agregar permisos.

**Paso 1 · La tienda + la base con Terraform**
```powershell
mkdir $HOME\tienda-bd
code $HOME\tienda-bd
```
`main.tf`:
```hcl
# =====================================================================
#  La base de pedidos de Laura en RDS · Esumer · Cloud Computing, Redes y Ciberseguridad
#  Red con 1 subred pública (la tienda) y 2 privadas en 2 zonas (la base),
#  EC2 con el cliente MySQL y una base RDS MySQL a la que SOLO entra la tienda.
#
#  Uso (en la terminal de VS Code, carpeta con este main.tf y tu terraform.tfvars):
#    terraform init
#    terraform apply      (RDS tarda 5-10 minutos en quedar lista)
#    terraform destroy    (al final; no la dejes encendida)
# =====================================================================

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

provider "aws" {
  region = "us-east-1"
}

# ---------- Tus datos (van en terraform.tfvars, que NUNCA se sube a GitHub)
variable "mi_ip" { type = string } # tu IP de casa con /32
variable "llave" { type = string } # nombre de tu key pair (.ppk)
variable "db_password" {           # clave de la base: mínimo 8 caracteres
  type      = string
  sensitive = true
}

# ---------- 1 · La red: 1 calle pública (tienda) y 2 privadas (base de datos)
resource "aws_vpc" "tienda" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  tags                 = { Name = "tienda-vpc" }
}

resource "aws_subnet" "web" {
  vpc_id                  = aws_vpc.tienda.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = "us-east-1a"
  map_public_ip_on_launch = true
  tags                    = { Name = "web-publica" }
}

resource "aws_subnet" "bd_a" {
  vpc_id            = aws_vpc.tienda.id
  cidr_block        = "10.0.11.0/24"
  availability_zone = "us-east-1a"
  tags              = { Name = "bd-privada-a" }
}

resource "aws_subnet" "bd_b" {
  vpc_id            = aws_vpc.tienda.id
  cidr_block        = "10.0.12.0/24"
  availability_zone = "us-east-1b"
  tags              = { Name = "bd-privada-b" }
}

resource "aws_internet_gateway" "puerta" {
  vpc_id = aws_vpc.tienda.id
  tags   = { Name = "tienda-igw" }
}

resource "aws_route_table" "publica" {
  vpc_id = aws_vpc.tienda.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.puerta.id
  }
  tags = { Name = "rt-publica" }
}

resource "aws_route_table_association" "web" {
  subnet_id      = aws_subnet.web.id
  route_table_id = aws_route_table.publica.id
}

# ---------- 2 · Los porteros: uno para la tienda y otro para la base
resource "aws_security_group" "tienda" {
  name        = "tienda-sg"
  description = "SSH solo desde mi IP"
  vpc_id      = aws_vpc.tienda.id
  ingress {
    description = "SSH solo mi IP"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.mi_ip]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "tienda-sg" }
}

resource "aws_security_group" "bd" {
  name        = "bd-sg"
  description = "MySQL solo desde la tienda"
  vpc_id      = aws_vpc.tienda.id
  ingress {
    description     = "MySQL solo desde el SG de la tienda"
    from_port       = 3306
    to_port         = 3306
    protocol        = "tcp"
    security_groups = [aws_security_group.tienda.id]
  }
  tags = { Name = "bd-sg" }
}

# ---------- 3 · La base de datos: RDS MySQL en las calles privadas
resource "aws_db_subnet_group" "bd" {
  name       = "tienda-bd-subnets"
  subnet_ids = [aws_subnet.bd_a.id, aws_subnet.bd_b.id] # RDS pide 2 zonas
  tags       = { Name = "tienda-bd-subnets" }
}

resource "aws_db_instance" "pedidos" {
  identifier              = "pedidos-laura"
  engine                  = "mysql"
  engine_version          = "8.0"
  instance_class          = "db.t3.micro" # capa gratuita
  allocated_storage       = 20            # GB (capa gratuita)
  storage_type            = "gp3"
  db_name                 = "tienda"
  username                = "laura"
  password                = var.db_password
  db_subnet_group_name    = aws_db_subnet_group.bd.name
  vpc_security_group_ids  = [aws_security_group.bd.id]
  publicly_accessible     = false # NUNCA abierta a internet
  backup_retention_period = 1     # copia automática diaria (1 día)
  skip_final_snapshot     = true  # solo para el lab: borrar sin foto final
  deletion_protection     = false
  tags                    = { Name = "pedidos-laura" }
}

# ---------- 4 · La tienda: EC2 con el cliente MySQL instalado al nacer
data "aws_ssm_parameter" "amazon_linux" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_instance" "tienda" {
  ami                    = data.aws_ssm_parameter.amazon_linux.value
  instance_type          = "t3.micro"
  subnet_id              = aws_subnet.web.id
  vpc_security_group_ids = [aws_security_group.tienda.id]
  key_name               = var.llave
  user_data              = join("\n", ["#!/bin/bash", "dnf install -y mariadb105"])
  tags                   = { Name = "tienda-laura" }
}

# ---------- 5 · Lo que Terraform te muestra al terminar
output "ip_tienda" {
  value = aws_instance.tienda.public_ip
}

output "endpoint_bd" {
  value = aws_db_instance.pedidos.address
}

output "conectarse" {
  value = "mysql -h ${aws_db_instance.pedidos.address} -u laura -p tienda"
}
```
`terraform.tfvars` (tus datos · NUNCA a GitHub):
```hcl
mi_ip       = "TU.IP.DE.CASA/32"   # mírala en https://checkip.amazonaws.com
llave       = "NOMBRE-DE-TU-KEY-PAIR"
db_password = "UnaClaveLarga2026"  # mínimo 8 caracteres
```
Copia también tu `.gitignore` de la clase pasada. Luego:
```powershell
terraform init
terraform apply
```
→ `Plan: 12 to add` → `yes`. RDS tarda **5–10 minutos**. Al final muestra `ip_tienda`, `endpoint_bd` y `conectarse`.

**Paso 2 · Entra a la tienda y háblale a la base**
MobaXterm → Session → SSH → host = `ip_tienda` · usuario `ec2-user` · tu `.ppk`. Pega el comando de `conectarse` y escribe tu clave:
```sql
CREATE TABLE pedidos (
  id INT PRIMARY KEY,
  cliente VARCHAR(50),
  producto VARCHAR(50),
  talla CHAR(2)
);
INSERT INTO pedidos VALUES
  (55,'Sofía','Camiseta','M'),
  (56,'Andrés','Buzo','L'),
  (57,'Camila','Camiseta','M');
SELECT * FROM pedidos;
UPDATE pedidos SET talla='L' WHERE id=57;
SELECT * FROM pedidos WHERE talla='L';
```
Para salir: `exit`.

**Paso 3 · Prueba de seguridad (desde tu PC) y destroy**
```powershell
nslookup <endpoint_bd>
Test-NetConnection <endpoint_bd> -Port 3306
terraform destroy
```
→ IP `10.0.11.x` (privada) · `TcpTestSucceeded : False` · `Destroy complete! Resources: 12 destroyed.`

> Si `AccessDenied` al crear la base: falta `AmazonRDSFullAccess` (paso 0).
> Si `mysql: command not found`: la EC2 aún está instalando el cliente; espera 1 minuto y repite.
> Si `ERROR 2003 Can't connect`: estás intentando desde tu PC (así debe ser); conéctate desde la tienda por MobaXterm.
> Si el `apply` dice que la clave no cumple: usa mínimo 8 caracteres, sin `/`, `@`, `"` ni espacios.
> Una base RDS encendida cobra por hora: **siempre** `terraform destroy` al terminar.
> Nunca subas `terraform.tfvars`, `terraform.tfstate` ni tu `.ppk` a GitHub.
