"""Cajero automatico: host origen, conectado unicamente a su router gateway.

No es un router y no participa en el plano de control. Envia envolturas DATA a su
gateway, que las encamina hasta el gateway del banco, y escucha las respuestas
que ese mismo gateway le entrega.

Uso:
    python src/atm_client.py [config/host_atm.json]
"""

import json
import os
import socket
import sys
import threading
import uuid

import net

MENU = """
Opciones de transaccion:
  1. Insertar tarjeta (CARD)
  2. Ingresar PIN
  3. Seleccionar opcion (1: saldo, 2: retiro)
  4. Ingresar monto a retirar (AMOUNT)
  5. Salir (LOGOUT)
"""

# El protocolo bancario es de solicitud y respuesta: el ATM espera la respuesta
# de cada comando antes de enviar el siguiente. Cada mensaje viaja en su propia
# conexion TCP y cada salto lo atiende en un hilo distinto, asi que enviar sin
# esperar permitiria que dos comandos se adelanten entre si y lleguen al banco en
# orden invertido, rompiendo su maquina de estados.
RESPONSE_TIMEOUT = 8.0


class ATMClient:
    def __init__(self, config_path):
        with open(config_path, "r", encoding="utf-8") as handle:
            config = json.load(handle)

        self.host_id = config.get("host_id", "ATM")
        self.ip = config["listen"]["ip"]
        self.port = config["listen"]["port"]
        self.gateway = config["gateway"]
        self.peer = config["peer"]

        self.session_id = f"s-{uuid.uuid4().hex[:6]}"
        self.noise_prob = 0.0
        self.running = True
        # Sincronizacion entre el hilo que escucha respuestas y el menu.
        self._response = threading.Event()

    def log(self, message):
        print(message, flush=True)

    def start(self):
        threading.Thread(target=self._listen, daemon=True).start()
        self.log(f"ATM {self.host_id} operativo en {self.ip}:{self.port}")
        self.log(
            f"Gateway: {self.gateway['router_id']} en "
            f"{self.gateway['ip']}:{self.gateway['port']}"
        )
        self._menu()

    # ------------------------------------------------------------- respuestas

    def _listen(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.ip, self.port))
        server.listen(8)

        while self.running:
            try:
                client, _ = server.accept()
            except OSError:
                continue
            threading.Thread(
                target=self._handle_response, args=(client,), daemon=True
            ).start()

    def _handle_response(self, client):
        try:
            body = net.recv_framed(client)
            if not body:
                return
            message = json.loads(body)
            if message.get("type") != "DATA":
                return
            payload = message.get("payload", {})
            command = payload.get("command")
            value = payload.get("payload", "")
            detail = f": {value}" if value else ""
            self.log(f"---> [BANCO] {command}{detail}")
            self._response.set()
        except json.JSONDecodeError:
            self.log("\n[!] Respuesta descartada: JSON invalido.")
        except Exception as exc:  # noqa: BLE001
            self.log(f"\n[!] Error procesando respuesta: {exc}")
        finally:
            client.close()

    # -------------------------------------------------------------- solicitudes

    def _send(self, command, value=""):
        message = {
            "type": "DATA",
            "packet_id": f"p-{uuid.uuid4().hex[:6]}",
            "session_id": self.session_id,
            "origin": {
                "host_id": self.host_id,
                "gateway_id": self.gateway["router_id"],
            },
            "destination": {
                "host_id": self.peer["host_id"],
                "gateway_id": self.peer["gateway_id"],
            },
            "noise": {"bit_flip_probability": self.noise_prob},
            "payload": {"command": command, "payload": value},
        }

        self._response.clear()
        if not net.send_json(self.gateway["ip"], self.gateway["port"], message):
            self.log(
                f"[!] No se pudo alcanzar el gateway "
                f"{self.gateway['router_id']} en "
                f"{self.gateway['ip']}:{self.gateway['port']}"
            )
            return

        self.log(f"[>] Enviado {command}")
        if not self._response.wait(RESPONSE_TIMEOUT):
            self.log(
                f"[!] Sin respuesta del banco en {RESPONSE_TIMEOUT:.0f} s. "
                f"El paquete pudo descartarse por ruido o no hay ruta disponible."
            )

    def _ask_noise(self):
        """Solicita la probabilidad de ruido, constante durante toda la sesion."""
        raw = input("Probabilidad de ruido por bit [0.0]: ").strip()
        if not raw:
            self.noise_prob = 0.0
            return
        try:
            value = float(raw)
        except ValueError:
            self.log("Valor invalido. Se usara 0.0.")
            return
        if not 0.0 <= value <= 1.0:
            self.log("Fuera del rango 0.0-1.0. Se usara 0.0.")
            return

        self.noise_prob = value
        # Hamming (7,4) corrige un solo bit por bloque de 7. Una envoltura DATA
        # ocupa cientos de bloques y el ruido se reaplica en cada salto, asi que
        # valores altos producen perdidas frecuentes por errores dobles.
        if value >= 0.005:
            self.log(
                f"Aviso: con ruido {value} es probable que algunos paquetes se "
                f"descarten por errores de 2 bits en un mismo bloque."
            )

    def _menu(self):
        print("\n" + "=" * 44)
        print("   SIMULADOR DE CAJERO AUTOMATICO")
        print("=" * 44)

        self._ask_noise()
        self.log(f"Sesion {self.session_id}. Enviando START_TRANSACTION...")
        self._send("START_TRANSACTION")

        acciones = {
            "1": ("CARD", "Numero de tarjeta: "),
            "2": ("PIN", "PIN (de prueba: 0507): "),
            "3": ("OPTION", "Opcion (1 o 2): "),
            "4": ("AMOUNT", "Monto a retirar: "),
        }

        while self.running:
            print(MENU)
            try:
                opcion = input("Seleccione un paso: ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if opcion == "5":
                self._send("LOGOUT")
                self.log("Cerrando sesion en el ATM...")
                self.running = False
                break

            accion = acciones.get(opcion)
            if accion is None:
                self.log("Opcion invalida.")
                continue

            command, prompt = accion
            self._send(command, input(prompt).strip())


if __name__ == "__main__":
    default_config = os.path.join(net.CONFIG_DIR, "host_atm.json")
    config_path = sys.argv[1] if len(sys.argv) > 1 else default_config
    ATMClient(config_path).start()
