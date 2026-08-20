"""Deterministic multi-label build failure classification."""

from __future__ import annotations

import re

from ..schemas import FailureFamily

_SIGNALS: tuple[tuple[FailureFamily, tuple[str, ...]], ...] = (
    (FailureFamily.ENVIRONMENT, (r"no space left", r"permission denied", r"network.*unavailable", r"internal error")),
    (FailureFamily.DEPENDENCY, (r"missing package", r"could not resolve", r"no matching package", r"dependency.*not found")),
    (FailureFamily.CONFIGURATION, (r"configure: error", r"cmake error", r"unknown architecture", r"unsupported target")),
    (FailureFamily.LINKING, (r"undefined reference", r"undefined symbol", r"cannot find -l", r"ld: ", r"linker command failed")),
    (FailureFamily.PACKAGING, (r"rpmbuild", r"debuild", r"packaging", r"package manifest", r"%files")),
    (FailureFamily.TEST, (r"tests? failed", r"assertion failed", r"pytest", r"ctest")),
    (FailureFamily.ARTIFACT, (r"expected artifact", r"artifact.*missing", r"validation failed")),
    (FailureFamily.COMPILATION, (r"fatal error:", r"error:", r"unrecognized command-line option", r"bad value .*?-march", r"intrinsic", r"assembler messages")),
    (FailureFamily.BUILD_PREPARATION, (r"patch failed", r"cannot unpack", r"source.*missing")),
)


def classify_failure(
    diagnostic: str, normalized_log: str = ""
) -> tuple[FailureFamily, list[FailureFamily], str]:
    """Classify with primary/secondary labels, prioritizing the causal diagnostic."""
    direct: list[FailureFamily] = []
    context: list[FailureFamily] = []
    for family, patterns in _SIGNALS:
        if any(re.search(pattern, diagnostic, re.I) for pattern in patterns):
            direct.append(family)
        elif any(re.search(pattern, normalized_log, re.I) for pattern in patterns):
            context.append(family)
    labels = direct + [family for family in context if family not in direct]
    primary = labels[0] if labels else FailureFamily.UNKNOWN
    rationale = (
        f"deterministic diagnostic signals selected {primary.value}"
        if labels
        else "no recognized deterministic diagnostic signal"
    )
    return primary, labels[1:], rationale
