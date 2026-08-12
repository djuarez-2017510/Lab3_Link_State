"""Base de datos de estado de enlace, Dijkstra y generacion de la tabla de ruteo.

La clase mantiene la LSDB (una LSA por router de origen, la de secuencia mas
alta), construye el grafo de la red, calcula rutas minimas y publica la tabla
resultante tanto en memoria (para el plano de datos) como en CSV (entregable
del laboratorio).
"""

import csv
import heapq
import os
import threading

from net import DATA_DIR


class LinkState:
    def __init__(self, router_id, neighbors_config, log=print):
        self.router_id = router_id
        self.neighbors_config = {n["router_id"]: n for n in neighbors_config}
        self.log = log

        self.lsdb = {}
        # Tabla vigente en memoria: destino -> next hop, ip, puerto, costo. El
        # plano de datos la consulta desde aqui y no releyendo el CSV, para no
        # hacer E/S por paquete ni leer el archivo mientras se reescribe.
        self.routing_table = {}
        self._table_lock = threading.Lock()
        # Discrepancias de costo ya reportadas, para no repetir el aviso en cada
        # recalculo.
        self._reported_conflicts = set()

    # ------------------------------------------------------------------ LSDB

    def update_lsdb(self, lsa_message):
        """Almacena la LSA si su secuencia supera la conocida para ese origen.

        Devuelve True si la LSA es nueva y debe reenviarse por flooding.
        """
        origin = lsa_message.get("origin_router_id")
        seq = lsa_message.get("sequence")
        if origin is None or not isinstance(seq, int):
            return False

        known = self.lsdb.get(origin)
        if known is None or seq > known["sequence"]:
            self.lsdb[origin] = lsa_message
            return True
        return False

    def _build_graph(self):
        """Construye el grafo no dirigido a partir de las LSAs de la LSDB.

        Un enlace se considera utilizable solo si **ambos** extremos lo anuncian.
        Esta verificacion bidireccional es la que permite que la red reaccione a
        la caida de un nodo: cuando un router deja de responder HELLO, sus
        vecinos dejan de anunciarlo, y aunque la LSA vieja del nodo caido siga en
        la LSDB, sus enlaces quedan sin confirmar y Dijkstra los ignora. Sin esta
        regla la LSA obsoleta mantendria vivas rutas hacia un nodo apagado.
        """
        # Adyacencia declarada por cada origen, tal como viene en su LSA.
        claimed = {}
        for origin, lsa in self.lsdb.items():
            declared = {}
            for link in lsa.get("links", []):
                neighbor = link.get("neighbor_router_id")
                cost = link.get("cost")
                if neighbor is None or not isinstance(cost, (int, float)):
                    continue
                declared[neighbor] = cost
            claimed[origin] = declared

        graph = {node: {} for node in claimed}
        for a, declared in claimed.items():
            for b, cost in declared.items():
                reverse = claimed.get(b, {}).get(a)
                if reverse is None:
                    # Enlace anunciado por un solo extremo: puede ser un nodo
                    # caido, una LSA que aun no llega o una configuracion
                    # asimetrica. No se usa para calcular rutas.
                    continue

                if reverse != cost:
                    # El contrato define el costo como un valor acordado en la
                    # configuracion, asi que una discrepancia es un error de
                    # configuracion y se reporta. Se conserva el mayor para que
                    # todos los routers converjan al mismo grafo de forma
                    # determinista, sin depender del orden de llegada.
                    key = tuple(sorted((a, b)))
                    if key not in self._reported_conflicts:
                        self._reported_conflicts.add(key)
                        self.log(
                            f"[{self.router_id}] ERROR DE CONFIGURACION: el enlace "
                            f"{key[0]}-{key[1]} se anuncia con costos distintos "
                            f"({cost} y {reverse}). Se usara {max(cost, reverse)}."
                        )
                    cost = max(cost, reverse)

                graph[a][b] = cost

        return graph

    # -------------------------------------------------------------- Dijkstra

    def compute_shortest_paths(self):
        """Ejecuta Dijkstra sobre el grafo actual y publica la tabla de ruteo."""
        graph = self._build_graph()
        if self.router_id not in graph:
            return

        distances = {node: float("inf") for node in graph}
        distances[self.router_id] = 0
        previous = {node: None for node in graph}
        visited = set()
        pq = [(0, self.router_id)]

        while pq:
            current_dist, current_node = heapq.heappop(pq)
            if current_node in visited:
                continue
            visited.add(current_node)
            for neighbor, weight in graph[current_node].items():
                distance = current_dist + weight
                if distance < distances[neighbor]:
                    distances[neighbor] = distance
                    previous[neighbor] = current_node
                    heapq.heappush(pq, (distance, neighbor))

        self._publish_table(distances, previous)

    def _first_hop(self, dest, previous):
        """Retrocede la cadena de predecesores hasta el primer salto desde aqui."""
        step = dest
        # El guardia contra ciclos evita colgarse si la cadena viniera
        # corrupta por LSAs inconsistentes de otra implementacion.
        for _ in range(len(previous) + 1):
            parent = previous.get(step)
            if parent is None:
                return None
            if parent == self.router_id:
                return step
            step = parent
        return None

    def _publish_table(self, distances, previous):
        table = {}
        for dest, dist in distances.items():
            if dest == self.router_id or dist == float("inf"):
                continue

            next_hop = self._first_hop(dest, previous)
            if next_hop is None:
                continue

            # El primer salto tiene que ser un vecino directo configurado. Si no
            # lo es, otro nodo anuncio un enlace hacia nosotros que no existe en
            # nuestra configuracion; antes esto lanzaba KeyError y derribaba el
            # hilo de routing, asi que ahora se descarta la ruta y se avisa.
            neighbor = self.neighbors_config.get(next_hop)
            if neighbor is None:
                if next_hop not in self._reported_conflicts:
                    self._reported_conflicts.add(next_hop)
                    self.log(
                        f"[{self.router_id}] ERROR DE CONFIGURACION: se calculo "
                        f"'{next_hop}' como siguiente salto hacia '{dest}', pero "
                        f"no es un vecino directo configurado. Ruta descartada."
                    )
                continue

            table[dest] = {
                "next_hop_router_id": next_hop,
                "next_hop_ip": neighbor["ip"],
                "next_hop_port": neighbor["port"],
                "total_cost": dist,
            }

        with self._table_lock:
            changed = table != self.routing_table
            self.routing_table = table

        self._write_csv(table)
        return changed

    def lookup(self, destination_router_id):
        """Consulta la tabla en memoria. Devuelve None si el destino no existe."""
        with self._table_lock:
            return self.routing_table.get(destination_router_id)

    def snapshot(self):
        """Copia de la tabla vigente, para impresion y diagnostico."""
        with self._table_lock:
            return dict(self.routing_table)

    # ------------------------------------------------------------------- CSV

    def _write_csv(self, table):
        """Escribe la tabla con el encabezado acordado en el contrato.

        Se escribe en un archivo temporal y se reemplaza de forma atomica: antes
        el archivo se truncaba con mode='w' mientras el plano de datos lo leia,
        de modo que un paquete podia encontrar una tabla vacia o a medias.
        """
        os.makedirs(DATA_DIR, exist_ok=True)
        final_path = os.path.join(DATA_DIR, f"{self.router_id}_tabla_enrutamiento.csv")
        temp_path = final_path + ".tmp"

        with open(temp_path, mode="w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "destination_router_id",
                "next_hop_router_id",
                "next_hop_ip",
                "next_hop_port",
                "total_cost",
            ])
            for dest in sorted(table):
                row = table[dest]
                writer.writerow([
                    dest,
                    row["next_hop_router_id"],
                    row["next_hop_ip"],
                    row["next_hop_port"],
                    row["total_cost"],
                ])

        os.replace(temp_path, final_path)
