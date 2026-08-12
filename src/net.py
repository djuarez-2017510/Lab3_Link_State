"""Utilidades de red compartidas por routers y hosts.

Centraliza dos cosas que antes estaban repetidas en cada archivo:

1. El encuadre de 4 bytes definido en el contrato de interoperabilidad
   (seccion 6): cada conexion TCP lleva la longitud del cuerpo en 4 bytes
   big-endian y luego el cuerpo. El encabezado nunca pasa por Hamming.
2. El manejo de errores de socket. Se captura `OSError` completo y no solo
   `ConnectionRefusedError`: en pruebas locales un puerto cerrado responde de
   inmediato con `ConnectionRefusedError`, pero sobre Tailscale un nodo apagado
   produce `TimeoutError` u `OSError` de red no alcanzable. Capturar solo el
   primer caso hacia que el hilo de routing muriera en la integracion.
"""

import json
import os
import socket

# Raiz del proyecto derivada de la ubicacion de este archivo, para que config/ y
# data/ se resuelvan sin importar desde que directorio se invoque el programa.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

HEADER_SIZE = 4
# Un vecino inalcanzable debe fallar rapido: sin timeout explicito, connect()
# bloquea alrededor de 21 s en Windows y frena todo el ciclo de flooding.
DEFAULT_TIMEOUT = 3.0


def recvall(sock, n):
    """Lee exactamente n bytes del socket. Devuelve None si la conexion se corta."""
    data = bytearray()
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data.extend(packet)
    return bytes(data)


def recv_framed(sock):
    """Lee un mensaje encuadrado y lo devuelve como str, o None si falla."""
    header = recvall(sock, HEADER_SIZE)
    if not header:
        return None
    body = recvall(sock, int.from_bytes(header, "big"))
    if body is None:
        return None
    return body.decode("utf-8", errors="ignore")


def frame(body):
    """Antepone el encabezado de longitud de 4 bytes al cuerpo."""
    payload = body.encode("utf-8") if isinstance(body, str) else body
    return len(payload).to_bytes(HEADER_SIZE, "big") + payload


def send_framed(ip, port, body, timeout=DEFAULT_TIMEOUT):
    """Abre una conexion, envia un mensaje encuadrado y cierra.

    Devuelve True si se entrego. Nunca propaga excepciones de red: el llamador
    decide si un destino inalcanzable es relevante, y ningun hilo debe morir
    porque un vecino este caido.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((ip, port))
        sock.sendall(frame(body))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def send_json(ip, port, message, timeout=DEFAULT_TIMEOUT):
    """Serializa un dict a JSON y lo envia encuadrado."""
    return send_framed(ip, port, json.dumps(message), timeout)


def reply_framed(sock, body):
    """Escribe una respuesta sobre una conexion ya abierta.

    Se usa para HELLO_REPLY. Si el par ya cerro el socket porque espera la
    respuesta en una conexion nueva, el error se ignora.
    """
    try:
        sock.sendall(frame(body))
        return True
    except OSError:
        return False


def looks_like_bits(body):
    """Distingue DATA protegido con Hamming de un mensaje del plano de control.

    El contrato (seccion 6) define la regla sobre un mismo puerto TCP: un cuerpo
    que inicia con '{' es plano de control; un cuerpo de '0' y '1' es DATA
    protegido. Se exige contenido no vacio para que una trama vacia no se
    confunda con bits validos.
    """
    return bool(body) and set(body) <= {"0", "1"}
