"""Deterministic delta-graph traversal for causal context selection."""

from __future__ import annotations

from collections import deque

from .model import DeltaGraph, GraphEdge


def shortest_path(graph: DeltaGraph, source: str, target: str) -> list[GraphEdge]:
    """Return the shortest directed causal chain or an empty list when disconnected."""
    queue: deque[tuple[str, list[GraphEdge]]] = deque([(source, [])])
    seen = {source}
    while queue:
        node, path = queue.popleft()
        if node == target:
            return path
        for edge in graph.adjacency.get(node, []):
            if edge.target not in seen:
                seen.add(edge.target)
                queue.append((edge.target, path + [edge]))
    return []


def neighbors(graph: DeltaGraph, node_id: str, *, depth: int = 1) -> list[str]:
    seen = {node_id}
    frontier = {node_id}
    for _ in range(max(0, depth)):
        next_frontier = {edge.target for node in frontier for edge in graph.adjacency.get(node, [])}
        next_frontier -= seen
        seen |= next_frontier
        frontier = next_frontier
    seen.remove(node_id)
    return sorted(seen)
