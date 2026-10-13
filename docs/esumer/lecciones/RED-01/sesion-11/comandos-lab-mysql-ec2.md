# LAB 1 · MySQL (MariaDB) instalado en tu EC2 (copiar y pegar en el chat)

_En MobaXterm, dentro de tu EC2 pública de clases pasadas (Amazon Linux 2023). MariaDB es el MySQL que trae Amazon Linux: mismos comandos y mismo SQL._

**Paso 0 · Entra e instala el motor**
- Consola → EC2 → tu instancia (si está detenida: Iniciar instancia; la IP pública cambia)
- MobaXterm → Session → SSH → host = IP pública · usuario `ec2-user` · tu `.ppk`
```
sudo dnf install -y mariadb105-server
sudo systemctl enable --now mariadb
sudo systemctl status mariadb --no-pager
mysql --version
```
Debe decir **active (running)**.

**Paso 1 · Tus primeros pedidos**
```
sudo mysql
```
```
CREATE DATABASE tienda;
USE tienda;
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
exit
```
📸 pantallazo del último SELECT.

**Paso 2 · ¿Dónde quedó y quién la cuida?**
```
sudo ls /var/lib/mysql/tienda
df -h /
sudo ss -ltn | grep 3306
```
¿Quién parcha MariaDB, saca copias o la recupera si se borra la EC2? **Tú** → por eso existe RDS (LAB 2).

> Si `dnf` dice *No match for argument*: estás en otra distribución; confirma con `cat /etc/os-release` (debe ser Amazon Linux 2023).
> Si `sudo mysql` dice *Can't connect … socket*: el motor está apagado → `sudo systemctl start mariadb`.
> Si sale *database exists*: ya la creaste antes → sigue con `USE tienda;`.
