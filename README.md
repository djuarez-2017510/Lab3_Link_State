# Laboratorio 3 — Protocolos de enrutamiento (Link State)

Implementación completa del protocolo de enrutamiento **Link State** sobre sockets
TCP, con una aplicación bancaria (ATM y banco) como plano de datos y **Hamming
(7,4)** como capa de detección y corrección de errores.

## Requisitos

- **Python 3.8 o superior.** No hay dependencias externas: el proyecto usa solo
  la biblioteca estándar (`json`, `socket`, `threading`, `csv`, `heapq`, `uuid`).
- Para la fase de integración, una cuenta en la red privada **Tailscale** del grupo.

```bash
python -m venv .venv                  # opcional, por consistencia de versión
pip install -r requirements.txt        # no instala nada; documenta la ausencia de dependencias
```

## Estructura

| Ruta | Contenido |
|---|---|
| `src/net.py` | Encuadre de 4 bytes, envío tolerante a fallos y rutas del proyecto |
| `src/hamming.py` | Codificación, detección y corrección Hamming (7,4) |
| `src/link_state.py` | LSDB, construcción del grafo, Dijkstra y generación del CSV |
| `src/router.py` | Nodo router: HELLO, LSA, flooding y forwarding |
| `src/atm_client.py` | Host cliente (ATM). No es router |
| `src/bank_server.py` | Host servidor (banco). No es router |
| `config/` | Topología `U–V–X` del contrato, más configuración de los hosts |
| `config/topologia_ejemplo/` | Topología `A…I` del enunciado, con rutas alternativas |
| `tests/test_protocolo.py` | Pruebas de Hamming y del cálculo de rutas |
| `data/` | Generado en ejecución: `<nodo>_tabla_enrutamiento.csv` |

## Ejecución local

### Windows (PowerShell)

```powershell
.\run_local.ps1                      # topología U - V - X
.\run_local.ps1 -Topologia ejemplo   # topología A..I (nueve nodos)
```

El script abre una ventana por nodo, respeta el orden de arranque del contrato
(banco → routers → ATM) y espera a que las tablas converjan antes de abrir el ATM.
Para detener todo: `Get-Process python | Stop-Process -Force`.

### Linux y macOS

```bash
make simple      # levanta banco y routers U, V, X
make atm         # abre el ATM (interactivo)
make stop        # detiene los nodos
```

### Manualmente, un nodo por terminal

```bash
python src/bank_server.py config/host_bank.json
python src/router.py config/router_x.json
python src/router.py config/router_v.json
python src/router.py config/router_u.json
python src/atm_client.py config/host_atm.json     # al final, tablas ya estables
```

### Pruebas

```bash
python tests/test_protocolo.py
```

## Cómo funciona

### Plano de control

Cada nodo levanta tres hilos en paralelo: **forwarding** (servidor TCP), **HELLO**
y **routing** (LSA y Dijkstra).

1. **HELLO.** Cada 5 s el router envía `HELLO` a cada vecino de su configuración.
   El vecino responde `HELLO_REPLY` con el costo que él tiene registrado para ese
   enlace. Se valida que la identidad coincida con la configuración y que los
   costos concuerden; una discrepancia se reporta como error de configuración.
   Si un vecino no responde durante tres rondas (15 s) se declara **caído**.
2. **LSA.** El router anuncia cada 10 s una LSA con número de secuencia creciente
   que contiene **solo los vecinos activos**. Un cambio de adyacencia dispara el
   anuncio de inmediato, sin esperar el ciclo periódico.
3. **Flooding.** Una LSA con secuencia mayor a la conocida para su origen se
   almacena y se reenvía a todos los vecinos excepto a aquel del que vino. Las
   secuencias menores o iguales se descartan, lo que corta la propagación
   infinita. Cada vecino se contacta en su propio hilo.
4. **Grafo y Dijkstra.** Con la LSDB se construye el grafo y se calculan las rutas
   mínimas. Un enlace se usa **solo si ambos extremos lo anuncian**: esta
   verificación bidireccional es lo que permite reaccionar a la caída de un nodo,
   porque su LSA obsoleta sigue en la LSDB pero sus vecinos ya dejaron de
   confirmarla.
