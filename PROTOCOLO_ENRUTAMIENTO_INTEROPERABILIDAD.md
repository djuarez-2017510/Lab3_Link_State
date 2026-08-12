# Protocolo de enrutamiento: contrato de interoperabilidad

Este documento define el contrato común entre las tres parejas. Cada implementación puede usar el lenguaje que prefiera; debe respetar los formatos, reglas y significados definidos aquí.

## 1. Modelo de red

- Cada router tiene un `router_id` único (`U`, `V`, `X`, etc.), usado dentro de la topología.
- La IP y el puerto indican dónde se puede contactar al router. En pruebas locales se usa `127.0.0.1` con puertos distintos; en integración se sustituyen por IP y puerto de Tailscale.
- Cada router conoce inicialmente **solo** sus vecinos directos, con `router_id`, IP, puerto y costo.
- Un host ATM se conecta únicamente con su router gateway. El banco se conecta únicamente con su propio gateway. Los routers intermedios no interpretan operaciones bancarias.

## 2. Configuración mínima por router

### Gateway del ATM / cliente

```json
{
  "router_id": "U",
  "listen": { "ip": "127.0.0.1", "port": 9001 },
  "neighbors": [
    { "router_id": "V", "ip": "127.0.0.1", "port": 9002, "cost": 3 }
  ],
  "attached_host": {
    "role": "CLIENT",
    "host_id": "ATM",
    "ip": "127.0.0.1",
    "port": 8001
  }
}
```

### Router intermedio

```json
{
  "router_id": "V",
  "listen": { "ip": "127.0.0.1", "port": 9002 },
  "neighbors": [
    { "router_id": "U", "ip": "127.0.0.1", "port": 9001, "cost": 3 },
    { "router_id": "X", "ip": "127.0.0.1", "port": 9003, "cost": 2 }
  ],
  "attached_host": null
}
```

### Gateway del banco / servidor

```json
{
  "router_id": "X",
  "listen": { "ip": "127.0.0.1", "port": 9003 },
  "neighbors": [
    { "router_id": "V", "ip": "127.0.0.1", "port": 9002, "cost": 2 }
  ],
  "attached_host": {
    "role": "SERVER",
    "host_id": "BANK",
    "ip": "127.0.0.1",
    "port": 8002
  }
}
```

Al integrar con otras computadoras, se sustituyen `127.0.0.1` por las IP de Tailscale correspondientes. Si varios routers se ejecutan en una misma computadora, comparten IP y se distinguen por puerto.

## 3. Plano de control

Los mensajes de control son JSON sin Hamming.

```json
{ "type": "HELLO", "origin_router_id": "U", "listen_port": 9001 }
```

```json
{ "type": "HELLO_REPLY", "origin_router_id": "V", "cost": 3 }
```

`HELLO` verifica que el vecino configurado está activo y que su identidad coincide. El costo proviene de la configuración acordada; una discrepancia se reporta como error de configuración.

```json
{
  "type": "LSA",
  "origin_router_id": "U",
  "sequence": 4,
  "links": [
    { "neighbor_router_id": "V", "cost": 3 }
  ],
  "from_router_id": "U"
}
```

Cada router conserva la secuencia más alta recibida por cada `origin_router_id`. Una LSA nueva se almacena y se reenvía a todos los vecinos excepto a `from_router_id`; al reenviarla, se actualiza `from_router_id` con el identificador del router que realiza ese salto. Una LSA con secuencia menor o igual se descarta. Con las LSAs recibidas se construye el grafo, se ejecuta Dijkstra y se genera el CSV.

## 4. Tabla de enrutamiento

Todos los routers generan el mismo encabezado CSV:

```text
destination_router_id,next_hop_router_id,next_hop_ip,next_hop_port,total_cost
```

El destino es siempre un router gateway, no el ATM ni el banco. El forwarding consulta `destination_router_id` y utiliza la IP y puerto de `next_hop_router_id`.

## 5. Plano de datos y protocolo bancario

Cada mensaje bancario viaja dentro de una envoltura `DATA`:

