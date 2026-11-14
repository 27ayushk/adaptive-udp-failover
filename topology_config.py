"""Port assignments for the original triangle and the second UDP client."""

from collections import deque

# Each tuple is (switch, switch port, switch, switch port).
LINKS = ((1, 2, 2, 1), (2, 2, 3, 2), (1, 3, 3, 3))
# Each host maps to its IPv4 address, access switch, and switch port.
HOSTS = {
    "hA": ("10.0.0.1", 1, 1),
    "hB": ("10.0.0.2", 3, 1),
    "hC": ("10.0.0.3", 1, 4),
}
SWITCHES = (1, 2, 3)
PRIMARY = (1, 3)


def find_path(graph, source, destination):
    queue = deque([(source,)])
    seen = {source}
    while queue:
        path = queue.popleft()
        if path[-1] == destination:
            return path
        for neighbor in sorted(graph.get(path[-1], {})):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((*path, neighbor))
    return ()


def configured_graph():
    graph = {switch: {} for switch in SWITCHES}
    for a, a_port, b, b_port in LINKS:
        graph[a][b] = a_port
        graph[b][a] = b_port
    return graph
