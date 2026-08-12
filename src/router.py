"""Nodo router: plano de control (HELLO, LSA, flooding, Dijkstra) y plano de datos.

El nodo levanta varios hilos en paralelo:

  - servidor TCP de forwarding, que atiende cada conexion entrante;
  - hilo de HELLO, que verifica que los vecinos configurados sigan activos;
  - hilo de LSA, que anuncia periodicamente los enlaces vigentes.

Los formatos de mensaje siguen PROTOCOLO_ENRUTAMIENTO_INTEROPERABILIDAD.md.

Uso:
    python src/router.py config/router_u.json
"""

import json
import socket
import sys
import threading
import time

import net
from hamming import Hamming74
from link_state import LinkState

# Cadencia del plano de control. Un vecino se declara caido si no contesta tres
# rondas seguidas de HELLO.
HELLO_INTERVAL = 5.0
NEIGHBOR_TIMEOUT = HELLO_INTERVAL * 3
LSA_INTERVAL = 10.0

# Un mismo packet_id no deberia pasar mas de una vez por el mismo router: el
# contrato exige un packet_id distinto por mensaje. Reenviarlo repetidas veces
# delata un bucle de ruteo por tablas transitoriamente inconsistentes.
MAX_FORWARDS_PER_PACKET = 4
PACKET_MEMORY_SECONDS = 60.0


