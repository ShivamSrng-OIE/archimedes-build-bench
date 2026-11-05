"""Strict extraction and validation for machine-actionable model JSON."""

from __future__ import annotations

import json
import re

from ..errors import ModelProtocolError
from ..json_safety import reject_excessive_json_nesting
from ..schemas import JSONValue
from .base import ModelTask

_REQUIRED_KEYS: dict[ModelTask, set[str]] = {
    ModelTask.FAILURE_ANALYSIS: {
        "primary_failure",
        "failure_stage",
        "claims",
        "hypotheses",
        "recommended_files",
    },
    ModelTask.SEMANTIC_ANCHOR: {
        "purpose",
        "inputs",
        "outputs",
        "side_effects",
        "invariants",
        "portable_alternatives",
    },
    ModelTask.HYPOTHESIS_GENERATION: {"hypotheses"},
    ModelTask.HYPOTHESIS_RANKING: {"ranked_hypothesis_ids"},
    ModelTask.PATCH_PLANNING: {"repair_family", "rationale", "edits", "assumptions"},
    ModelTask.PATCH_CRITIQUE: {"accepted", "findings"},
    ModelTask.POST_BUILD_REFLECTION: {
        "interpretation",
        "hypothesis_updates",
        "next_action",
    },
}
_ALLOWED_EDIT_OPERATIONS = {
    "replace_lines",
    "insert_before",
    "insert_after",
    "create_file",
}
_MAX_ANALYSIS_ITEMS = 50
_MAX_PATCH_ASSUMPTIONS = 20


def _reject_nonfinite_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _object_from_text(raw: str) -> dict[str, JSONValue]:
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.I)
    candidates = [stripped]
    start, end = stripped.find("{"), stripped.rfind("}")
    if start >= 0 and end > start:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        try:
            reject_excessive_json_nesting(candidate)
            value = json.loads(candidate, parse_constant=_reject_nonfinite_constant)
        except (json.JSONDecodeError, ValueError, RecursionError):
            continue
        if isinstance(value, dict):
            return value
    raise ModelProtocolError("model response does not contain one valid JSON object")


def parse_model_json(raw: str, task: ModelTask) -> dict[str, JSONValue]:
    """Extract one object, require task keys, and reject dangerous edit action types."""
    value = _object_from_text(raw)
    missing = _REQUIRED_KEYS[task] - value.keys()
    if missing:
        raise ModelProtocolError(
            f"model response missing required keys: {sorted(missing)}"
        )
    if task is ModelTask.FAILURE_ANALYSIS:
        if not all(
            isinstance(value.get(key), str) and str(value.get(key)).strip()
            for key in ("primary_failure", "failure_stage")
        ):
            raise ModelProtocolError(
                "failure analysis primary_failure and failure_stage must be non-empty strings"
            )
        claims = value.get("claims")
        hypotheses = value.get("hypotheses")
        recommended_files = value.get("recommended_files")
        if (
            not isinstance(claims, list)
            or len(claims) > _MAX_ANALYSIS_ITEMS
            or not all(
                isinstance(item, dict)
                and isinstance(item.get("statement"), str)
                and bool(str(item.get("statement")).strip())
                for item in claims
            )
        ):
            raise ModelProtocolError(
                "failure analysis claims must be objects with non-empty statement strings"
            )
        if not isinstance(hypotheses, list) or len(hypotheses) > _MAX_ANALYSIS_ITEMS:
            raise ModelProtocolError(
                "failure analysis hypotheses must be a bounded array"
            )
        if (
            not isinstance(recommended_files, list)
            or len(recommended_files) > _MAX_ANALYSIS_ITEMS
            or not all(
                isinstance(item, str) and item.strip() for item in recommended_files
            )
        ):
            raise ModelProtocolError(
                "failure analysis recommended_files must be repository-relative strings"
            )
    if task is ModelTask.PATCH_CRITIQUE:
        findings = value.get("findings")
        if type(value.get("accepted")) is not bool or (
            not isinstance(findings, list)
            or len(findings) > _MAX_ANALYSIS_ITEMS
            or not all(isinstance(item, str) for item in findings)
        ):
            raise ModelProtocolError(
                "patch critique requires accepted boolean and bounded string findings"
            )
    if task is ModelTask.PATCH_PLANNING:
        edits = value.get("edits")
        assumptions = value.get("assumptions")
        if not all(
            isinstance(value.get(key), str) and str(value.get(key)).strip()
            for key in ("repair_family", "rationale")
        ):
            raise ModelProtocolError(
                "patch repair_family and rationale must be non-empty strings"
            )
        if (
            not isinstance(assumptions, list)
            or not assumptions
            or len(assumptions) > _MAX_PATCH_ASSUMPTIONS
            or not all(isinstance(item, str) and item.strip() for item in assumptions)
        ):
            raise ModelProtocolError(
                "patch assumptions must be a non-empty string array"
            )
        if not isinstance(edits, list) or not 1 <= len(edits) <= 5:
            raise ModelProtocolError(
                "patch edits must contain between one and five operations"
            )
        for edit in edits:
            if (
                not isinstance(edit, dict)
                or edit.get("operation") not in _ALLOWED_EDIT_OPERATIONS
            ):
                raise ModelProtocolError(
                    "patch contains an unknown or dangerous operation"
                )
            if not all(
                isinstance(edit.get(key), str) and edit.get(key)
                for key in ("path", "replacement", "rationale")
            ):
                raise ModelProtocolError(
                    "patch edit path, replacement, and rationale must be strings"
                )
            if edit.get("operation") != "create_file" and not isinstance(
                edit.get("expected_text"), str
            ):
                raise ModelProtocolError(
                    "non-create patch edits require exact expected_text"
                )
    return value
