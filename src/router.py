import json
import socket
import threading
import time
import csv
from link_state import LinkState
from hamming import Hamming74

def recvall(sock, n):
    data = bytearray()
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data.extend(packet)
    return bytes(data)

class RouterNode:
    def __init__(self, config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)

        self.router_id = self.config["router_id"]
        self.ip = self.config["listen"]["ip"]
        self.port = self.config["listen"]["port"]
        self.neighbors = self.config["neighbors"]
        self.attached_host = self.config.get("attached_host", None)

        self.sequence = 0
        self.link_state = LinkState(self.router_id, self.neighbors)
        self.lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._listen_server, daemon=True).start()
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
            header = recvall(client_sock, 4)
            if not header: return
            msg_length = int.from_bytes(header, 'big')
            
            data = recvall(client_sock, msg_length)
            if not data: return
            decoded_data = data.decode('utf-8')
            
            if set(decoded_data).issubset({'0', '1'}):
                self._forward_data(decoded_data)
            else:
                message = json.loads(decoded_data)
                if message.get("type") == "LSA":
                    self._process_lsa(message)
                elif message.get("type") == "DATA":
                    self._forward_json_data(message)
                    
        except Exception as e:
            print(f"Error procesando conexión en {self.router_id}: {e}")
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
            time.sleep(10) 

    def _flood_lsa(self, lsa_msg):
        forward_msg = dict(lsa_msg)
        forward_msg["from_router_id"] = self.router_id
        
        payload = json.dumps(forward_msg).encode('utf-8')
        header = len(payload).to_bytes(4, 'big')
        
        for neighbor in self.neighbors:
            if neighbor["router_id"] == lsa_msg.get("from_router_id"):
                continue
                
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((neighbor["ip"], neighbor["port"]))
                s.sendall(header + payload)
                s.close()
            except ConnectionRefusedError:
                pass

    def _load_routing_table(self):
        routing_table = {}
        filepath = f'data/{self.router_id}_tabla_enrutamiento.csv'
        try:
            with open(filepath, mode='r') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    routing_table[row["destination_router_id"]] = {
                        "next_hop_ip": row["next_hop_ip"],
                        "next_hop_port": int(row["next_hop_port"])
                    }
        except FileNotFoundError:
            pass
        return routing_table

    def _forward_json_data(self, message):
        dest_gateway = message.get("destination", {}).get("gateway_id")
        routing_table = self._load_routing_table()
        
        if dest_gateway not in routing_table:
            print(f"[{self.router_id}] Destino {dest_gateway} no existe en tabla de ruteo.")
            return

        next_hop = routing_table[dest_gateway]
        noise_prob = message.get("noise", {}).get("bit_flip_probability", 0.0)
        
        forward_bits = Hamming74.encode_message(json.dumps(message), noise_prob)
        
        payload = forward_bits.encode('utf-8')
        header = len(payload).to_bytes(4, 'big')

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((next_hop["next_hop_ip"], next_hop["next_hop_port"]))
            s.sendall(header + payload)
            s.close()
        except ConnectionRefusedError:
            print(f"[{self.router_id}] Falla al enviar al salto {next_hop['next_hop_ip']}:{next_hop['next_hop_port']}")

    def _forward_data(self, bit_string):
        json_str = Hamming74.decode_message(bit_string)
        if not json_str: return

        try:
            message = json.loads(json_str)
        except json.JSONDecodeError:
            return

        dest_gateway = message.get("destination", {}).get("gateway_id")
        
        if dest_gateway == self.router_id:
            if self.attached_host:
                self._send_to_host(message)
            return

        routing_table = self._load_routing_table()
        if dest_gateway not in routing_table:
            return

        next_hop = routing_table[dest_gateway]
        noise_prob = message.get("noise", {}).get("bit_flip_probability", 0.0)
        forward_bits = Hamming74.encode_message(json.dumps(message), noise_prob)
        
        payload = forward_bits.encode('utf-8')
        header = len(payload).to_bytes(4, 'big')

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((next_hop["next_hop_ip"], next_hop["next_hop_port"]))
            s.sendall(header + payload)
            s.close()
        except ConnectionRefusedError:
            pass

    def _send_to_host(self, message):
        try:
            payload = json.dumps(message).encode('utf-8')
            header = len(payload).to_bytes(4, 'big')
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((self.attached_host["ip"], self.attached_host["port"]))
            s.sendall(header + payload)
            s.close()
        except ConnectionRefusedError:
            pass

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        router = RouterNode(sys.argv[1])
        router.start()
    else:
        print("Uso: python src/router.py <config.json>")