class RouterNode:
    def __init__(self, config_path):
        with open(config_path, "r", encoding="utf-8") as handle:
            self.config = json.load(handle)

        self.router_id = self.config["router_id"]
        self.ip = self.config["listen"]["ip"]
        self.port = self.config["listen"]["port"]
        self.neighbors = self.config["neighbors"]
        self.attached_host = self.config.get("attached_host")

        self.neighbors_by_id = {n["router_id"]: n for n in self.neighbors}
        # Estado de adyacencia alimentado por HELLO_REPLY. Solo los vecinos
        # activos se anuncian en nuestra LSA, de modo que la caida de un nodo
        # se propaga por la red y Dijkstra deja de usar ese enlace.
        self.neighbor_state = {
            rid: {"up": False, "last_seen": 0.0} for rid in self.neighbors_by_id
        }

        self.sequence = 0
        self.state_lock = threading.Lock()
        self.link_state = LinkState(self.router_id, self.neighbors, log=self.log)

        self._forward_counts = {}
        self._packet_lock = threading.Lock()
        self._lsa_wakeup = threading.Event()

    def log(self, message):
        print(message, flush=True)

    # --------------------------------------------------------------- arranque

    def start(self):
        threading.Thread(target=self._serve, name="forwarding", daemon=True).start()
        threading.Thread(target=self._hello_loop, name="hello", daemon=True).start()
        threading.Thread(target=self._lsa_loop, name="routing", daemon=True).start()

        self.log(f"Router {self.router_id} operativo en {self.ip}:{self.port}")
        if self.attached_host:
            host = self.attached_host
            self.log(
                f"[{self.router_id}] host local {host['host_id']} "
                f"({host['role']}) en {host['ip']}:{host['port']}"
            )
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.log(f"\nRouter {self.router_id} detenido.")

    def _run_forever(self, name, step, interval):
        """Ejecuta step() periodicamente sin que una excepcion mate el hilo."""
        while True:
            try:
                step()
            except Exception as exc:  # noqa: BLE001 - el hilo debe sobrevivir
                self.log(f"[{self.router_id}] error en hilo {name}: {exc}")
            time.sleep(interval)

    # ------------------------------------------------------- servidor entrante

    def _serve(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.ip, self.port))
        server.listen(16)

        while True:
            try:
                client, addr = server.accept()
            except OSError as exc:
                self.log(f"[{self.router_id}] error aceptando conexion: {exc}")
                continue
            threading.Thread(
                target=self._handle_connection, args=(client, addr), daemon=True
            ).start()

    def _handle_connection(self, client, addr):
        try:
            body = net.recv_framed(client)
            if not body:
                return

            if net.looks_like_bits(body):
                self._handle_protected_data(body)
                return

            message = json.loads(body)
            kind = message.get("type")

            if kind == "LSA":
                self._handle_lsa(message)
            elif kind == "HELLO":
                self._handle_hello(message, addr, client)
            elif kind == "HELLO_REPLY":
                self._handle_hello_reply(message)
            elif kind == "DATA":
                # DATA en JSON plano llega del host local (ATM o banco), que no
                # aplica Hamming. Entre routers siempre viaja protegido.
                self._route(message)
            else:
                self.log(f"[{self.router_id}] tipo de mensaje desconocido: {kind}")
        except json.JSONDecodeError:
            self.log(f"[{self.router_id}] trama descartada: JSON invalido")
        except Exception as exc:  # noqa: BLE001
            self.log(f"[{self.router_id}] error procesando conexion: {exc}")
        finally:
            client.close()

    # ------------------------------------------------------------------ HELLO

    def _hello_loop(self):
        self._run_forever("hello", self._hello_round, HELLO_INTERVAL)

    def _hello_round(self):
        """Envia HELLO a cada vecino configurado y expira los que no contestan."""
        hello = {
            "type": "HELLO",
            "origin_router_id": self.router_id,
            "listen_port": self.port,
        }
        for neighbor in self.neighbors:
            net.send_json(neighbor["ip"], neighbor["port"], hello)

        now = time.monotonic()
        for rid, state in self.neighbor_state.items():
            if state["up"] and now - state["last_seen"] > NEIGHBOR_TIMEOUT:
                self._set_neighbor_up(rid, False, "sin respuesta a HELLO")

    def _handle_hello(self, message, addr, client):
        """Responde un HELLO validando que la identidad coincida con la config."""
        origin = message.get("origin_router_id")
        neighbor = self.neighbors_by_id.get(origin)
        if neighbor is None:
            self.log(
                f"[{self.router_id}] HELLO ignorado: '{origin}' no es un vecino "
                f"configurado (desde {addr[0]})"
            )
            return

        reply = json.dumps({
            "type": "HELLO_REPLY",
            "origin_router_id": self.router_id,
            "cost": neighbor["cost"],
        })

        # El HELLO trae listen_port justamente para poder contestar por una
        # conexion nueva. Se intenta tambien sobre el socket abierto, por si la
        # implementacion del par espera la respuesta ahi; si ya lo cerro, el
        # error se ignora.
        net.reply_framed(client, reply)
        listen_port = message.get("listen_port", neighbor["port"])
        net.send_framed(addr[0], listen_port, reply)

    def _handle_hello_reply(self, message):
        origin = message.get("origin_router_id")
        neighbor = self.neighbors_by_id.get(origin)
        if neighbor is None:
            self.log(
                f"[{self.router_id}] HELLO_REPLY ignorado: '{origin}' no es un "
                f"vecino configurado"
            )
            return

        advertised = message.get("cost")
        if advertised is not None and advertised != neighbor["cost"]:
            self.log(
                f"[{self.router_id}] ERROR DE CONFIGURACION: el enlace hacia "
                f"'{origin}' vale {neighbor['cost']} aqui y {advertised} alla."
            )

        self.neighbor_state[origin]["last_seen"] = time.monotonic()
        if not self.neighbor_state[origin]["up"]:
            self._set_neighbor_up(origin, True, "HELLO_REPLY recibido")

    def _set_neighbor_up(self, router_id, is_up, reason):
        """Marca un vecino como activo o caido y reanuncia la LSA de inmediato."""
        self.neighbor_state[router_id]["up"] = is_up
        estado = "ACTIVO" if is_up else "CAIDO"
        self.log(f"[{self.router_id}] vecino {router_id} {estado} ({reason})")
        # Un cambio de adyacencia altera nuestros enlaces, asi que no se espera
        # el siguiente ciclo periodico para anunciarlo.
        self._lsa_wakeup.set()

    def _active_links(self):
        return [
            {"neighbor_router_id": rid, "cost": self.neighbors_by_id[rid]["cost"]}
            for rid, state in self.neighbor_state.items()
            if state["up"]
        ]

    # -------------------------------------------------------------------- LSA

    def _lsa_loop(self):
        while True:
            try:
                self._advertise()
            except Exception as exc:  # noqa: BLE001
                self.log(f"[{self.router_id}] error en hilo routing: {exc}")
            # Despierta antes si HELLO detecto un cambio de adyacencia.
            if self._lsa_wakeup.wait(LSA_INTERVAL):
                self._lsa_wakeup.clear()

    def _advertise(self):
        """Construye la LSA propia, la instala en la LSDB y la inunda."""
        with self.state_lock:
            self.sequence += 1
            lsa = {
                "type": "LSA",
                "origin_router_id": self.router_id,
                "sequence": self.sequence,
                "links": self._active_links(),
                "from_router_id": self.router_id,
            }
            self.link_state.update_lsdb(lsa)
            self._recompute()

        self._flood(lsa, exclude=None)

    def _handle_lsa(self, message):
        origin = message.get("origin_router_id")
        came_from = message.get("from_router_id")

        with self.state_lock:
            is_new = self.link_state.update_lsdb(message)
            if is_new:
                self._recompute()

        if not is_new:
            return

        self.log(
            f"[{self.router_id}] LSA de {origin} seq={message.get('sequence')} "
            f"aceptada. Recalculando Dijkstra."
        )
        self._flood(message, exclude=came_from)

    def _recompute(self):
        """Recalcula rutas y muestra la tabla cuando cambia. Requiere state_lock."""
        before = self.link_state.snapshot()
        self.link_state.compute_shortest_paths()
        after = self.link_state.snapshot()
        if after != before:
            self._print_table(after)

    def _print_table(self, table):
        if not table:
            self.log(f"[{self.router_id}] tabla de ruteo vacia (sin vecinos activos)")
            return
        rutas = ", ".join(
            f"{dest} via {row['next_hop_router_id']} (costo {row['total_cost']})"
            for dest, row in sorted(table.items())
        )
        self.log(f"[{self.router_id}] tabla de ruteo: {rutas}")

    def _flood(self, lsa, exclude):
        """Reenvia la LSA a todos los vecinos salvo aquel del que vino.

        Cada vecino se contacta en su propio hilo: con timeouts de 3 s y varios
        vecinos caidos, un envio secuencial atrasaria el flooding varios segundos.
        """
        forward = dict(lsa)
        forward["from_router_id"] = self.router_id
        body = json.dumps(forward)

        for neighbor in self.neighbors:
            if neighbor["router_id"] == exclude:
                continue
            threading.Thread(
                target=net.send_framed,
                args=(neighbor["ip"], neighbor["port"], body),
                daemon=True,
            ).start()

    # ------------------------------------------------------------ plano de datos

    def _handle_protected_data(self, bit_string):
        """Corrige Hamming, deserializa y encamina la envoltura DATA."""
        decoded = Hamming74.decode_message(bit_string)
        try:
            message = json.loads(decoded)
        except json.JSONDecodeError:
            # El contrato indica registrar y descartar: sin un origen y destino
            # validos no hay ruta segura para devolver un error.
            self.log(
                f"[{self.router_id}] trama descartada: Hamming no recupero un "
                f"DATA valido (mas de un error por bloque de 7 bits)"
            )
            return

        if message.get("type") != "DATA":
            self.log(f"[{self.router_id}] trama descartada: no es DATA")
            return

        self._route(message)

    def _route(self, message):
        """Decide si el DATA se entrega al host local o se reenvia al siguiente salto."""
        destination = message.get("destination", {})
        dest_gateway = destination.get("gateway_id")
        packet_id = message.get("packet_id", "?")

        if dest_gateway == self.router_id:
            self._deliver_local(message)
            return

        if self._is_looping(packet_id):
            self.log(
                f"[{self.router_id}] paquete {packet_id} descartado: bucle "
                f"detectado hacia '{dest_gateway}'"
            )
            return

        route = self.link_state.lookup(dest_gateway)
        if route is None:
            self.log(
                f"[{self.router_id}] paquete {packet_id} descartado: no hay ruta "
                f"hacia '{dest_gateway}'"
            )
            return

        noise = message.get("noise", {}).get("bit_flip_probability", 0.0)
        protected = Hamming74.encode_message(json.dumps(message), noise)

        if net.send_framed(route["next_hop_ip"], route["next_hop_port"], protected):
            self.log(
                f"[{self.router_id}] paquete {packet_id} hacia '{dest_gateway}' "
                f"reenviado a {route['next_hop_router_id']}"
            )
        else:
            self.log(
                f"[{self.router_id}] paquete {packet_id} no pudo enviarse a "
                f"{route['next_hop_router_id']} "
                f"({route['next_hop_ip']}:{route['next_hop_port']})"
            )

    def _deliver_local(self, message):
        """Entrega la envoltura DATA completa y corregida al host local."""
        if not self.attached_host:
            self.log(
                f"[{self.router_id}] paquete para este gateway descartado: no "
                f"hay host local conectado"
            )
            return

        host = self.attached_host
        command = message.get("payload", {}).get("command")
        if net.send_json(host["ip"], host["port"], message):
            self.log(f"[{self.router_id}] entregado a {host['host_id']}: {command}")
        else:
            self.log(
                f"[{self.router_id}] no se pudo entregar a {host['host_id']} "
                f"en {host['ip']}:{host['port']}"
            )

    def _is_looping(self, packet_id):
        """Cuenta reenvios por packet_id para cortar bucles de ruteo.

        Se resuelve localmente y no con un campo TTL porque el contrato de
        interoperabilidad no define uno, y agregarlo unilateralmente rompria la
        compatibilidad con las implementaciones de las otras parejas.
        """
        now = time.monotonic()
        with self._packet_lock:
            for known, seen_at in list(self._forward_counts.items()):
                if now - seen_at[1] > PACKET_MEMORY_SECONDS:
                    del self._forward_counts[known]

            entry = self._forward_counts.get(packet_id)
            if entry is None:
                self._forward_counts[packet_id] = [1, now]
                return False

            entry[0] += 1
            return entry[0] > MAX_FORWARDS_PER_PACKET


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python src/router.py <config.json>")
        sys.exit(1)
    RouterNode(sys.argv[1]).start()
