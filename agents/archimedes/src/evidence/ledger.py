"""Per-case append-only evidence and claim registry with stable identifiers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..schemas import Claim, ConsequenceLevel, EvidenceItem, EvidenceKind, JSONValue
from .confidence import fuse_confidence


@dataclass(slots=True)
class EvidenceLedger:
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
        """Append bounded evidence with a deterministic per-case identifier."""
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
        """Add and immediately calibrate a claim from registered evidence."""
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
        claim = self.claims[claim_id]
        supporting = [self.evidence[item_id] for item_id in claim.supporting_evidence_ids]
        contradicting = [self.evidence[item_id] for item_id in claim.contradicting_evidence_ids]
        claim.confidence, claim.confidence_band, claim.status = fuse_confidence(
            supporting, contradicting
        )
        return claim

    def contradict(self, claim_id: str, evidence_id: str) -> Claim:
        claim = self.claims[claim_id]
        if evidence_id not in claim.contradicting_evidence_ids:
            claim.contradicting_evidence_ids.append(evidence_id)
        return self.recalculate(claim_id)

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "evidence": [asdict(item) for item in self.evidence.values()],
            "claims": [asdict(claim) for claim in self.claims.values()],
        }
