"""Pruebas de la capa Hamming y del calculo de rutas.

Ejecutar desde la raiz del proyecto:
    python tests/test_protocolo.py
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from hamming import Hamming74  # noqa: E402
from link_state import LinkState  # noqa: E402


def lsa(origin, sequence, links):
    """Construye una LSA con el formato del contrato de interoperabilidad."""
    return {
        "type": "LSA",
        "origin_router_id": origin,
        "sequence": sequence,
        "links": [
            {"neighbor_router_id": n, "cost": c} for n, c in links
        ],
        "from_router_id": origin,
    }


class TestHamming(unittest.TestCase):
    def test_ida_y_vuelta_sin_ruido(self):
        mensaje = json.dumps({"type": "DATA", "payload": {"command": "CARD"}})
        self.assertEqual(
            Hamming74.decode_message(Hamming74.encode_message(mensaje)), mensaje
        )

    def test_corrige_un_bit_en_cada_posicion(self):
        """Hamming (7,4) debe corregir un error en cualquiera de las 7 posiciones."""
        mensaje = "ABCD"
        codificado = Hamming74.encode_message(mensaje)
        for posicion in range(7):
            bits = list(codificado)
            bits[posicion] = "1" if bits[posicion] == "0" else "0"
            self.assertEqual(
                Hamming74.decode_message("".join(bits)),
                mensaje,
                f"no se corrigio el error en la posicion {posicion}",
            )

    def test_corrige_un_bit_por_bloque(self):
        mensaje = json.dumps({"command": "START_TRANSACTION", "payload": ""})
        bits = list(Hamming74.encode_message(mensaje))
        # Un error en el primer bit de cada bloque de 7.
        for inicio in range(0, len(bits) - 6, 7):
            bits[inicio] = "1" if bits[inicio] == "0" else "0"
        self.assertEqual(Hamming74.decode_message("".join(bits)), mensaje)

    def test_dos_errores_en_un_bloque_no_son_recuperables(self):
        """Limite conocido del codigo: con 2 errores por bloque la trama se corrompe.

        El router debe descartarla al no obtener un JSON valido, y esa es la razon
        por la que probabilidades de ruido altas producen perdidas.
        """
        mensaje = json.dumps({"type": "DATA", "packet_id": "p-001"})
        bits = list(Hamming74.encode_message(mensaje))
        bits[0] = "1" if bits[0] == "0" else "0"
        bits[1] = "1" if bits[1] == "0" else "0"
        self.assertNotEqual(Hamming74.decode_message("".join(bits)), mensaje)


class TestLinkState(unittest.TestCase):
    def test_lsdb_descarta_secuencias_viejas(self):
        ls = LinkState("U", [{"router_id": "V", "ip": "127.0.0.1", "port": 9002, "cost": 3}])
        self.assertTrue(ls.update_lsdb(lsa("V", 5, [("U", 3)])))
        self.assertFalse(ls.update_lsdb(lsa("V", 5, [("U", 3)])), "secuencia igual")
        self.assertFalse(ls.update_lsdb(lsa("V", 4, [("U", 3)])), "secuencia menor")
        self.assertTrue(ls.update_lsdb(lsa("V", 6, [("U", 3)])), "secuencia mayor")

    def test_ruta_mas_corta_elige_el_camino_optimo(self):
        """Topologia A..I del enunciado: A hacia G debe salir por C con costo 10."""
        vecinos = [
            {"router_id": "B", "ip": "127.0.0.1", "port": 9102, "cost": 7},
            {"router_id": "C", "ip": "127.0.0.1", "port": 9103, "cost": 2},
            {"router_id": "I", "ip": "127.0.0.1", "port": 9109, "cost": 1},
        ]
        ls = LinkState("A", vecinos, log=lambda _: None)
        for origen, enlaces in [
            ("A", [("B", 7), ("C", 2), ("I", 1)]),
            ("B", [("A", 7), ("F", 3)]),
            ("C", [("A", 2), ("D", 3)]),
            ("D", [("C", 3), ("I", 6), ("F", 2), ("E", 5)]),
            ("E", [("D", 5), ("G", 2)]),
            ("F", [("B", 3), ("D", 2), ("H", 4), ("G", 3)]),
            ("G", [("F", 3), ("E", 2)]),
            ("H", [("F", 4)]),
            ("I", [("A", 1), ("D", 6)]),
        ]:
            ls.update_lsdb(lsa(origen, 1, enlaces))
        ls.compute_shortest_paths()

        ruta_g = ls.lookup("G")
        self.assertIsNotNone(ruta_g)
        self.assertEqual(ruta_g["next_hop_router_id"], "C")
        self.assertEqual(ruta_g["total_cost"], 10)
        # El vecino directo mas barato sigue siendo un salto directo.
        self.assertEqual(ls.lookup("I")["next_hop_router_id"], "I")
        self.assertEqual(ls.lookup("H")["total_cost"], 11)

    def test_enlace_anunciado_por_un_solo_extremo_se_ignora(self):
        """Un enlace sin confirmacion bidireccional no debe usarse para rutear."""
        ls = LinkState(
            "U",
            [{"router_id": "V", "ip": "127.0.0.1", "port": 9002, "cost": 3}],
            log=lambda _: None,
        )
        ls.update_lsdb(lsa("U", 1, [("V", 3)]))
        ls.update_lsdb(lsa("V", 1, [("U", 3)]))
        # El nodo Z de otra pareja anuncia un enlace hacia U que U no anuncia.
        ls.update_lsdb(lsa("Z", 1, [("U", 1)]))
        ls.compute_shortest_paths()

        self.assertEqual(ls.lookup("V")["next_hop_router_id"], "V")
        self.assertIsNone(ls.lookup("Z"), "el enlace U-Z no esta confirmado")

    def test_vecino_no_configurado_no_rompe_el_calculo(self):
        """Regresion: un primer salto que no es vecino directo configurado
        lanzaba KeyError y derribaba el hilo de routing.

        Se simula una LSA malformada de otra implementacion que atribuye a U un
        enlace hacia Z, de modo que el enlace queda confirmado por ambos lados
        aunque Z no exista en la configuracion local de U.
        """
        ls = LinkState(
            "U",
            [{"router_id": "V", "ip": "127.0.0.1", "port": 9002, "cost": 3}],
            log=lambda _: None,
        )
        ls.update_lsdb(lsa("U", 1, [("V", 3), ("Z", 1)]))
        ls.update_lsdb(lsa("V", 1, [("U", 3)]))
        ls.update_lsdb(lsa("Z", 1, [("U", 1)]))

        ls.compute_shortest_paths()  # no debe lanzar excepcion

        self.assertEqual(ls.lookup("V")["next_hop_router_id"], "V")
        self.assertIsNone(ls.lookup("Z"), "Z no es un vecino directo configurado")

    def test_nodo_caido_deja_de_usarse_aunque_su_lsa_siga_en_la_lsdb(self):
        """Los vecinos dejan de anunciar al nodo caido y sus enlaces se invalidan.

        Comprueba el mecanismo que permite reconverger: la LSA obsoleta de F
        sigue almacenada, pero sus enlaces ya no estan confirmados por B, D ni G.
        """
        vecinos = [
            {"router_id": "B", "ip": "127.0.0.1", "port": 9102, "cost": 7},
            {"router_id": "C", "ip": "127.0.0.1", "port": 9103, "cost": 2},
            {"router_id": "I", "ip": "127.0.0.1", "port": 9109, "cost": 1},
        ]
        ls = LinkState("A", vecinos, log=lambda _: None)
        topologia = [
            ("A", [("B", 7), ("C", 2), ("I", 1)]),
            ("B", [("A", 7), ("F", 3)]),
            ("C", [("A", 2), ("D", 3)]),
            ("D", [("C", 3), ("I", 6), ("F", 2), ("E", 5)]),
            ("E", [("D", 5), ("G", 2)]),
            ("F", [("B", 3), ("D", 2), ("H", 4), ("G", 3)]),
            ("G", [("F", 3), ("E", 2)]),
            ("H", [("F", 4)]),
            ("I", [("A", 1), ("D", 6)]),
        ]
        for origen, enlaces in topologia:
            ls.update_lsdb(lsa(origen, 1, enlaces))
        ls.compute_shortest_paths()
        self.assertEqual(ls.lookup("G")["total_cost"], 10, "ruta optima via F")

        # F se cae: B, D, G y H lo retiran de sus LSAs con una secuencia mayor.
        # La LSA de F permanece en la LSDB, tal como ocurre en la red real.
        for origen, enlaces in [
            ("B", [("A", 7)]),
            ("D", [("C", 3), ("I", 6), ("E", 5)]),
            ("G", [("E", 2)]),
            ("H", []),
        ]:
            ls.update_lsdb(lsa(origen, 2, enlaces))
        ls.compute_shortest_paths()

        # Ahora la unica ruta hacia G es A-C-D-E-G, con costo 12.
        self.assertEqual(ls.lookup("G")["total_cost"], 12)
        self.assertEqual(ls.lookup("G")["next_hop_router_id"], "C")
        self.assertIsNone(ls.lookup("F"), "F esta caido")
        self.assertIsNone(ls.lookup("H"), "H solo era alcanzable por F")

    def test_costo_discrepante_se_resuelve_de_forma_determinista(self):
        avisos = []
        ls = LinkState(
            "U",
            [{"router_id": "V", "ip": "127.0.0.1", "port": 9002, "cost": 3}],
            log=avisos.append,
        )
        ls.update_lsdb(lsa("U", 1, [("V", 3)]))
        ls.update_lsdb(lsa("V", 1, [("U", 9)]))  # el otro extremo dice 9
        ls.compute_shortest_paths()

        self.assertEqual(ls.lookup("V")["total_cost"], 9)
        self.assertTrue(
            any("ERROR DE CONFIGURACION" in a for a in avisos),
            "la discrepancia de costo debe reportarse",
        )

    def test_destino_inalcanzable_no_aparece_en_la_tabla(self):
        ls = LinkState(
            "U",
            [{"router_id": "V", "ip": "127.0.0.1", "port": 9002, "cost": 3}],
            log=lambda _: None,
        )
        ls.update_lsdb(lsa("U", 1, [("V", 3)]))
        ls.update_lsdb(lsa("V", 1, [("U", 3)]))
        # Isla separada, sin enlace hacia nuestro componente.
        ls.update_lsdb(lsa("Y", 1, [("Z", 1)]))
        ls.update_lsdb(lsa("Z", 1, [("Y", 1)]))
        ls.compute_shortest_paths()

        self.assertIsNone(ls.lookup("Y"))
        self.assertIsNone(ls.lookup("Z"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
