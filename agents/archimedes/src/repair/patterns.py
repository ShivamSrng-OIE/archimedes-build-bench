"""Generic portability-rule loading and semantic-shape matching."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..schemas import FailureFamily, JSONValue, RepairFamily


@dataclass(frozen=True, slots=True)
class RepairRule:
    id: str
    failure_family: FailureFamily
    repair_family: RepairFamily
    architecture_constraints: tuple[str, ...]
    preconditions: tuple[str, ...]
    repair_strategy: tuple[str, ...]
    negative_conditions: tuple[str, ...]
    confidence: float


def _string_tuple(value: JSONValue | object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if isinstance(item, (str, int, float, bool)))


def load_rules(path: Path) -> list[RepairRule]:
    raw: JSONValue = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("portability rulebook must contain a JSON array")
    rules: list[RepairRule] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        confidence_value = item.get("confidence", 0.5)
        confidence = float(confidence_value) if isinstance(confidence_value, (str, int, float)) else 0.5
        rules.append(RepairRule(
            id=str(item["id"]),
            failure_family=FailureFamily(str(item["failure_family"])),
            repair_family=RepairFamily(str(item["repair_family"])),
            architecture_constraints=_string_tuple(item.get("architecture_constraints")),
            preconditions=_string_tuple(item.get("preconditions")),
            repair_strategy=_string_tuple(item.get("repair_strategy")),
            negative_conditions=_string_tuple(item.get("negative_conditions")),
            confidence=confidence,
        ))
    return rules


def matching_rules(
    rules: list[RepairRule], failure_family: FailureFamily, keywords: set[str]
) -> list[RepairRule]:
    """Retrieve by failure/semantic shape rather than package or hidden case identity."""
    matches = []
    lowered = {value.lower() for value in keywords}
    for rule in rules:
        if rule.failure_family is not failure_family:
            continue
        required = {value.lower() for value in rule.preconditions}
        coverage = sum(any(token in condition for token in lowered) for condition in required)
        if not required or coverage:
            matches.append(rule)
    return sorted(matches, key=lambda item: (-item.confidence, item.id))
