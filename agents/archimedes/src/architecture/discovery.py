"""Fill in target-architecture facts from the build log and the hazard scan alone.

Discovery runs only when the target is unfamiliar. It reads evidence that is
already on hand and never runs a tool or guesses a value.
"""

from __future__ import annotations

import re

from ..schemas import ArchitectureFact, CaseData
from .hazard_scanner import ArchitectureHazard
from .profile import ArchitectureProfile


def discover_from_static_evidence(
    profile: ArchitectureProfile, case: CaseData, hazards: list[ArchitectureHazard]
) -> ArchitectureProfile:
    """Add facts to a profile from the build log and the hazard list.

    It records the compiler family named in the log (gcc, clang or cc), an
    ``-mabi=`` value from the log, and whether the repository contains
    architecture macros. When the scan found no hazards at all, it adds an
    out-of-distribution signal.

    Args:
        profile: The target profile to update in place.
        case: The Case, whose build log is searched.
        hazards: Hazards found by the static scan.

    Returns:
        The same profile object, updated.
    """
    log = case.build_log
    compiler = re.search(r"\b(gcc|clang|cc)(?:[- ]|\s)", log, re.IGNORECASE)
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
