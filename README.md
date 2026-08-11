# Laboratorio 3 - Protocolos de Enrutamiento

Implementación del protocolo de enrutamiento Link State y simulación de transmisión de datos para una red bancaria (ATM y Banco). El desarrollo se divide en el Plano de Control (descubrimiento y rutas) y el Plano de Datos (transmisión, detección y corrección de errores).

## Arquitectura de Directorios

*   `config/`: Archivos JSON con la configuración inicial de los nodos, definiendo la topología (identificador, puertos, IP y vecinos).
*   `src/`: Código fuente de la infraestructura de red.
    *   `router.py`: Servidor TCP asíncrono para el manejo de los nodos.
    *   `link_state.py`: Lógica del algoritmo Flooding, LSA y cálculo de rutas mediante Dijkstra.
*   `data/`: Directorio autogenerado. Contiene `nodo_tabla_enrutamiento.csv` con las rutas óptimas resultantes.

## Instrucciones de Ejecución (Plano de Control)

Para inicializar la topología y verificar el descubrimiento de la red, ejecute los routers en terminales independientes desde la raíz del proyecto.

1.  **Inicializar Router U (Gateway ATM):**
    ```bash
    python src/router.py config/router_u.json
    ```

2.  **Inicializar Router V (Intermedio):**
    ```bash
    python src/router.py config/router_v.json
    ```

3.  **Inicializar Router X (Gateway Banco):**
    ```bash
    python src/router.py config/router_x.json
    ```

Una vez que los nodos se encuentren en ejecución y los algoritmos converjan, se generará el archivo `data/nodo_tabla_enrutamiento.csv` con las tablas de enrutamiento calculadas.
