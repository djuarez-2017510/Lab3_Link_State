# Laboratorio 3 - Protocolos de enrutamiento
#
# Equivalente POSIX de run_local.ps1. En Windows use el script de PowerShell.
#
#   make venv       crea el entorno virtual
#   make test       ejecuta las pruebas de Hamming y Dijkstra
#   make simple     levanta la topologia U-V-X en segundo plano
#   make ejemplo    levanta la topologia A..I en segundo plano
#   make atm        abre el ATM en primer plano (interactivo)
#   make stop       detiene todos los nodos
#   make clean      borra tablas generadas y cache de Python

PYTHON  ?= python3
VENV    := .venv
CONFIG  := config
EJEMPLO := config/topologia_ejemplo
LOGS    := data/logs

.PHONY: venv test simple ejemplo atm stop clean

venv:
	$(PYTHON) -m venv $(VENV)
	@echo "Active el entorno con: source $(VENV)/bin/activate"
	@echo "El proyecto solo usa la biblioteca estandar; no hay que instalar nada."

test:
	$(PYTHON) tests/test_protocolo.py

simple:
	@mkdir -p $(LOGS)
	@$(PYTHON) -u src/bank_server.py $(CONFIG)/host_bank.json > $(LOGS)/bank.log 2>&1 &
	@for n in u v x; do \
		$(PYTHON) -u src/router.py $(CONFIG)/router_$$n.json > $(LOGS)/$$n.log 2>&1 & \
	done
	@echo "Banco y routers U, V, X levantados. Logs en $(LOGS)/"
	@echo "Espere unos segundos y ejecute: make atm"

ejemplo:
	@mkdir -p $(LOGS)
	@$(PYTHON) -u src/bank_server.py $(EJEMPLO)/host_bank.json > $(LOGS)/bank.log 2>&1 &
	@for n in a b c d e f g h i; do \
		$(PYTHON) -u src/router.py $(EJEMPLO)/router_$$n.json > $(LOGS)/$$n.log 2>&1 & \
	done
	@echo "Banco y routers A..I levantados. Logs en $(LOGS)/"
	@echo "Espere unos segundos y ejecute: make atm CONFIG=$(EJEMPLO)"

atm:
	$(PYTHON) -u src/atm_client.py $(CONFIG)/host_atm.json

stop:
	-@pkill -f "src/router.py" 2>/dev/null || true
	-@pkill -f "src/bank_server.py" 2>/dev/null || true
	@echo "Nodos detenidos."

clean:
	rm -rf data src/__pycache__ tests/__pycache__
	@echo "Tablas y cache eliminados."
