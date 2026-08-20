"""Semantic-shape rule retrieval without package-name or hidden-case lookup."""

from __future__ import annotations

from ..repair.patterns import RepairRule
from ..schemas import RepairFamily


def retrieve_by_repair_family(rules: list[RepairRule], family: RepairFamily) -> list[RepairRule]:
    return sorted((rule for rule in rules if rule.repair_family is family), key=lambda rule: (-rule.confidence, rule.id))
