"""Portability critic that rejects patch candidates likely to break other architectures.

The checks are plain text tests on the proposed replacement text, so they are cheap,
deterministic and run before any file is touched.
"""

from __future__ import annotations

import re

from ..schemas import PatchCandidate, RepairFamily


def portability_findings(candidate: PatchCandidate) -> list[str]:
    """List the portability problems found in a patch candidate.

    A candidate is flagged when it has no edits, its confidence is below 0.80, a
    conditional compiler-flag repair has no ``ifeq``/``ifneq``/``if(``/``#if`` guard,
    an SSE flag appears next to ``aarch64``, or a ``-dumpmachine`` triple is compared
    with ``$(filter x86_64,...)`` or ``$(filter amd64,...)``, which misses full triples
    such as ``x86_64-pc-linux-gnu``.

    Args:
        candidate: The proposed repair to inspect.

    Returns:
        One human-readable message per problem found. An empty list means the
        candidate passes.
    """
    findings: list[str] = []
    if not candidate.planned_edits:
        findings.append("candidate contains no semantically complete edit")
    if candidate.confidence < 0.80:
        findings.append("critical assumption confidence is below 0.80")
    replacement = "\n".join(edit.replacement.lower() for edit in candidate.planned_edits)
    if candidate.repair_family is RepairFamily.CONDITIONAL_COMPILER_FLAG:
        if not any(token in replacement for token in ("ifneq", "ifeq", "if(", "#if")):
            findings.append("architecture-specific flag is not guarded")
    if "-msse" in replacement and "aarch64" in replacement:
        findings.append("x86 SSE option appears enabled for aarch64")
    if "-dumpmachine" in replacement and re.search(
        r"\$\(\s*filter\s+(?:x86_64|amd64)\s*,", replacement
    ):
        findings.append("compiler target triple is compared to an exact architecture name")
    return findings


def passes_portability_critic(candidate: PatchCandidate) -> bool:
    """Run the portability checks and record their findings on the candidate.

    Args:
        candidate: The proposed repair. Its ``critic_findings`` field is overwritten
            with the result of ``portability_findings``.

    Returns:
        True when no portability problem was found, otherwise False.
    """
    candidate.critic_findings = portability_findings(candidate)
    return not candidate.critic_findings
