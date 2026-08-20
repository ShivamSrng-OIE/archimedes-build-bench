"""Portability and semantic-preservation review for patch candidates."""

from __future__ import annotations

from ..schemas import PatchCandidate, RepairFamily


def portability_findings(candidate: PatchCandidate) -> list[str]:
    findings: list[str] = []
    if not candidate.planned_edits:
        findings.append("candidate contains no semantically complete edit")
    if candidate.confidence < 0.80:
        findings.append("critical assumption confidence is below 0.80")
    replacement = "\n".join(edit.replacement.lower() for edit in candidate.planned_edits)
    if candidate.repair_family is RepairFamily.CONDITIONAL_COMPILER_FLAG:
        if not any(token in replacement for token in ("ifneq", "if(", "#if")):
            findings.append("architecture-specific flag is not guarded")
    if "-msse" in replacement and "aarch64" in replacement:
        findings.append("x86 SSE option appears enabled for aarch64")
    return findings


def passes_portability_critic(candidate: PatchCandidate) -> bool:
    candidate.critic_findings = portability_findings(candidate)
    return not candidate.critic_findings
