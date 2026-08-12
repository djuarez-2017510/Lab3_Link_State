"""Servidor bancario: host destino, conectado unicamente a su router gateway.

No es un router y no participa en el plano de control. Recibe envolturas DATA ya
corregidas desde su gateway, mantiene el estado de cada transaccion por
session_id y responde con otra envoltura DATA intercambiando origen y destino.

Uso:
    python src/bank_server.py [config/host_bank.json]
"""

import json
import os
import socket
import sys
import threading
import uuid

import net

# Tarjeta y PIN de prueba fijados para el laboratorio.
VALID_PIN = "0507"
INITIAL_BALANCE = 10000


class BankServer:
    def __init__(self, config_path):
        with open(config_path, "r", encoding="utf-8") as handle:
            config = json.load(handle)

        self.host_id = config.get("host_id", "BANK")
        self.ip = config["listen"]["ip"]
        self.port = config["listen"]["port"]
        self.gateway = config["gateway"]

        self.sessions = {}
        self.lock = threading.Lock()

    def log(self, message):
        print(message, flush=True)

    def start(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.ip, self.port))
        server.listen(16)

        self.log(f"Servidor bancario {self.host_id} operativo en {self.ip}:{self.port}")
        self.log(
            f"Gateway: {self.gateway['router_id']} en "
            f"{self.gateway['ip']}:{self.gateway['port']}"
        )

        try:
            while True:
                client, _ = server.accept()
                threading.Thread(
                    target=self._handle_connection, args=(client,), daemon=True
                ).start()
        except KeyboardInterrupt:
            self.log("\nServidor bancario detenido.")

    def _handle_connection(self, client):
        try:
            body = net.recv_framed(client)
            if not body:
                return
            message = json.loads(body)
            if message.get("type") != "DATA":
                return

            response = self._process(message)
            if response:
                sent = net.send_json(
                    self.gateway["ip"], self.gateway["port"], response
                )
                if not sent:
                    self.log(
                        f"[!] No se pudo alcanzar el gateway "
                        f"{self.gateway['router_id']}; respuesta perdida."
                    )
        except json.JSONDecodeError:
            self.log("[!] Trama descartada: JSON invalido.")
        except Exception as exc:  # noqa: BLE001
            self.log(f"[!] Error procesando conexion: {exc}")
        finally:
            client.close()

    def _process(self, message):
        """Aplica la maquina de estados bancaria y devuelve la respuesta DATA."""
        session_id = message.get("session_id")
        payload = message.get("payload", {})
        command = payload.get("command")
        value = payload.get("payload", "")

        self.log(
            f"[*] Sesion: {session_id} | Comando: {command} | Valor: '{value}'"
        )

        with self.lock:
            if command == "START_TRANSACTION":
                self.sessions[session_id] = {
                    "state": "WAITING_CARD",
                    "balance": INITIAL_BALANCE,
                }
                return self._respond(message, "TRANSACTION_READY")

            session = self.sessions.get(session_id)
            if session is None:
                return self._respond(
                    message, "PROTOCOL_ERROR", "Sesion inactiva o invalida."
                )

            state = session["state"]

            if command == "CARD" and state == "WAITING_CARD":
                # Validacion minima acordada para el laboratorio: la tarjeta debe
                # ser numerica y de al menos 4 digitos.
                if not value.isdigit() or len(value) < 4:
                    del self.sessions[session_id]
                    return self._respond(message, "CARD_INVALID")
                session["state"] = "WAITING_PIN"
                return self._respond(message, "CARD_ACCEPTED")

            if command == "PIN" and state == "WAITING_PIN":
                if value == VALID_PIN:
                    session["state"] = "WAITING_OPTION"
                    return self._respond(message, "PIN_ACCEPTED")
                del self.sessions[session_id]
                return self._respond(message, "PIN_INCORRECT")

            if command == "OPTION" and state == "WAITING_OPTION":
                if value == "1":
                    session["state"] = "COMPLETED"
                    return self._respond(
                        message, "BALANCE", str(session["balance"])
                    )
                if value == "2":
                    session["state"] = "WAITING_AMOUNT"
                    return self._respond(message, "REQUEST_AMOUNT")
                del self.sessions[session_id]
                return self._respond(
                    message, "PROTOCOL_ERROR", "Se esperaba la opcion 1 o 2."
                )

            if command == "AMOUNT" and state == "WAITING_AMOUNT":
                if not value.isdigit() or int(value) <= 0:
                    del self.sessions[session_id]
                    return self._respond(
                        message, "PROTOCOL_ERROR", "Se esperaba un monto positivo."
                    )
                amount = int(value)
                if amount > session["balance"]:
                    # El contrato conserva WAITING_AMOUNT: el ATM puede reintentar.
                    return self._respond(
                        message, "INSUFFICIENT_FUNDS", str(session["balance"])
                    )
                session["balance"] -= amount
                session["state"] = "COMPLETED"
                return self._respond(
                    message, "WITHDRAWAL_SUCCESSFUL", str(session["balance"])
                )

            if command == "LOGOUT":
                del self.sessions[session_id]
                return self._respond(message, "LOGOUT_ACK")

            del self.sessions[session_id]
            return self._respond(
                message,
                "PROTOCOL_ERROR",
                f"Comando '{command}' inesperado en estado {state}.",
            )

    def _respond(self, original, command, value=""):
        """Construye la respuesta DATA intercambiando origen y destino."""
        return {
            "type": "DATA",
            "packet_id": f"p-{uuid.uuid4().hex[:6]}",
            "session_id": original["session_id"],
            "origin": original["destination"],
            "destination": original["origin"],
            "noise": original.get("noise", {"bit_flip_probability": 0.0}),
            "payload": {"command": command, "payload": value},
        }


if __name__ == "__main__":
    default_config = os.path.join(net.CONFIG_DIR, "host_bank.json")
    config_path = sys.argv[1] if len(sys.argv) > 1 else default_config
    BankServer(config_path).start()
