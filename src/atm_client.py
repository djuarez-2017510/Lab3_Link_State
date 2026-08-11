import json
import socket
import threading
import uuid
import time

def recvall(sock, n):
    data = bytearray()
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data.extend(packet)
    return bytes(data)

class ATMClient:
    def __init__(self, listen_ip='127.0.0.1', listen_port=8001, gateway_ip='127.0.0.1', gateway_port=9001):
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        self.gateway_ip = gateway_ip
        self.gateway_port = gateway_port
        self.session_id = f"s-{uuid.uuid4().hex[:6]}"
        self.noise_prob = 0.01  
        self.running = True

    def start(self):
        threading.Thread(target=self._listen_responses, daemon=True).start()
        print(f"ATM Operativo en {self.listen_ip}:{self.listen_port}")
        self._show_menu()

    def _listen_responses(self):
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((self.listen_ip, self.listen_port))
        server_socket.listen(5)

        while self.running:
            try:
                client_sock, _ = server_socket.accept()
                self._handle_response(client_sock)
            except Exception:
                pass

    def _handle_response(self, client_sock):
        try:
            header = recvall(client_sock, 4)
            if not header: return
            msg_length = int.from_bytes(header, 'big')
            
            data = recvall(client_sock, msg_length)
            if not data: return
            message = json.loads(data.decode('utf-8'))
            
            if message.get("type") == "DATA":
                payload = message.get("payload", {})
                print(f"\n---> [BANCO] {payload.get('command')}: {payload.get('payload')}")
        except Exception:
            pass
        finally:
            client_sock.close()

    def _send_command(self, command, payload_data=""):
        packet_id = f"p-{uuid.uuid4().hex[:6]}"
        message = {
            "type": "DATA",
            "packet_id": packet_id,
            "session_id": self.session_id,
            "origin": { "host_id": "ATM", "gateway_id": "U" },
            "destination": { "host_id": "BANK", "gateway_id": "X" },
            "noise": { "bit_flip_probability": self.noise_prob },
            "payload": { "command": command, "payload": payload_data }
        }
        
        try:
            payload = json.dumps(message).encode('utf-8')
            header = len(payload).to_bytes(4, 'big')
            
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((self.gateway_ip, self.gateway_port))
            s.sendall(header + payload)
            s.close()
        except ConnectionRefusedError:
            print("\n[Error] No se pudo conectar al Gateway del ATM (Router U).")

    def _show_menu(self):
        time.sleep(1) 
        print("\n" + "="*40)
        print("   SIMULADOR DE CAJERO AUTOMÁTICO")
        print("="*40)
        
        try:
            prob = input("Ingrese probabilidad de ruido (ej. 0.01 para 1%) [0.0]: ")
            self.noise_prob = float(prob) if prob else 0.0
        except ValueError:
            print("Valor inválido. Usando 0.0 por defecto.")
            self.noise_prob = 0.0
            
        print("\nEnviando START_TRANSACTION...")
        self._send_command("START_TRANSACTION")
        
        while self.running:
            print("\nOpciones de Transacción:")
            print("1. Insertar Tarjeta (CARD)")
            print("2. Ingresar PIN")
            print("3. Seleccionar Opción (1: Saldo, 2: Retiro)")
            print("4. Ingresar Monto a retirar (AMOUNT)")
            print("5. Salir (LOGOUT)")
            
            opcion = input("Seleccione un paso: ")
            
            if opcion == '1':
                tarjeta = input("Ingrese número de tarjeta: ")
                self._send_command("CARD", tarjeta)
            elif opcion == '2':
                pin = input("Ingrese PIN (De prueba: 0507): ")
                self._send_command("PIN", pin)
            elif opcion == '3':
                opt = input("Ingrese opción (1 o 2): ")
                self._send_command("OPTION", opt)
            elif opcion == '4':
                monto = input("Ingrese monto a retirar: ")
                self._send_command("AMOUNT", monto)
            elif opcion == '5':
                self._send_command("LOGOUT")
                print("Cerrando sesión en el ATM...")
                self.running = False
                break
            else:
                print("Opción inválida.")
            
            time.sleep(0.5) 

if __name__ == "__main__":
    atm = ATMClient()
    atm.start()