```json
{
  "type": "DATA",
  "packet_id": "uuid-por-mensaje",
  "session_id": "uuid-por-transaccion",
  "origin": { "host_id": "ATM", "gateway_id": "U" },
  "destination": { "host_id": "BANK", "gateway_id": "X" },
  "noise": { "bit_flip_probability": 0.01 },
  "payload": { "command": "CARD", "payload": "123456" }
}
```

Los routers solo usan `destination.gateway_id` para enrutar. Al llegar a ese gateway, este entrega al host indicado la envoltura `DATA` completa, ya corregida. El host utiliza `session_id` y `payload`, y conserva `origin` y `destination` para poder responder. La respuesta bancaria usa el mismo formato, intercambiando `origin` y `destination`.

El `payload` conserva el flujo bancario existente, ahora como JSON: `START_TRANSACTION`, `CARD`, `PIN`, `OPTION`, `AMOUNT`, sus respuestas, mensajes de error y cierre de sesión. El banco conserva el estado de la transacción mediante `session_id`, no mediante una conexión TCP directa.

### Caso de uso por rol

**ATM / cliente.** El ATM crea una sola `session_id`, solicita una probabilidad de ruido al iniciar y crea un `DATA` por cada comando bancario. Para una tarjeta, por ejemplo:

```json
{
  "type": "DATA",
  "packet_id": "p-001",
  "session_id": "s-001",
  "origin": { "host_id": "ATM", "gateway_id": "U" },
  "destination": { "host_id": "BANK", "gateway_id": "X" },
  "noise": { "bit_flip_probability": 0.01 },
  "payload": { "command": "CARD", "payload": "123456" }
}
```

**Router intermedio.** Si `U` recibe el paquete anterior y su CSV indica que el siguiente salto para `X` es `V`, corrige Hamming, consulta esa fila, vuelve a proteger el mismo objeto `DATA` y lo envía a `V`. No cambia `packet_id`, `session_id`, origen, destino ni `payload`. Todos los routers intermedios hacen lo mismo.

**Gateway del banco.** Cuando `X` recibe un `DATA` cuyo `destination.gateway_id` es `X`, no busca otro router: lo entrega al banco local. El banco lee `session_id` y ejecuta el comando de `payload`.

**Banco / servidor.** El banco responde con otro `DATA`. Ejemplo para una tarjeta válida:

```json
{
  "type": "DATA",
  "packet_id": "p-002",
  "session_id": "s-001",
  "origin": { "host_id": "BANK", "gateway_id": "X" },
  "destination": { "host_id": "ATM", "gateway_id": "U" },
  "noise": { "bit_flip_probability": 0.01 },
  "payload": { "command": "CARD_ACCEPTED", "payload": "" }
}
```

`X` encamina esta respuesta hacia `U`; al llegar a `U`, este la entrega al ATM. El ATM interpreta únicamente `payload` y actualiza su interfaz.

### Catálogo completo de mensajes bancarios

Los JSON siguientes son los únicos mensajes de aplicación que viajan dentro de `DATA`. `packet_id` cambia en cada mensaje, `session_id` se conserva durante toda la transacción y `bit_flip_probability` se conserva durante toda la sesión. Los valores `U` y `X` representan, respectivamente, el gateway del ATM y el gateway del banco en la topología de ejemplo.

#### Solicitudes: ATM → banco

**Iniciar transacción.** El banco crea el estado `WAITING_CARD` para `session_id` y responde `TRANSACTION_READY`.

```json
{"type":"DATA","packet_id":"p-001","session_id":"s-001","origin":{"host_id":"ATM","gateway_id":"U"},"destination":{"host_id":"BANK","gateway_id":"X"},"noise":{"bit_flip_probability":0.01},"payload":{"command":"START_TRANSACTION","payload":""}}
```

**Enviar tarjeta.**

```json
{"type":"DATA","packet_id":"p-002","session_id":"s-001","origin":{"host_id":"ATM","gateway_id":"U"},"destination":{"host_id":"BANK","gateway_id":"X"},"noise":{"bit_flip_probability":0.01},"payload":{"command":"CARD","payload":"123456"}}
```

**Enviar PIN.**

