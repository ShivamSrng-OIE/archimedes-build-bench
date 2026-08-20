"""Strict extraction and validation for machine-actionable model JSON."""

from __future__ import annotations

import json
import re

from ..errors import ModelProtocolError
from ..schemas import JSONValue
from .base import ModelTask

_REQUIRED_KEYS: dict[ModelTask, set[str]] = {
    ModelTask.FAILURE_ANALYSIS: {"primary_failure", "failure_stage", "claims", "hypotheses", "recommended_files"},
    ModelTask.SEMANTIC_ANCHOR: {"purpose", "inputs", "outputs", "side_effects", "invariants", "portable_alternatives"},
    ModelTask.HYPOTHESIS_GENERATION: {"hypotheses"},
    ModelTask.HYPOTHESIS_RANKING: {"ranked_hypothesis_ids"},
    ModelTask.PATCH_PLANNING: {"repair_family", "rationale", "edits", "assumptions"},
    ModelTask.PATCH_CRITIQUE: {"accepted", "findings"},
    ModelTask.POST_BUILD_REFLECTION: {"interpretation", "hypothesis_updates", "next_action"},
}
_ALLOWED_EDIT_OPERATIONS = {"replace_lines", "insert_before", "insert_after", "create_file"}


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
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ModelProtocolError("model response does not contain one valid JSON object")


def parse_model_json(raw: str, task: ModelTask) -> dict[str, JSONValue]:
    """Extract one object, require task keys, and reject dangerous edit action types."""
    value = _object_from_text(raw)
    missing = _REQUIRED_KEYS[task] - value.keys()
    if missing:
        raise ModelProtocolError(f"model response missing required keys: {sorted(missing)}")
    if task is ModelTask.PATCH_PLANNING:
        edits = value.get("edits")
        if not isinstance(edits, list):
            raise ModelProtocolError("patch edits must be a JSON array")
        for edit in edits:
            if not isinstance(edit, dict) or edit.get("operation") not in _ALLOWED_EDIT_OPERATIONS:
                raise ModelProtocolError("patch contains an unknown or dangerous operation")
    return value
