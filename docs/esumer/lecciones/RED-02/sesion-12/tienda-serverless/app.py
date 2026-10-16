# La Lambda de la tienda: recibe pedidos de la página y los guarda en DynamoDB
import base64, json, os, time, uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

# Esto corre UNA sola vez, cuando AWS prende una instancia nueva (arranque en frío)
tabla = boto3.resource("dynamodb").Table(os.environ["TABLA"])
INSTANCIA = uuid.uuid4().hex[:6]  # nombre corto de esta instancia de la Lambda
frio = True


def responder(codigo, datos):
    return {"statusCode": codigo, "headers": {"content-type": "application/json"}, "body": json.dumps(datos, default=str)}


def handler(event, context):
    # Esto corre en CADA petición
    global frio
    inicio = time.time()
    era_frio, frio = frio, False
    metodo = event["requestContext"]["http"]["method"]

    if metodo == "POST":  # guardar un pedido
        cuerpo = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            cuerpo = base64.b64decode(cuerpo).decode()
        datos = json.loads(cuerpo)
        cliente = str(datos.get("cliente", "")).strip().lower()[:40]
        if not cliente:
            return responder(400, {"error": "falta el cliente"})
        pedido = {
            "cliente": cliente,                                                    # llave de partición
            # llave de orden: la hora + 4 letras al azar (dos pedidos en el mismo milisegundo no se pisan)
            "fecha": datetime.now(timezone.utc).isoformat(timespec="milliseconds") + "#" + uuid.uuid4().hex[:4],
            "producto": str(datos.get("producto", "camiseta"))[:40],
            "talla": str(datos.get("talla", "M"))[:4],
            "instancia": INSTANCIA,
        }
        tabla.put_item(Item=pedido)  # PutItem: guardar
        print("pedido guardado:", json.dumps(pedido))  # esto sale en CloudWatch Logs
        resultado = {"operacion": "PutItem", "pedido": pedido}
    else:
        # GET: ?cliente=camila → Query (solo su partición) · sin cliente → Scan (toda la tabla)
        cliente = ((event.get("queryStringParameters") or {}).get("cliente") or "").strip().lower()
        if cliente:
            items = tabla.query(KeyConditionExpression=Key("cliente").eq(cliente))["Items"]
            resultado = {"operacion": "Query", "pedidos": items}
        else:
            items = tabla.scan()["Items"]
            items.sort(key=lambda p: p["fecha"], reverse=True)
            resultado = {"operacion": "Scan", "pedidos": items}

    resultado.update({"instancia": INSTANCIA, "frio": era_frio, "ms": round((time.time() - inicio) * 1000)})
    return responder(200, resultado)
