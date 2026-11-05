"""Turn an architecture profile into the JSON "architecture constitution" diagnostic."""

from __future__ import annotations

from dataclasses import asdict

from ..schemas import JSONValue
from .profile import ArchitectureProfile


def constitution_payload(profile: ArchitectureProfile) -> dict[str, JSONValue]:
    """Build the JSON form of an architecture profile.

    Unknown facts stay in the output with a ``None`` value and source
    ``unknown``, so a reader can see what was not established.

    Args:
        profile: The target architecture profile.

    Returns:
        The canonical and raw names, observed aliases, all facts sorted by key,
        and the out-of-distribution signals.
    """
    return {
        "canonical_architecture": profile.identity.canonical.value,
        "raw_architecture": profile.identity.raw,
        "aliases_observed": list(profile.identity.aliases_observed),
        "facts": {key: asdict(value) for key, value in sorted(profile.facts.items())},
        "ood_signals": list(profile.ood_signals),
    }
