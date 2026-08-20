"""Typed heterogeneous graph with deterministic adjacency lists."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class NodeType(str, Enum):
    FILE = "file"
    BUILD_RULE = "build_rule"
    PACKAGE_RULE = "package_rule"
    COMPILER_FLAG = "compiler_flag"
    DEPENDENCY = "dependency"
    SOURCE_SYMBOL = "source_symbol"
    ARCH_GUARD = "arch_guard"
    INTRINSIC = "intrinsic"
    ASM_BLOCK = "asm_block"
    ABI_ASSUMPTION = "abi_assumption"
    TEST = "test"
    ARTIFACT = "artifact"
    FAILURE = "failure"
    SEMANTIC_ANCHOR = "semantic_anchor"
    CLAIM = "claim"


class EdgeType(str, Enum):
    DECLARES = "declares"
    INJECTS = "injects"
    COMPILES = "compiles"
    LINKS = "links"
    DEPENDS_ON = "depends_on"
    GUARDED_BY = "guarded_by"
    PRODUCES = "produces"
    FAILS_AT = "fails_at"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    ALTERNATIVE_TO = "alternative_to"
    VALID_ON_SOURCE = "valid_on_source"
    INVALID_ON_TARGET = "invalid_on_target"
    TRACES_TO = "traces_to"


@dataclass(frozen=True, slots=True)
class GraphNode:
    id: str
    type: NodeType
    label: str
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source: str
    target: str
    type: EdgeType
    confidence: float
    evidence_ids: tuple[str, ...] = ()
    source_validity: str = "unknown"
    target_validity: str = "unknown"


@dataclass(slots=True)
class DeltaGraph:
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    adjacency: dict[str, list[GraphEdge]] = field(default_factory=dict)

    def add_node(self, node: GraphNode) -> None:
        self.nodes[node.id] = node
        self.adjacency.setdefault(node.id, [])

    def add_edge(self, edge: GraphEdge) -> None:
        if edge.source not in self.nodes or edge.target not in self.nodes:
            raise ValueError("both edge endpoints must exist")
        if edge not in self.adjacency.setdefault(edge.source, []):
            self.adjacency[edge.source].append(edge)
            self.adjacency[edge.source].sort(key=lambda item: (item.target, item.type.value))
