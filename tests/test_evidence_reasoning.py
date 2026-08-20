from __future__ import annotations

import unittest

from src.evidence.confidence import claim_justifies_patch, fuse_confidence
from src.evidence.ledger import EvidenceLedger
from src.reasoning.hypotheses import generate_initial_hypotheses
from src.reasoning.probe_planner import information_value, rank_probes
from src.schemas import (
    ClaimStatus,
    ConsequenceLevel,
    EvidenceItem,
    EvidenceKind,
    FailureAnalysis,
    FailureFamily,
    FailureFrontier,
    FrontierStage,
    Probe,
)


class EvidenceReasoningTests(unittest.TestCase):
    def test_llm_only_confidence_is_capped(self) -> None:
        llm = EvidenceItem("evidence-001", EvidenceKind.LLM_INFERENCE, "model says so", reliability=0.99)
        confidence, _, status = fuse_confidence([llm], [])
        self.assertEqual(confidence, 0.65)
        self.assertNotEqual(status, ClaimStatus.CONFIRMED)

    def test_non_llm_direct_evidence_can_confirm(self) -> None:
        direct = EvidenceItem("evidence-001", EvidenceKind.DIRECT_FILE_OBSERVATION, "exact", reliability=0.98)
        confidence, _, status = fuse_confidence([direct], [])
        self.assertGreaterEqual(confidence, 0.90)
        self.assertEqual(status, ClaimStatus.CONFIRMED)

    def test_direct_contradiction_prevents_confirmation(self) -> None:
        support = EvidenceItem("evidence-001", EvidenceKind.STATIC_ANALYSIS, "pattern", reliability=0.72)
        contradiction = EvidenceItem("evidence-002", EvidenceKind.TOOL_OUTPUT, "tool refutes", reliability=0.95)
        _, _, status = fuse_confidence([support], [contradiction])
        self.assertEqual(status, ClaimStatus.CONTRADICTED)

    def test_ledger_recalculates_contradictions(self) -> None:
        ledger = EvidenceLedger()
        support = ledger.add_evidence(kind=EvidenceKind.DIRECT_FILE_OBSERVATION, summary="support", reliability=0.98)
        claim = ledger.add_claim("flag is here", "provenance", supporting_evidence_ids=[support.id])
        self.assertEqual(claim.status, ClaimStatus.CONFIRMED)
        contrary = ledger.add_evidence(kind=EvidenceKind.TOOL_OUTPUT, summary="contrary", reliability=0.99)
        ledger.contradict(claim.id, contrary.id)
        self.assertNotEqual(claim.status, ClaimStatus.CONFIRMED)

    def test_high_consequence_decision_threshold(self) -> None:
        self.assertFalse(claim_justifies_patch(0.79, ConsequenceLevel.HIGH, ClaimStatus.SUPPORTED))
        self.assertTrue(claim_justifies_patch(0.80, ConsequenceLevel.HIGH, ClaimStatus.SUPPORTED))
        self.assertFalse(claim_justifies_patch(0.99, ConsequenceLevel.LOW, ClaimStatus.CONTRADICTED))

    def test_atomic_link_failure_yields_real_alternatives(self) -> None:
        failure = FailureAnalysis(
            "", "undefined reference to __atomic_compare_exchange_16", FailureFamily.LINKING, [],
            FailureFrontier(FrontierStage.LINK, "atomic"),
        )
        hypotheses = generate_initial_hypotheses(failure, [])
        self.assertEqual(len(hypotheses.hypotheses), 3)
        self.assertEqual(round(sum(item.posterior_score for item in hypotheses.hypotheses), 6), 1.0)

    def test_probe_information_value_and_ranking(self) -> None:
        cheap_value = information_value(
            hypothesis_mass=0.8, discrimination_strength=0.9,
            consequence=ConsequenceLevel.HIGH, normalized_cost=0.2,
        )
        expensive_value = information_value(
            hypothesis_mass=0.8, discrimination_strength=0.9,
            consequence=ConsequenceLevel.HIGH, normalized_cost=1.0,
        )
        probes = [
            Probe("probe-002", "compile_only", "expensive", estimated_information_gain=expensive_value, estimated_cost=1.0),
            Probe("probe-001", "repository_search", "cheap", estimated_information_gain=cheap_value, estimated_cost=0.2),
        ]
        self.assertEqual(rank_probes(probes)[0].id, "probe-001")


if __name__ == "__main__":
    unittest.main()
