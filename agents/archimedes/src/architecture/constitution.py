"""Serializable Architecture Constitution for case diagnostics and decisions."""

from __future__ import annotations

from dataclasses import asdict

from ..schemas import JSONValue
from .profile import ArchitectureProfile


def constitution_payload(profile: ArchitectureProfile) -> dict[str, JSONValue]:
    """Represent known and explicitly unknown architecture facts without guessing."""
    return {
        "canonical_architecture": profile.identity.canonical.value,
        "raw_architecture": profile.identity.raw,
        "aliases_observed": list(profile.identity.aliases_observed),
        "facts": {key: asdict(value) for key, value in sorted(profile.facts.items())},
        "ood_signals": list(profile.ood_signals),
    }