```json
{"type":"DATA","packet_id":"p-003","session_id":"s-001","origin":{"host_id":"ATM","gateway_id":"U"},"destination":{"host_id":"BANK","gateway_id":"X"},"noise":{"bit_flip_probability":0.01},"payload":{"command":"PIN","payload":"0507"}}
```

**Seleccionar consulta de saldo.**

```json
{"type":"DATA","packet_id":"p-004","session_id":"s-001","origin":{"host_id":"ATM","gateway_id":"U"},"destination":{"host_id":"BANK","gateway_id":"X"},"noise":{"bit_flip_probability":0.01},"payload":{"command":"OPTION","payload":"1"}}
```

**Seleccionar retiro.**

```json
{"type":"DATA","packet_id":"p-005","session_id":"s-001","origin":{"host_id":"ATM","gateway_id":"U"},"destination":{"host_id":"BANK","gateway_id":"X"},"noise":{"bit_flip_probability":0.01},"payload":{"command":"OPTION","payload":"2"}}
```

**Enviar monto de retiro.** Este mensaje puede repetirse únicamente después de `INSUFFICIENT_FUNDS` y conserva el mismo `session_id`.

```json
{"type":"DATA","packet_id":"p-006","session_id":"s-001","origin":{"host_id":"ATM","gateway_id":"U"},"destination":{"host_id":"BANK","gateway_id":"X"},"noise":{"bit_flip_probability":0.01},"payload":{"command":"AMOUNT","payload":"1200"}}
```

**Cerrar sesión.** Puede enviarse después de una operación terminada o para cancelar una transacción activa.

```json
{"type":"DATA","packet_id":"p-007","session_id":"s-001","origin":{"host_id":"ATM","gateway_id":"U"},"destination":{"host_id":"BANK","gateway_id":"X"},"noise":{"bit_flip_probability":0.01},"payload":{"command":"LOGOUT","payload":""}}
```

#### Respuestas: banco → ATM

**Transacción lista.**

```json
{"type":"DATA","packet_id":"p-101","session_id":"s-001","origin":{"host_id":"BANK","gateway_id":"X"},"destination":{"host_id":"ATM","gateway_id":"U"},"noise":{"bit_flip_probability":0.01},"payload":{"command":"TRANSACTION_READY","payload":""}}
```

**Tarjeta aceptada.** El banco pasa a `WAITING_PIN`.

```json
{"type":"DATA","packet_id":"p-102","session_id":"s-001","origin":{"host_id":"BANK","gateway_id":"X"},"destination":{"host_id":"ATM","gateway_id":"U"},"noise":{"bit_flip_probability":0.01},"payload":{"command":"CARD_ACCEPTED","payload":""}}
```

**Tarjeta inválida.** El banco elimina la sesión; el ATM debe crear una nueva `session_id` para intentar otra transacción.

```json
{"type":"DATA","packet_id":"p-103","session_id":"s-001","origin":{"host_id":"BANK","gateway_id":"X"},"destination":{"host_id":"ATM","gateway_id":"U"},"noise":{"bit_flip_probability":0.01},"payload":{"command":"CARD_INVALID","payload":""}}
```

**PIN aceptado.** El banco pasa a `WAITING_OPTION`.

```json
{"type":"DATA","packet_id":"p-104","session_id":"s-001","origin":{"host_id":"BANK","gateway_id":"X"},"destination":{"host_id":"ATM","gateway_id":"U"},"noise":{"bit_flip_probability":0.01},"payload":{"command":"PIN_ACCEPTED","payload":""}}
```

**PIN incorrecto.** El banco elimina la sesión; el ATM debe crear una nueva `session_id` para intentar otra transacción.

```json
{"type":"DATA","packet_id":"p-105","session_id":"s-001","origin":{"host_id":"BANK","gateway_id":"X"},"destination":{"host_id":"ATM","gateway_id":"U"},"noise":{"bit_flip_probability":0.01},"payload":{"command":"PIN_INCORRECT","payload":""}}
```

**Saldo consultado.** El banco pasa a `COMPLETED`; el ATM puede cerrar con `LOGOUT`.

