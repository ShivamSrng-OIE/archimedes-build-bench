"""Classify a build failure into a primary failure family plus secondary families.

``_SIGNALS`` lists regular expressions per family. Its order is the priority order:
when several families match, the earlier one becomes the primary label.
"""

from __future__ import annotations

import re

from ..schemas import FailureFamily

_SIGNALS: tuple[tuple[FailureFamily, tuple[str, ...]], ...] = (
    (FailureFamily.ENVIRONMENT, (r"no space left", r"permission denied", r"network.*unavailable", r"internal error", r"build killed with signal", r"(?:minutes?|mins?|hours?|hrs?) of inactivity", r"job seems to be stuck here", r"out of memory: killed process", r"killed signal terminated program")),
    (FailureFamily.DEPENDENCY, (r"missing package", r"could not resolve", r"no matching package", r"dependency.*not found", r"modulenotfounderror", r"no module named", r"command not found", r"can't exec .*no such file", r"^(?:error:\s*)?unresolvable\b", r"\bnothing provides \S+", r"failed build dependencies", r"but it is not (?:installable|going to be installed)", r"\bDepends: \S+(?: \([^)]*\))? but \S+ is to be installed", r"unmet (?:build )?dependencies")),
    (FailureFamily.CONFIGURATION, (r"configure: error", r"cmake error", r"unknown architecture", r"unsupported target", r"error:\s*invalid command")),
    (FailureFamily.LINKING, (r"undefined reference", r"undefined symbol", r"cannot find -l", r"(?:^|/)ld(?:\.\w+)?: ", r"linker command failed", r"\bBFD\b.*assertion fail", r"relocation .*can not be used", r"GCS is required by -z gcs")),
    (FailureFamily.PACKAGING, (r"bad exit status from \S+ \(%\w+\)", r"installed \(but unpackaged\)", r"(?:file|directory) not found(?: by glob)?: \S*buildroot", r"^error: line \d+: ", r"\be: badness \d+ exceeds threshold", r"rpmbuild(?!/)", r"debuild", r"packaging", r"package manifest", r"%files", r"dpkg-(?:deb|gencontrol|gensymbols): error", r"(?:cannot|can't) (?:access|stat).*debian/", r"cannot find \(any matches for\)", r"failed to remove .*debian/")),
    (FailureFamily.TEST, (r"^\s*\d+/\d+\s+\S.*?\s+FAIL\s+\d", r"\.\w+:\d+: failure$", r"^\[\s+failed\s+\]", r"^--- fail: ", r"tests? failed", r"assertion(?:error| failed)", r"pytest", r"ctest", r"^FAIL(?:ED)?:", r"Failed test", r"(?:found issues in|test failure|failed unexpectedly)", r"illegal instruction", r"segmentation fault", r"differ: byte")),
    (FailureFamily.ARTIFACT, (r"expected artifact", r"artifact.*missing", r"validation failed")),
    (FailureFamily.COMPILATION, (r"fatal error:", r"error:", r"unrecognized command-line option", r"bad value .*?-march", r"intrinsic", r"assembler messages", r"reference .*not found in the current environment", r"inconsistent assumptions over library")),
    (FailureFamily.BUILD_PREPARATION, (r"patch failed", r"hunks? failed", r"cannot unpack", r"source.*missing")),
)


def classify_failure(
    diagnostic: str, normalized_log: str = ""
) -> tuple[FailureFamily, list[FailureFamily], str]:
    """Classify a failure, giving families seen in the diagnostic line priority.

    A family is "direct" when one of its patterns matches the diagnostic itself, and
    "context" when it matches only the wider log text. Direct families come first,
    each group in ``_SIGNALS`` order, so a generic ``error:`` elsewhere in the log
    cannot outrank what the causal line says. The caller is expected to strip noise
    lines from ``normalized_log`` first so they cannot lend a family.

    Args:
        diagnostic: The primary diagnostic line.
        normalized_log: Context text around the diagnostic. Empty means only the
            diagnostic is used.

    Returns:
        A tuple of the primary family (``FailureFamily.UNKNOWN`` when nothing
        matched), the remaining matched families in priority order, and a short
        rationale string.
    """
    direct: list[FailureFamily] = []
    context: list[FailureFamily] = []
    for family, patterns in _SIGNALS:
        if any(re.search(pattern, diagnostic, re.IGNORECASE) for pattern in patterns):
            direct.append(family)
        elif any(re.search(pattern, normalized_log, re.IGNORECASE) for pattern in patterns):
            context.append(family)
    labels = direct + [family for family in context if family not in direct]
    primary = labels[0] if labels else FailureFamily.UNKNOWN
    rationale = (
        f"deterministic diagnostic signals selected {primary.value}"
        if labels
        else "no recognized deterministic diagnostic signal"
    )
    return primary, labels[1:], rationale
