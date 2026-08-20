"""Deterministic competition safety and anti-cheating gate."""

from __future__ import annotations

import re

from ..schemas import PatchCandidate
from ..errors import PathPolicyError
from ..workspace.path_policy import PathPolicy

_BYPASS_PATTERNS = (
    r"\bskip(?:_all)?\b", r"\bxfailed?\b", r"disable[_ -]?tests?",
    r"dummy (?:artifact|package)", r"/workspace/input", r"evaluator", r"hidden[-_ ]?case",
    r"docker\.sock", r"\bsudo\b", r"\bcurl\b", r"\bwget\b",
)


def policy_findings(candidate: PatchCandidate, path_policy: PathPolicy) -> list[str]:
    findings: list[str] = []
    for edit in candidate.planned_edits:
        try:
            path_policy.validate_relative(edit.path, creating=edit.operation == "create_file")
        except PathPolicyError as exc:
            findings.append(f"forbidden path {edit.path}: {exc}")
        lower_path = edit.path.lower()
        if edit.operation in {"delete", "delete_file"}:
            findings.append("file deletion is disabled")
        if "test" in lower_path and edit.replacement.strip() == "":
            findings.append("meaningful test deletion is forbidden")
        combined = f"{edit.path}\n{edit.replacement}\n{edit.rationale}"
        for pattern in _BYPASS_PATTERNS:
            if re.search(pattern, combined, re.I):
                findings.append(f"policy-sensitive pattern rejected: {pattern}")
    return sorted(set(findings))


def passes_policy_critic(candidate: PatchCandidate, path_policy: PathPolicy) -> bool:
    candidate.policy_findings = policy_findings(candidate, path_policy)
    return not candidate.policy_findings
