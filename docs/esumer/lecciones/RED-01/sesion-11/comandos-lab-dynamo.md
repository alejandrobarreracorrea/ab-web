# LAB A · DynamoDB con la AWS CLI (copiar y pegar en el chat)

_Terminal de VS Code (PowerShell), dentro de la carpeta `lab-dynamo`. Sintaxis validada con la AWS CLI v2 (2026-10-09)._

**Paso 0 · Permiso y carpeta**
- IAM → Usuarios → `terraform-lab` → Agregar permisos → `AmazonDynamoDBFullAccess` (y `AmazonRDSFullAccess` para el LAB B)
```
mkdir $HOME\lab-dynamo
code $HOME\lab-dynamo
```
`camila.json`
```
{
  "cliente":  { "S": "camila" },
  "fecha":    { "S": "2026-10-09 10:00" },
  "producto": { "S": "camiseta" },
  "talla":    { "S": "L" }
}
```

`pedro.json`
```
{
  "cliente":  { "S": "pedro" },
  "fecha":    { "S": "2026-10-09 10:05" },
  "producto": { "S": "gorra" },
  "regalo":   { "S": "si" }
}
```

`consulta.json`
```
{ ":c": { "S": "camila" } }
```

**Paso 1 · Crea, guarda y lee**
```
$T = "pedidos-tunombre"
aws dynamodb create-table --table-name $T --attribute-definitions "AttributeName=cliente,AttributeType=S" "AttributeName=fecha,AttributeType=S" --key-schema "AttributeName=cliente,KeyType=HASH" "AttributeName=fecha,KeyType=RANGE" --billing-mode PAY_PER_REQUEST
aws dynamodb wait table-exists --table-name $T
aws dynamodb put-item --table-name $T --item file://camila.json
aws dynamodb put-item --table-name $T --item file://pedro.json
aws dynamodb query --table-name $T --key-condition-expression "cliente = :c" --expression-attribute-values file://consulta.json
aws dynamodb scan --table-name $T
```
Reto: en `camila.json` cambia la fecha a `2026-10-09 11:00` y el producto, vuelve a hacer `put-item` y repite el `query` → `"Count": 2`.

Consola → DynamoDB → Tablas → tu tabla → **Explorar elementos** 📸

**Paso 2 · Limpieza**
```
aws dynamodb delete-table --table-name $T
```

> Si sale *ResourceInUseException*: esa tabla ya existe → cambia el nombre en `$T`.
> Si sale *AccessDeniedException*: falta `AmazonDynamoDBFullAccess` en `terraform-lab`.
> Si `file://` no encuentra el archivo: la terminal no está en la carpeta → `cd $HOME\lab-dynamo`.
