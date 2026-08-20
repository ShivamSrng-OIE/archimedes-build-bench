"""Minimum, evidence-first architecture discovery needed for a current failure."""

from __future__ import annotations

import re

from ..schemas import ArchitectureFact, CaseData
from .hazard_scanner import ArchitectureHazard
from .profile import ArchitectureProfile


def discover_from_static_evidence(
    profile: ArchitectureProfile, case: CaseData, hazards: list[ArchitectureHazard]
) -> ArchitectureProfile:
    """Update a profile from metadata, logs, and repository conditionals only."""
    log = case.build_log
    compiler = re.search(r"\b(gcc|clang|cc)(?:[- ]|\s)", log, re.I)
    if compiler:
        profile.facts["compiler_family"] = ArchitectureFact(
            "compiler_family", compiler.group(1).lower(), "build log", 0.85
        )
    abi = re.search(r"-mabi=([^\s]+)", log)
    if abi:
        profile.facts["abi"] = ArchitectureFact("abi", abi.group(1), "build log", 0.90)
    if any(h.category == "architecture_macro" for h in hazards):
        profile.facts["repository_architecture_conditions"] = ArchitectureFact(
            "repository_architecture_conditions", True, "static repository scan", 0.90
        )
    if not hazards:
        profile.ood_signals.append("few matching portability signals")
    return profile
