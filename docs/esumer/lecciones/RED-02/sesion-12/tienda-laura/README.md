# La tienda serverless de Laura

Laboratorio de la clase 12 · Esumer · Cloud Computing, Redes y Ciberseguridad.

```
tienda-laura/
├── front/          ← lo que ve el cliente (FRONT): se sube a S3 y lo entrega CloudFront
│   ├── index.html     la estructura
│   ├── estilos.css    el diseño
│   └── app.js         lo que reacciona y habla con el back
├── back/           ← lo que corre en la nube (BACK): una función Lambda
│   └── app.py         valida, pone el precio y guarda en DynamoDB
└── terraform/      ← la infraestructura que une todo
    └── main.tf        DynamoDB + Lambda + API Gateway + S3 + CloudFront
```

## Antes de empezar (una sola vez)

Tu usuario `terraform-lab` necesita permisos para Lambda, API Gateway y para crear el rol de la Lambda
(IAM → Usuarios → terraform-lab → Agregar permisos):

1. Adjuntar políticas directamente: `AWSLambda_FullAccess` y `AmazonAPIGatewayAdministrator`.
2. Crear política insertada (pestaña JSON) con el contenido de `politica-iam-roles.json`, nombre `solo-roles-tienda`.

S3, CloudFront y DynamoDB ya los tiene de clases pasadas.

## Ponerla en marcha

En la terminal de VS Code, **dentro de la carpeta `terraform`**:

```powershell
cd terraform
terraform init
terraform apply      # escribe yes · tarda unos 4 minutos (CloudFront)
```

Al terminar te muestra:

- `tu_tienda` → ábrela en el navegador (https://d….cloudfront.net)
- `tu_api` → la puerta del back (API Gateway)

Si la página dice que no puede leer los pedidos, espera un minuto y recarga: CloudFront recién creado.

## Qué probar

1. Haz un pedido: mira el viaje (navegador → API Gateway → Lambda → DynamoDB) y si fue 🥶 en frío o 🔥 caliente.
2. Pulsa **⚡ Ráfaga: 20 pedidos a la vez**: ¿cuántas instancias de la Lambda prendió AWS?
3. En la consola: DynamoDB → Explorar elementos → `tienda-pedidos`; Lambda → `tienda-serverless` → Supervisar → logs.

## Al terminar (¡importante!)

```powershell
terraform destroy    # escribe yes · Destroy complete! Resources: 16 destroyed.
```
