"""Node, edge and graph types for the architecture delta graph.

Adjacency lists are kept sorted so the graph is the same on every run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class NodeType(str, Enum):
    """What a graph node stands for.

    Members cover repository objects (files, build and package rules, compiler
    flags, dependencies, source symbols, tests, artifacts), the hazard kinds
    (architecture guards, intrinsics, assembly blocks, ABI assumptions), and
    reasoning objects (the failure, semantic anchors and claims). The builder
    currently creates file, failure and hazard nodes.
    """

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
    """How two graph nodes are related.

    Members describe build relations (declares, injects, compiles, links,
    depends on, guarded by, produces, fails at), evidence relations (supports,
    contradicts, alternative to) and architecture validity (valid on source,
    invalid on target, traces to). The builder currently creates
    ``DECLARES``, ``TRACES_TO`` and ``INVALID_ON_TARGET`` edges.
    """

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
    """One node of the delta graph.

    Attributes:
        id: Unique node ID such as ``hazard-003``.
        type: What the node stands for.
        label: Human-readable text, such as a path or a matched flag.
        attributes: Extra string properties, such as ``line``.
    """

    id: str
    type: NodeType
    label: str
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """One directed, typed edge of the delta graph.

    Attributes:
        source: ID of the node the edge starts at.
        target: ID of the node the edge points to.
        type: How the two nodes are related.
        confidence: Confidence in the relation, from 0 to 1.
        evidence_ids: Ledger IDs of evidence behind the relation.
        source_validity: Whether the relation holds on the source
            architecture, or ``unknown``.
        target_validity: Whether it holds on the target architecture, or the
            target name it was found invalid on, or ``unknown``.
    """

    source: str
    target: str
    type: EdgeType
    confidence: float
    evidence_ids: tuple[str, ...] = ()
    source_validity: str = "unknown"
    target_validity: str = "unknown"


@dataclass(slots=True)
class DeltaGraph:
    """A directed graph stored as nodes plus sorted outgoing-edge lists.

    Attributes:
        nodes: Nodes by ID.
        adjacency: Outgoing edges per node ID, sorted by target ID and edge
            type.
    """

    nodes: dict[str, GraphNode] = field(default_factory=dict)
    adjacency: dict[str, list[GraphEdge]] = field(default_factory=dict)

    def add_node(self, node: GraphNode) -> None:
        """Add a node, replacing any node with the same ID.

        Args:
            node: The node to add.

        Returns:
            None.
        """
        self.nodes[node.id] = node
        self.adjacency.setdefault(node.id, [])

    def add_edge(self, edge: GraphEdge) -> None:
        """Add an edge unless an identical one already exists.

        Args:
            edge: The edge to add; both endpoints must already be nodes.

        Returns:
            None.

        Raises:
            ValueError: If either endpoint is not in the graph.
        """
        if edge.source not in self.nodes or edge.target not in self.nodes:
            raise ValueError("both edge endpoints must exist")
        if edge not in self.adjacency.setdefault(edge.source, []):
            self.adjacency[edge.source].append(edge)
            self.adjacency[edge.source].sort(key=lambda item: (item.target, item.type.value))
