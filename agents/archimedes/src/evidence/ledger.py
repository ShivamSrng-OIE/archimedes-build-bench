"""The evidence ledger: an append-only record of evidence and claims for one run."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..schemas import Claim, ConsequenceLevel, EvidenceItem, EvidenceKind, JSONValue
from .confidence import fuse_confidence


@dataclass(slots=True)
class EvidenceLedger:
    """Evidence items and claims for one run, with stable sequential IDs.

    Items and claims are never removed. Claims are recalibrated whenever
    contradicting evidence is attached. The ledger is written to
    ``evidence-ledger.json`` at the end of the run.

    Attributes:
        evidence: Evidence items by ID, in the order they were added.
        claims: Claims by ID, in the order they were added.
    """

    evidence: dict[str, EvidenceItem] = field(default_factory=dict)
    claims: dict[str, Claim] = field(default_factory=dict)
    _evidence_counter: int = 0
    _claim_counter: int = 0

    def add_evidence(
        self,
        *,
        kind: EvidenceKind,
        summary: str,
        reliability: float,
        strength: float = 1.0,
        raw_excerpt: str = "",
        source: str = "",
        path: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
        stage: str = "",
    ) -> EvidenceItem:
        """Add one evidence item and give it the next ``evidence-NNN`` ID.

        Args:
            kind: Where the fact came from.
            summary: Short plain-text statement of the fact.
            reliability: Trust in the source; clamped to 0..0.99.
            strength: How strongly the fact bears on claims; clamped to 0..1.
            raw_excerpt: Supporting text; cut to 10,000 characters.
            source: Free-text description of the origin.
            path: Repository-relative file the fact refers to, if any.
            line_start: First line of the excerpt, if known.
            line_end: Last line of the excerpt, if known.
            stage: Controller stage recording the item.

        Returns:
            The stored evidence item.
        """
        self._evidence_counter += 1
        item = EvidenceItem(
            id=f"evidence-{self._evidence_counter:03d}",
            kind=kind,
            summary=summary,
            raw_excerpt=raw_excerpt[:10_000],
            source=source,
            path=path,
            line_start=line_start,
            line_end=line_end,
            reliability=min(0.99, max(0.0, reliability)),
            strength=min(1.0, max(0.0, strength)),
            created_during_stage=stage,
        )
        self.evidence[item.id] = item
        return item

    def add_claim(
        self,
        statement: str,
        category: str,
        *,
        supporting_evidence_ids: list[str] | None = None,
        contradicting_evidence_ids: list[str] | None = None,
        consequence: ConsequenceLevel = ConsequenceLevel.MEDIUM,
    ) -> Claim:
        """Add a claim, give it the next ``claim-NNN`` ID and compute its confidence.

        Args:
            statement: The belief in plain words.
            category: Short label grouping related claims.
            supporting_evidence_ids: IDs of evidence already in the ledger that
                support the claim.
            contradicting_evidence_ids: IDs of evidence already in the ledger
                that argue against it.
            consequence: How much damage acting on a wrong claim could do.

        Returns:
            The stored, calibrated claim.

        Raises:
            KeyError: If an evidence ID is not in the ledger.
        """
        self._claim_counter += 1
        claim = Claim(
            id=f"claim-{self._claim_counter:03d}",
            statement=statement,
            category=category,
            supporting_evidence_ids=list(supporting_evidence_ids or []),
            contradicting_evidence_ids=list(contradicting_evidence_ids or []),
            consequence_level=consequence,
        )
        self.claims[claim.id] = claim
        self.recalculate(claim.id)
        return claim

    def recalculate(self, claim_id: str) -> Claim:
        """Recompute a claim's confidence, band and status from its evidence.

        Args:
            claim_id: ID of the claim to update.

        Returns:
            The updated claim.

        Raises:
            KeyError: If the claim or one of its evidence IDs is unknown.
        """
        claim = self.claims[claim_id]
        supporting = [self.evidence[item_id] for item_id in claim.supporting_evidence_ids]
        contradicting = [self.evidence[item_id] for item_id in claim.contradicting_evidence_ids]
        claim.confidence, claim.confidence_band, claim.status = fuse_confidence(
            supporting, contradicting
        )
        return claim

    def contradict(self, claim_id: str, evidence_id: str) -> Claim:
        """Attach contradicting evidence to a claim and recalibrate it.

        Args:
            claim_id: ID of the claim.
            evidence_id: ID of the contradicting evidence; added only once.

        Returns:
            The updated claim.

        Raises:
            KeyError: If the claim or the evidence is unknown.
        """
        claim = self.claims[claim_id]
        if evidence_id not in claim.contradicting_evidence_ids:
            claim.contradicting_evidence_ids.append(evidence_id)
        return self.recalculate(claim_id)

    def to_dict(self) -> dict[str, JSONValue]:
        """Convert the ledger to a JSON-ready dictionary.

        Returns:
            Lists of all evidence items and all claims, in insertion order.
        """
        return {
            "evidence": [asdict(item) for item in self.evidence.values()],
            "claims": [asdict(claim) for claim in self.claims.values()],
        }
