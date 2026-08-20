"""Evidence-gated deterministic candidate generation for trusted V1 repair families."""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath

from ..architecture.hazard_scanner import ArchitectureHazard
from ..errors import PathPolicyError
from ..schemas import (
    Architecture, CaseData, FrontierStage, JSONValue, PatchCandidate, PlannedEdit, RepairFamily,
)
from ..workspace.path_policy import PathPolicy
from .deterministic import DeterministicMatch

PUBLIC_DEMO_BROKEN_BLOCK = """# BUILD-BENCH-DEMO-BROKEN
echo "intentional Build-Bench demo failure" >&2
exit 1
"""
PUBLIC_DEMO_REPAIRED_BLOCK = """# BUILD-BENCH-DEMO-REPAIRED
echo "Build-Bench Example Agent repaired the package"
"""


def _makefile_condition(line: str, target: Architecture) -> str:
    del target  # The guard describes flag support, not the currently observed target alone.
    return (
        "ARCHIMEDES_TARGET_TRIPLE ?= $(shell $(CC) -dumpmachine 2>/dev/null)\n"
        "ifneq (,$(filter x86_64% amd64%,$(ARCHIMEDES_TARGET_TRIPLE)))\n"
        f"{line}\n"
        "endif"
    )


def _cmake_condition(line: str) -> str:
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
    """Condition a directly observed x86-only flag in a recognized build declaration."""
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


def public_demo_candidate(
    *, path: str, claim_ids: list[str], confidence: float
) -> PatchCandidate:
    """Repair the released Starter Kit's explicit public failure marker.

    This adapter exists only to exercise the official local failure-to-success workflow. It is
    keyed by the public marker and direct file/log evidence, never by a hidden case identifier.
    """
    edit = PlannedEdit(
        operation="replace_lines",
        path=path,
        expected_text=PUBLIC_DEMO_BROKEN_BLOCK,
        replacement=PUBLIC_DEMO_REPAIRED_BLOCK,
        rationale="replace the released Starter Kit's explicit public demo failure block",
        claim_dependencies=claim_ids,
    )
    return PatchCandidate(
        id="candidate-001",
        repair_family=RepairFamily.PACKAGING_RULE,
        rationale=edit.rationale,
        assumption_claim_ids=claim_ids,
        planned_edits=[edit],
        confidence=confidence,
        risk=0.01,
        expected_frontier=FrontierStage.SUCCESS,
        expected_information_gain_if_built=0.99,
        score_components={
            "critical_claim_confidence": confidence,
            "causal_coverage": 0.99,
            "semantic_preservation": 0.99,
            "expected_frontier_gain": 0.99,
            "source_preservation": 0.99,
            "rule_support": 0.99,
            "minimality": 0.99,
        },
    )


def deterministic_match_candidate(
    match: DeterministicMatch,
    *,
    claim_ids: list[str],
    confidence: float,
    current_frontier: FrontierStage,
) -> PatchCandidate:
    """Convert a uniquely observed repair-rule match into a critic-ready candidate."""
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
    """Validate structured model edits against current bytes before critic review."""
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