5. **Tabla de ruteo.** Se publica en memoria (la usa el forwarding) y en
   `data/<nodo>_tabla_enrutamiento.csv`, con el encabezado del contrato. El CSV se
   escribe en un temporal y se reemplaza de forma atómica, para que el plano de
   datos nunca lea un archivo a medio escribir.

### Plano de datos

El ATM envía la envoltura `DATA` en JSON a su gateway. Entre routers el mensaje
viaja **protegido con Hamming (7,4)**: cada router corrige los bits, deserializa,
extrae `destination.gateway_id`, consulta su tabla, vuelve a codificar aplicando
el ruido configurado y reenvía al siguiente salto. El gateway del destino entrega
la envoltura completa y corregida a su host local.

Sobre un mismo puerto TCP se distinguen los dos planos como indica el contrato: un
cuerpo que empieza con `{` es plano de control; un cuerpo de `0` y `1` es `DATA`
protegido.

**Protección contra bucles.** Si un `packet_id` pasa más de cuatro veces por el
mismo router, el paquete se descarta y se registra el bucle. Se resuelve
localmente y no con un campo `TTL` porque el contrato no define uno, y agregarlo
unilateralmente rompería la interoperabilidad con las otras parejas.

### Límite conocido de Hamming (7,4)

El código corrige **un** error por bloque de 7 bits. Una envoltura `DATA` ocupa
varios cientos de bloques y el ruido se reaplica en **cada salto**, de modo que
probabilidades altas producen errores dobles dentro de un mismo bloque. En ese
caso el JSON no se recupera; el nodo registra el incidente y descarta la trama,
como establece el contrato. Tasa de entrega medida sobre un salto:

| `bit_flip_probability` | Paquetes entregados intactos |
|---|---|
| 0.0 | 100 % |
| 0.001 | 99 % |
| 0.005 | 87 % |
| 0.01 | 60 % |
| 0.02 | 18 % |

Para demostrar corrección efectiva conviene usar valores del orden de `0.0005` a
`0.002`; para demostrar el límite del código, `0.01` o más.

## Topologías incluidas

### `config/` — U–V–X

La del contrato de interoperabilidad. Cadena `U(3)V(2)X`, con el ATM en `U` y el
banco en `X`. Sirve para verificar el flujo completo con pocos procesos.

### `config/topologia_ejemplo/` — A…I

El grafo del enunciado, con nueve nodos y once aristas, ATM en `A` y banco en `G`.
Al tener rutas alternativas sí demuestra la selección de ruta óptima:

```
A—B 7   A—I 1   A—C 2   B—F 3   C—D 3   I—D 6
F—D 2   F—H 4   F—G 3   D—E 5   E—G 2
```

> **Los costos de esta topología son provisionales.** El enunciado los muestra
> como etiquetas en la imagen y deben sustituirse por los que se acuerden con las
> otras dos parejas antes de la integración.

Con estos costos, la ruta óptima de `A` a `G` es **A → C → D → F → G, costo 10**,
por encima de tres alternativas (`A-B-F-G` = 13, `A-I-D-F-G` = 12,
`A-C-D-E-G` = 12). Al detener el router `F`, la red reconverge a
**A → C → D → E → G, costo 12**, y al reiniciarlo vuelve a la ruta de costo 10.

## Integración sobre Tailscale

La arquitectura no cambia; solo la configuración.

1. Instalar Tailscale e iniciar sesión con la cuenta de Google en la red del
   grupo. Obtener la IP propia (`100.x.y.z`) con `tailscale ip -4`.
2. En el archivo del router propio, poner en `listen.ip` la **IP de Tailscale** o
   `0.0.0.0`. Si se deja `127.0.0.1`, ningún nodo externo podrá conectarse.
3. En `neighbors`, poner la IP de Tailscale y el puerto de cada vecino, con el
   costo acordado con la pareja dueña de ese nodo. Los puertos pueden repetirse
   entre máquinas distintas.
4. En la configuración de los hosts, `gateway.ip` es la IP de Tailscale del router
   propio, y `listen.ip` la del host.
5. Verificar la conectividad antes de arrancar: `tailscale ping <ip-del-vecino>`.

Al levantar los nodos, el log de HELLO confirma la adyacencia
(`vecino X ACTIVO`) y avisa si un costo no coincide entre las dos parejas, que es
el error de configuración más común en la integración.

