import csv
import heapq
import os

class LinkState:
    def __init__(self, router_id, neighbors_config):
        self.router_id = router_id
        self.neighbors_config = {n["router_id"]: n for n in neighbors_config}
        self.lsdb = {}

    def update_lsdb(self, lsa_message):
        origin = lsa_message["origin_router_id"]
        seq = lsa_message["sequence"]
        
        # Se conserva la secuencia más alta recibida por cada origin_router_id
        if origin not in self.lsdb or seq > self.lsdb[origin]["sequence"]:
            self.lsdb[origin] = lsa_message
            return True
        return False

    def compute_shortest_paths(self):
        graph = {}
        for node, data in self.lsdb.items():
            if node not in graph:
                graph[node] = {}
            for link in data["links"]:
                neighbor = link["neighbor_router_id"]
                cost = link["cost"]
                graph[node][neighbor] = cost
                if neighbor not in graph:
                    graph[neighbor] = {}
                graph[neighbor][node] = cost

        distances = {node: float('inf') for node in graph}
        distances[self.router_id] = 0
        previous = {node: None for node in graph}
        pq = [(0, self.router_id)]

        while pq:
            current_dist, current_node = heapq.heappop(pq)
            if current_dist > distances[current_node]:
                continue
            for neighbor, weight in graph.get(current_node, {}).items():
                distance = current_dist + weight
                if distance < distances[neighbor]:
                    distances[neighbor] = distance
                    previous[neighbor] = current_node
                    heapq.heappush(pq, (distance, neighbor))

        self._generate_csv(distances, previous)

    def _generate_csv(self, distances, previous):
        os.makedirs('data', exist_ok=True)
        filepath = 'data/nodo_tabla_enrutamiento.csv'
        
        with open(filepath, mode='w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(["destination_router_id", "next_hop_router_id", "next_hop_ip", "next_hop_port", "total_cost"])
            
            for dest in distances:
                if dest == self.router_id or distances[dest] == float('inf'):
                    continue
                    
                step = dest
                while previous[step] != self.router_id:
                    step = previous[step]
                next_hop = step
                
                next_hop_ip = self.neighbors_config[next_hop]["ip"]
                next_hop_port = self.neighbors_config[next_hop]["port"]
                
                writer.writerow([dest, next_hop, next_hop_ip, next_hop_port, distances[dest]])