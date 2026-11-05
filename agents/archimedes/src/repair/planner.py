"""Wrap rule matches, conditional flag repairs and model plans into patch candidates.

Each builder returns a ``PatchCandidate`` with fixed scoring weights for the candidate
ranker. Model plans get the most checking, because nothing about them is trusted until
their edits have been matched against the current file bytes.
"""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath

from ..architecture.hazard_scanner import ArchitectureHazard
from ..errors import PathPolicyError
from ..schemas import (
    Architecture,
    CaseData,
    FrontierStage,
    JSONValue,
    PatchCandidate,
    PlannedEdit,
    RepairFamily,
)
from ..workspace.path_policy import PathPolicy
from .deterministic import DeterministicMatch


def _makefile_condition(line: str, target: Architecture) -> str:
    """Wrap a Makefile line so it only takes effect when the compiler targets x86_64.

    The guard asks the compiler for its target triple with ``$(CC) -dumpmachine`` and
    matches it with the ``x86_64%`` and ``amd64%`` patterns, so full triples such as
    ``x86_64-pc-linux-gnu`` match too.

    Args:
        line: The original Makefile line that sets the x86-only flag.
        target: The Case's target architecture. It is not used, because the guard
            depends on what the compiler supports, not on one observed target.

    Returns:
        The replacement text: the triple variable, an ``ifneq`` guard, the original
        line and ``endif``.
    """
    del target  # The guard describes flag support, not the currently observed target alone.
    return (
        "ARCHIMEDES_TARGET_TRIPLE ?= $(shell $(CC) -dumpmachine 2>/dev/null)\n"
        "ifneq (,$(filter x86_64% amd64%,$(ARCHIMEDES_TARGET_TRIPLE)))\n"
        f"{line}\n"
        "endif"
    )


def _cmake_condition(line: str) -> str:
    """Wrap a CMake line so it only runs when ``CMAKE_SYSTEM_PROCESSOR`` is x86_64.

    Args:
        line: The original CMake line that sets the x86-only flag.

    Returns:
        The replacement text: an ``if(CMAKE_SYSTEM_PROCESSOR MATCHES ...)`` block
        holding the stripped original line, closed by ``endif()``.
    """
    return (
        "if(CMAKE_SYSTEM_PROCESSOR MATCHES \"^(x86_64|AMD64|amd64)$\")\n"
        f"  {line.strip()}\n"
        "endif()"
    )


def conditional_flag_candidate(
    case: CaseData,
    hazard: ArchitectureHazard,
    *,
    claim_ids: list[str],
    confidence: float,
) -> PatchCandidate | None:
    """Build a candidate that puts an x86-only compiler flag behind an architecture guard.

    Only Makefiles (``Makefile``, ``Makefile.*``, ``*.mk``) and CMake files
    (``CMakeLists.txt``, ``*.cmake``) are handled. The hazard's excerpt is used as the
    expected text, so the edit applies only if that exact line is still in the file.

    Args:
        case: The loaded Case, used for its target architecture.
        hazard: The architecture hazard that found the flag in a build file.
        claim_ids: IDs of the evidence claims that justify the edit.
        confidence: Confidence of the critical claims, copied onto the candidate.

    Returns:
        The candidate, or None when the hazard is not in a recognized Makefile or
        CMake file.
    """
    path = PurePosixPath(hazard.path)
    name = path.name.lower()
    original = hazard.excerpt
    if name == "makefile" or name.startswith("makefile.") or path.suffix.lower() == ".mk":
        replacement = _makefile_condition(original, case.target_architecture.canonical)
    elif name == "cmakelists.txt" or path.suffix.lower() == ".cmake":
        replacement = _cmake_condition(original)
    else:
        return None
    edit = PlannedEdit(
        operation="replace_lines",
        path=hazard.path,
        expected_text=original,
        replacement=replacement,
        rationale=(
            f"restrict {hazard.matched_text} to the architecture family where it is valid; "
            "preserve the source optimization and target portable path"
        ),
        claim_dependencies=claim_ids,
    )
    components = {
        "critical_claim_confidence": confidence,
        "causal_coverage": 0.98,
        "semantic_preservation": 0.86,
        "expected_frontier_gain": 0.85,
        "source_preservation": 0.90,
        "rule_support": 0.90,
        "minimality": 0.88,
    }
    return PatchCandidate(
        id="candidate-001",
        repair_family=RepairFamily.CONDITIONAL_COMPILER_FLAG,
        rationale=edit.rationale,
        assumption_claim_ids=claim_ids,
        planned_edits=[edit],
        confidence=confidence,
        risk=0.08,
        expected_frontier=FrontierStage.COMPILE,
        expected_information_gain_if_built=0.80,
        score_components=components,
    )


