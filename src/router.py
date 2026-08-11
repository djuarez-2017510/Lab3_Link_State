import json
import socket
import threading
import time
from link_state import LinkState

class RouterNode:
    def __init__(self, config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
        
        self.router_id = self.config["router_id"]
        self.ip = self.config["listen"]["ip"]
        self.port = self.config["listen"]["port"]
        self.neighbors = self.config["neighbors"]
        
        self.sequence = 0
        self.link_state = LinkState(self.router_id, self.neighbors)
        self.lock = threading.Lock()

    def start(self):
        # Hilo de servidor (escucha)
        threading.Thread(target=self._listen_server, daemon=True).start()
        # Hilo de transmisión periódica (LSA)
        threading.Thread(target=self._broadcast_lsa, daemon=True).start()
        
        print(f"Router {self.router_id} operativo en {self.ip}:{self.port}")
        while True:
            time.sleep(1)

    def _listen_server(self):
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((self.ip, self.port))
        server_socket.listen(10)

        while True:
            client_sock, _ = server_socket.accept()
            threading.Thread(target=self._handle_connection, args=(client_sock,), daemon=True).start()

    def _handle_connection(self, client_sock):
        try:
            # Los mensajes de control inician con el prefijo de longitud de 4 bytes
            header = client_sock.recv(4)
            if not header: return
            msg_length = int.from_bytes(header, 'big')
            
            data = client_sock.recv(msg_length)
            message = json.loads(data.decode('utf-8'))
            
            if message.get("type") == "LSA":
                self._process_lsa(message)
                
        except Exception as e:
            pass
        finally:
            client_sock.close()

    def _process_lsa(self, message):
        with self.lock:
            if self.link_state.update_lsdb(message):
                print(f"[{self.router_id}] LSA de {message['origin_router_id']} procesado. Recalculando Dijkstra.")
                self.link_state.compute_shortest_paths()
                self._flood_lsa(message)

    def _broadcast_lsa(self):
        while True:
            self.sequence += 1
            lsa_msg = {
                "type": "LSA",
                "origin_router_id": self.router_id,
                "sequence": self.sequence,
                "links": [{"neighbor_router_id": n["router_id"], "cost": n["cost"]} for n in self.neighbors],
                "from_router_id": self.router_id
            }
            
            with self.lock:
                self.link_state.update_lsdb(lsa_msg)
                self.link_state.compute_shortest_paths()
            
            self._flood_lsa(lsa_msg)
            time.sleep(10) # Transmisión periódica del LSA

    def _flood_lsa(self, lsa_msg):
        # El reenvío actualiza from_router_id al router que realiza el salto
        forward_msg = dict(lsa_msg)
        forward_msg["from_router_id"] = self.router_id
        
        payload = json.dumps(forward_msg).encode('utf-8')
        header = len(payload).to_bytes(4, 'big')
        
        for neighbor in self.neighbors:
            # Evitar enviar de vuelta al nodo que transmitió la LSA
            if neighbor["router_id"] == lsa_msg.get("from_router_id"):
                continue
                
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((neighbor["ip"], neighbor["port"]))
                s.sendall(header + payload)
                s.close()
            except ConnectionRefusedError:
                pass

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        router = RouterNode(sys.argv[1])
        router.start()