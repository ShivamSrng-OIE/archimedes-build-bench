"""Tests for confidence fusion, the evidence ledger and initial hypothesis generation."""

from __future__ import annotations

import unittest

from src.evidence.confidence import claim_justifies_patch, fuse_confidence
from src.evidence.ledger import EvidenceLedger
from src.reasoning.hypotheses import generate_initial_hypotheses
from src.schemas import (
    ClaimStatus,
    ConsequenceLevel,
    EvidenceItem,
    EvidenceKind,
    FailureAnalysis,
    FailureFamily,
    FailureFrontier,
    FrontierStage,
)


class EvidenceReasoningTests(unittest.TestCase):
    """Tests for how evidence turns into claim confidence and repair decisions."""

    def test_llm_only_confidence_is_capped(self) -> None:
        """Check that a claim backed only by model inference is capped at 0.65 confidence and never
        confirmed, however reliable the inference is said to be.
        """
        llm = EvidenceItem("evidence-001", EvidenceKind.LLM_INFERENCE, "model says so", reliability=0.99)
        confidence, _, status = fuse_confidence([llm], [])
        self.assertEqual(confidence, 0.65)
        self.assertNotEqual(status, ClaimStatus.CONFIRMED)

    def test_non_llm_direct_evidence_can_confirm(self) -> None:
        """Check that one highly reliable direct file observation is enough to confirm a claim with
        confidence of at least 0.90.
        """
        direct = EvidenceItem("evidence-001", EvidenceKind.DIRECT_FILE_OBSERVATION, "exact", reliability=0.98)
        confidence, _, status = fuse_confidence([direct], [])
        self.assertGreaterEqual(confidence, 0.90)
        self.assertEqual(status, ClaimStatus.CONFIRMED)

    def test_direct_contradiction_prevents_confirmation(self) -> None:
        """Check that strong contradicting tool output marks a claim as contradicted even when
        supporting evidence exists.
        """
        support = EvidenceItem("evidence-001", EvidenceKind.STATIC_ANALYSIS, "pattern", reliability=0.72)
        contradiction = EvidenceItem("evidence-002", EvidenceKind.TOOL_OUTPUT, "tool refutes", reliability=0.95)
        _, _, status = fuse_confidence([support], [contradiction])
        self.assertEqual(status, ClaimStatus.CONTRADICTED)

    def test_ledger_recalculates_contradictions(self) -> None:
        """Check that adding a contradiction to a confirmed claim in ``EvidenceLedger`` updates the
        claim's status right away.
        """
        ledger = EvidenceLedger()
        support = ledger.add_evidence(kind=EvidenceKind.DIRECT_FILE_OBSERVATION, summary="support", reliability=0.98)
        claim = ledger.add_claim("flag is here", "provenance", supporting_evidence_ids=[support.id])
        self.assertEqual(claim.status, ClaimStatus.CONFIRMED)
        contrary = ledger.add_evidence(kind=EvidenceKind.TOOL_OUTPUT, summary="contrary", reliability=0.99)
        ledger.contradict(claim.id, contrary.id)
        self.assertNotEqual(claim.status, ClaimStatus.CONFIRMED)

    def test_high_consequence_decision_threshold(self) -> None:
        """Check that a high-consequence patch needs confidence of at least 0.80, and that a
        contradicted claim never justifies a patch, whatever its confidence.
        """
        self.assertFalse(claim_justifies_patch(0.79, ConsequenceLevel.HIGH, ClaimStatus.SUPPORTED))
        self.assertTrue(claim_justifies_patch(0.80, ConsequenceLevel.HIGH, ClaimStatus.SUPPORTED))
        self.assertFalse(claim_justifies_patch(0.99, ConsequenceLevel.LOW, ClaimStatus.CONTRADICTED))

    def test_atomic_link_failure_yields_real_alternatives(self) -> None:
        """Check that an undefined ``__atomic_compare_exchange_16`` at link time yields three
        competing hypotheses whose scores add up to 1.
        """
        failure = FailureAnalysis(
            "", "undefined reference to __atomic_compare_exchange_16", FailureFamily.LINKING, [],
            FailureFrontier(FrontierStage.LINK, "atomic"),
        )
        hypotheses = generate_initial_hypotheses(failure, [])
        self.assertEqual(len(hypotheses.hypotheses), 3)
        self.assertEqual(round(sum(item.posterior_score for item in hypotheses.hypotheses), 6), 1.0)



if __name__ == "__main__":
    unittest.main()
