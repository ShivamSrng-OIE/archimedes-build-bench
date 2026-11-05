"""Competition-policy critic that rejects unsafe or test-bypassing patch candidates.

It checks every planned edit against the path policy and a fixed list of patterns that
point to skipped tests, fake artifacts, network access or attempts to reach the
evaluator, so a candidate that would break the competition rules is never applied.
"""

from __future__ import annotations

import re

from ..errors import PathPolicyError
from ..schemas import PatchCandidate
from ..workspace.path_policy import PathPolicy

_BYPASS_PATTERNS = (
    r"\bskip(?:_all)?\b", r"\bxfailed?\b", r"disable[_ -]?tests?",
    r"dummy (?:artifact|package)", r"/workspace/input", r"evaluator", r"hidden[-_ ]?case",
    r"docker\.sock", r"\bsudo\b", r"\bcurl\b", r"\bwget\b",
)


def policy_findings(candidate: PatchCandidate, path_policy: PathPolicy) -> list[str]:
    """List the competition-policy violations in a patch candidate.

    For each edit it checks that the path policy accepts the path, that the edit
    does not delete a file, that it does not empty a file whose path contains
    ``test``, and that none of the ``_BYPASS_PATTERNS`` (``skip``, ``xfail``,
    ``disable tests``, ``docker.sock``, ``sudo``, ``curl`` and so on) appears in the
    path, replacement text or rationale. Matching is case-insensitive.

    Args:
        candidate: The proposed repair to inspect.
        path_policy: Decides which repository paths may be edited or created.

    Returns:
        The sorted, de-duplicated violation messages. An empty list means the
        candidate passes.
    """
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
            if re.search(pattern, combined, re.IGNORECASE):
                findings.append(f"policy-sensitive pattern rejected: {pattern}")
    return sorted(set(findings))


def passes_policy_critic(candidate: PatchCandidate, path_policy: PathPolicy) -> bool:
    """Run the policy checks and record their findings on the candidate.

    Args:
        candidate: The proposed repair. Its ``policy_findings`` field is overwritten
            with the result of ``policy_findings``.
        path_policy: Decides which repository paths may be edited or created.

    Returns:
        True when no policy violation was found, otherwise False.
    """
    candidate.policy_findings = policy_findings(candidate, path_policy)
    return not candidate.policy_findings
