"""Build an initial source-target causal delta from failure and hazard evidence."""

from __future__ import annotations

from ..architecture.hazard_scanner import ArchitectureHazard
from ..schemas import CaseData, FailureAnalysis
from .model import DeltaGraph, EdgeType, GraphEdge, GraphNode, NodeType

_HAZARD_NODES = {
    "compiler_flag": NodeType.COMPILER_FLAG,
    "architecture_macro": NodeType.ARCH_GUARD,
    "packaging_conditional": NodeType.PACKAGE_RULE,
    "intrinsic_header": NodeType.INTRINSIC,
    "inline_assembly": NodeType.ASM_BLOCK,
    "abi_assumption": NodeType.ABI_ASSUMPTION,
}


def build_delta_graph(
    case: CaseData, failure: FailureAnalysis, hazards: list[ArchitectureHazard]
) -> DeltaGraph:
    """Connect observed failure tokens to repository architecture assumptions."""
    graph = DeltaGraph()
    failure_node = GraphNode("failure-001", NodeType.FAILURE, failure.primary_diagnostic)
    graph.add_node(failure_node)
    for index, hazard in enumerate(hazards, 1):
        file_id = f"file-{index:03d}"
        hazard_id = f"hazard-{index:03d}"
        graph.add_node(GraphNode(file_id, NodeType.FILE, hazard.path))
        graph.add_node(GraphNode(hazard_id, _HAZARD_NODES[hazard.category], hazard.matched_text, {"line": str(hazard.line)}))
        graph.add_edge(GraphEdge(file_id, hazard_id, EdgeType.DECLARES, hazard.confidence))
        if hazard.matched_text.lower() in failure.primary_diagnostic.lower():
            graph.add_edge(GraphEdge("failure-001", hazard_id, EdgeType.TRACES_TO, 0.96))
            graph.add_edge(GraphEdge(hazard_id, "failure-001", EdgeType.INVALID_ON_TARGET, 0.90, target_validity=case.target_architecture.canonical.value))
    return graph