def deterministic_match_candidate(
    match: DeterministicMatch,
    *,
    claim_ids: list[str],
    confidence: float,
    current_frontier: FrontierStage,
) -> PatchCandidate:
    """Turn a deterministic rule match into a patch candidate for the critics.

    The candidate's confidence is the lower of the claim confidence and the rule's
    own confidence. The expected frontier is the stage after the current one, kept
    between ``ENVIRONMENT`` and ``SUCCESS``.

    Args:
        match: The matched rule with its exact-text edits. Each edit's
            ``claim_dependencies`` is overwritten with ``claim_ids``.
        claim_ids: IDs of the evidence claims that justify the edit.
        confidence: Confidence of the critical claims.
        current_frontier: The stage where the current build fails.

    Returns:
        The candidate with id ``candidate-001``.
    """
    for edit in match.edits:
        edit.claim_dependencies = list(claim_ids)
    expected_frontier = FrontierStage(
        min(int(FrontierStage.SUCCESS), max(int(FrontierStage.ENVIRONMENT), int(current_frontier) + 1))
    )
    return PatchCandidate(
        id="candidate-001",
        repair_family=match.family,
        rationale=match.rationale,
        assumption_claim_ids=claim_ids,
        planned_edits=list(match.edits),
        confidence=min(confidence, match.confidence),
        risk=match.risk,
        expected_frontier=expected_frontier,
        expected_information_gain_if_built=0.80,
        score_components={
            "critical_claim_confidence": min(confidence, match.confidence),
            "causal_coverage": 0.92,
            "semantic_preservation": 0.88,
            "expected_frontier_gain": 0.82,
            "source_preservation": 0.86,
            "rule_support": 0.94,
            "minimality": 0.88,
        },
    )


def model_plan_candidate(
    data: dict[str, JSONValue],
    *,
    path_policy: PathPolicy,
    current_frontier: FrontierStage,
) -> PatchCandidate | None:
    """Check a model's repair plan and turn it into a patch candidate if it holds up.

    The plan must name a real repair family (not ``unknown`` or ``no_safe_fix``), give
    a rationale and a non-empty list of assumptions, and contain 1 to 5 edits. Each
    edit must use ``replace_lines``, ``insert_before``, ``insert_after`` or
    ``create_file``, pass the path policy, and stay within 48,000 characters (64,000
    for all replacements together). For edits to existing files, the expected text
    must occur exactly once in the current UTF-8, non-binary file, and the file's
    SHA-256 is recorded so the executor refuses the edit if the file changes in
    between. Files to be created must not exist yet.

    Args:
        data: The parsed JSON answer from the patch-planning model call.
        path_policy: Decides which repository paths may be edited or created.
        current_frontier: The stage where the current build fails.

    Returns:
        The candidate with id ``candidate-model-001`` and a fixed confidence of 0.80,
        or None if any part of the plan fails a check.
    """
    family_value = data.get("repair_family")
    rationale_value = data.get("rationale")
    assumptions = data.get("assumptions")
    raw_edits = data.get("edits")
    try:
        family = RepairFamily(str(family_value))
    except ValueError:
        return None
    if family in {RepairFamily.UNKNOWN, RepairFamily.NO_SAFE_FIX}:
        return None
    if not isinstance(rationale_value, str) or not rationale_value.strip():
        return None
    if not isinstance(assumptions, list) or not assumptions or not all(
        isinstance(item, str) and item.strip() for item in assumptions
    ):
        return None
    if not isinstance(raw_edits, list) or not 1 <= len(raw_edits) <= 5:
        return None
    edits: list[PlannedEdit] = []
    total_replacement = 0
    for raw in raw_edits:
        if not isinstance(raw, dict):
            return None
        operation = raw.get("operation")
        proposed_path = raw.get("path")
        replacement = raw.get("replacement")
        expected = raw.get("expected_text")
        edit_rationale = raw.get("rationale", rationale_value)
        if operation not in {"replace_lines", "insert_before", "insert_after", "create_file"}:
            return None
        if not isinstance(proposed_path, str) or not isinstance(replacement, str):
            return None
        if not isinstance(edit_rationale, str) or not edit_rationale.strip():
            return None
        total_replacement += len(replacement)
        if total_replacement > 64_000 or len(replacement) > 48_000:
            return None
        creating = operation == "create_file"
        try:
            path = path_policy.validate_relative(proposed_path, creating=creating)
        except PathPolicyError:
            return None
        expected_hash: str | None = None
        if creating:
            if path.exists():
                return None
            expected = None
        else:
            if not isinstance(expected, str) or not expected or len(expected) > 48_000:
                return None
            try:
                data_bytes = path.read_bytes()
                current = data_bytes.decode("utf-8")
            except (OSError, UnicodeError):
                return None
            if b"\0" in data_bytes[:8192] or current.count(expected) != 1:
                return None
            expected_hash = hashlib.sha256(data_bytes).hexdigest()
        edits.append(
            PlannedEdit(
                operation=str(operation),
                path=proposed_path,
                replacement=replacement,
                expected_text=expected if isinstance(expected, str) else None,
                expected_hash=expected_hash,
                rationale=edit_rationale[:2000],
            )
        )
    expected_frontier = FrontierStage(
        min(int(FrontierStage.SUCCESS), max(int(FrontierStage.ENVIRONMENT), int(current_frontier) + 1))
    )
    return PatchCandidate(
        id="candidate-model-001",
        repair_family=family,
        rationale=rationale_value[:4000],
        assumption_claim_ids=[],
        planned_edits=edits,
        confidence=0.80,
        risk=0.18,
        expected_frontier=expected_frontier,
        expected_information_gain_if_built=0.72,
        score_components={
            "critical_claim_confidence": 0.80,
            "causal_coverage": 0.78,
            "semantic_preservation": 0.70,
            "expected_frontier_gain": 0.70,
            "source_preservation": 0.72,
            "rule_support": 0.45,
            "minimality": 0.75,
        },
    )