```json
{"type":"DATA","packet_id":"p-106","session_id":"s-001","origin":{"host_id":"BANK","gateway_id":"X"},"destination":{"host_id":"ATM","gateway_id":"U"},"noise":{"bit_flip_probability":0.01},"payload":{"command":"BALANCE","payload":"10000"}}
```

**Monto solicitado.** El banco pasa a `WAITING_AMOUNT`.

```json
{"type":"DATA","packet_id":"p-107","session_id":"s-001","origin":{"host_id":"BANK","gateway_id":"X"},"destination":{"host_id":"ATM","gateway_id":"U"},"noise":{"bit_flip_probability":0.01},"payload":{"command":"REQUEST_AMOUNT","payload":""}}
```

**Retiro exitoso.** El banco pasa a `COMPLETED`; el payload es el saldo nuevo.

```json
{"type":"DATA","packet_id":"p-108","session_id":"s-001","origin":{"host_id":"BANK","gateway_id":"X"},"destination":{"host_id":"ATM","gateway_id":"U"},"noise":{"bit_flip_probability":0.01},"payload":{"command":"WITHDRAWAL_SUCCESSFUL","payload":"8800"}}
```

**Fondos insuficientes.** El banco conserva `WAITING_AMOUNT`; el payload es el saldo disponible y el ATM puede enviar otro `AMOUNT`.

```json
{"type":"DATA","packet_id":"p-109","session_id":"s-001","origin":{"host_id":"BANK","gateway_id":"X"},"destination":{"host_id":"ATM","gateway_id":"U"},"noise":{"bit_flip_probability":0.01},"payload":{"command":"INSUFFICIENT_FUNDS","payload":"10000"}}
```

**Error de secuencia o formato.** El banco elimina la sesión; el payload describe el motivo exacto.

```json
{"type":"DATA","packet_id":"p-110","session_id":"s-001","origin":{"host_id":"BANK","gateway_id":"X"},"destination":{"host_id":"ATM","gateway_id":"U"},"noise":{"bit_flip_probability":0.01},"payload":{"command":"PROTOCOL_ERROR","payload":"Se esperaba CARD."}}
```

**Cierre confirmado.** El banco elimina la sesión.

```json
{"type":"DATA","packet_id":"p-111","session_id":"s-001","origin":{"host_id":"BANK","gateway_id":"X"},"destination":{"host_id":"ATM","gateway_id":"U"},"noise":{"bit_flip_probability":0.01},"payload":{"command":"LOGOUT_ACK","payload":""}}
```

Si Hamming (7,4) no produce un `DATA` JSON válido después de corregir, el nodo registra el incidente y descarta la trama. No se define un JSON `INTEGRITY_ERROR` para ese caso, porque sin recuperar de forma válida el origen y destino no existe una ruta segura para devolverlo. La selección de algoritmo de Lab 2 deja de formar parte del flujo bancario: este laboratorio utiliza siempre Hamming (7,4). Los mensajes de saludo y experimentos de rendimiento tampoco forman parte de esta especificación.

## 6. Hamming, ruido y TCP

- Todos los mensajes `DATA` se serializan como JSON UTF-8, se convierten a bits y se protegen con Hamming (7,4).
- La probabilidad `bit_flip_probability`, entre `0.0` y `1.0`, se selecciona al inicio de la sesión y se aplica a cada bit protegido en cada salto.
- Cada router recibe, corrige Hamming, consulta su tabla, vuelve a aplicar Hamming y ruido, y reenvía.
- Cada conexión TCP empieza con un encabezado de longitud de 4 bytes, seguido del contenido. El encabezado no se somete a Hamming ni a ruido.
- En un único puerto TCP por router, un cuerpo JSON que inicia con `{` corresponde al plano de control; un cuerpo compuesto por `0` y `1` corresponde a `DATA` protegido.

## 7. Orden de ejecución

1. Iniciar banco y routers.
2. Los routers ejecutan HELLO, LSA, flooding, Dijkstra y generan sus tablas.
3. Con las tablas listas, iniciar el ATM.
4. El ATM envía solicitudes al gateway del cliente; los routers las reenvían hasta el gateway del banco.
5. El banco responde usando el mismo mecanismo en sentido inverso.
