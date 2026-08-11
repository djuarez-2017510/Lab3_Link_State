import json
import socket
import threading
import uuid

def recvall(sock, n):
    data = bytearray()
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data.extend(packet)
    return bytes(data)

class BankServer:
    def __init__(self, listen_ip='127.0.0.1', listen_port=8002, gateway_ip='127.0.0.1', gateway_port=9003):
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        self.gateway_ip = gateway_ip
        self.gateway_port = gateway_port
        self.sessions = {}

    def start(self):
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((self.listen_ip, self.listen_port))
        server_socket.listen(10)
        
        print(f"Servidor Bancario operativo en {self.listen_ip}:{self.listen_port}")
        
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
            message = json.loads(data.decode('utf-8'))
            
            if message.get("type") == "DATA":
                response = self._process_transaction(message)
                if response:
                    self._send_to_gateway(response)
                    
        except Exception:
            pass
        finally:
            client_sock.close()

    def _process_transaction(self, message):
        session_id = message.get("session_id")
        payload_data = message.get("payload", {})
        command = payload_data.get("command")
        val = payload_data.get("payload")
        
        print(f"[*] Requerimiento recibido -> Sesión: {session_id} | Comando: {command} | Valor: '{val}'")
        
        if command == "START_TRANSACTION":
            self.sessions[session_id] = {"state": "WAITING_CARD", "balance": 10000}
            return self._build_response(message, "TRANSACTION_READY", "")
            
        if session_id not in self.sessions:
            return self._build_response(message, "PROTOCOL_ERROR", "Sesión inactiva o inválida.")
            
        session = self.sessions[session_id]
        state = session["state"]
        
        if command == "CARD" and state == "WAITING_CARD":
            session["state"] = "WAITING_PIN"
            return self._build_response(message, "CARD_ACCEPTED", "")
            
        elif command == "PIN" and state == "WAITING_PIN":
            if val == "0507":
                session["state"] = "WAITING_OPTION"
                return self._build_response(message, "PIN_ACCEPTED", "")
            else:
                del self.sessions[session_id]
                return self._build_response(message, "PIN_INCORRECT", "")
                
        elif command == "OPTION" and state == "WAITING_OPTION":
            if val == "1":
                session["state"] = "COMPLETED"
                return self._build_response(message, "BALANCE", str(session["balance"]))
            elif val == "2":
                session["state"] = "WAITING_AMOUNT"
                return self._build_response(message, "REQUEST_AMOUNT", "")
                
        elif command == "AMOUNT" and state == "WAITING_AMOUNT":
            amount = int(val)
            if amount <= session["balance"]:
                session["balance"] -= amount
                session["state"] = "COMPLETED"
                return self._build_response(message, "WITHDRAWAL_SUCCESSFUL", str(session["balance"]))
            else:
                return self._build_response(message, "INSUFFICIENT_FUNDS", str(session["balance"]))
                
        elif command == "LOGOUT":
            if session_id in self.sessions:
                del self.sessions[session_id]
            return self._build_response(message, "LOGOUT_ACK", "")
            
        else:
            del self.sessions[session_id]
            return self._build_response(message, "PROTOCOL_ERROR", f"Comando inesperado: {command}")

    def _build_response(self, original_msg, resp_command, resp_payload):
        return {
            "type": "DATA",
            "packet_id": f"p-{uuid.uuid4().hex[:6]}",
            "session_id": original_msg["session_id"],
            "origin": original_msg["destination"],
            "destination": original_msg["origin"],
            "noise": original_msg["noise"],
            "payload": {
                "command": resp_command,
                "payload": resp_payload
            }
        }

    def _send_to_gateway(self, message):
        try:
            payload = json.dumps(message).encode('utf-8')
            header = len(payload).to_bytes(4, 'big')
            
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((self.gateway_ip, self.gateway_port))
            s.sendall(header + payload)
            s.close()
        except ConnectionRefusedError:
            pass

if __name__ == "__main__":
    server = BankServer()
    server.start()