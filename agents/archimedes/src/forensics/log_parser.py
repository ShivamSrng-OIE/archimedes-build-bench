"""Normalize build logs and pick out the line that actually caused the failure.

The primary diagnostic is the first causal error, not the last line printed: a build
usually ends with ``make: ***`` or ``Bad exit status`` echoes of an earlier compiler,
linker, packaging or test error. ``analyze_build_log`` ties normalization, diagnostic
selection, classification, signature and frontier together.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..constants import MAX_LOG_CHARACTERS
from ..schemas import FailureAnalysis
from .error_signature import normalize_error_signature
from .failure_classifier import classify_failure
from .frontier import frontier_from_family

_ANSI = re.compile(r"\x1b(?:[@-_][0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_TIMESTAMP = re.compile(
    r"^(?:\[)?(?:\d{4}-\d{2}-\d{2}[T ][0-9:.+Z-]+|\d{2}:\d{2}:\d{2})(?:\])?\s*"
)
# Open Build Service logs prefix every line with elapsed seconds, e.g. "[   43s] ".
_OBS_ELAPSED = re.compile(r"^\[\s*\d+s\]\s?")
# OBS prints this marker once the build root is ready and rpmbuild starts. Diagnostics
# before it come from preparing the build root, so a failure inside the build wins over them.
_OBS_BUILD_START = re.compile(r"^-{5,}\s+building\s+\S+(?:\s+\(user\s+\S+\))?\s*$", re.IGNORECASE)
_PREFIX = re.compile(r"^(?:stdout|stderr|error|warning)\s*[|:]\s*", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class DiagnosticPattern:
    """One entry in the table of known diagnostic shapes.

    Attributes:
        regex: The pattern searched for in a normalized log line.
        causal_weight: How likely a matching line is to be the real cause. Concrete
            compiler errors score 100; wrapper lines such as ``make: ***`` score
            15 or less so they only win when nothing better exists.
    """

    regex: re.Pattern[str]
    causal_weight: int


_PATTERNS = (
    DiagnosticPattern(re.compile(r"fatal error:.*(?:not found|no such file)", re.IGNORECASE), 100),
    DiagnosticPattern(
        re.compile(
            r"(?:^|[/\\])(?:cc1|lto1|gcc|g\+\+|clang|clang\+\+|xgcc): "
            r"(?:fatal |internal compiler )?error:",
            re.IGNORECASE,
        ),
        100,
    ),
    DiagnosticPattern(
        re.compile(r"(?:^|\s)[^\s:]+:\d+(?::\d+)?: (?:fatal )?error:", re.IGNORECASE), 100
    ),
    DiagnosticPattern(re.compile(r"(?:^|\s)error\[E\d+\]:", re.IGNORECASE), 100),
    DiagnosticPattern(re.compile(r"(?:^|\s)(?:as|nasm):.*\bError:", re.IGNORECASE), 99),
    DiagnosticPattern(re.compile(r"(?:unrecognized|unknown|unsupported|bad value).*(?:option|argument|march|mcpu|mabi|-m)", re.IGNORECASE), 100),
    DiagnosticPattern(re.compile(r"(?:relocation .*can not be used|recompile with -fPIC)", re.IGNORECASE), 99),
    DiagnosticPattern(
        re.compile(
            r"build killed with signal|after \d+\s*(?:minutes?|mins?|hours?|hrs?) of inactivity|"
            r"job seems to be stuck here, killed",
            re.IGNORECASE,
        ),
        99,
    ),
    DiagnosticPattern(re.compile(r"\bOut of memory: Killed process\b", re.IGNORECASE), 99),
    # rpmbuild's own stage errors are causal and must outrank soft-failed test output.
    DiagnosticPattern(
        re.compile(
            r"^(?:error: )?(?:(?:File|Directory) not found(?: by glob)?: /\S*BUILDROOT|"
            r"Installed \(but unpackaged\) file\(s\) found|Failed build dependencies)|"
            r"^error: line \d+: ",
            re.IGNORECASE,
        ),
        97,
    ),
    DiagnosticPattern(re.compile(r"\b\d+ out of \d+ hunks? FAILED\b", re.IGNORECASE), 95),
    DiagnosticPattern(re.compile(r"\bDepends: \S+(?: \([^)]*\))? but (?:it is not (?:installable|going to be installed)|\S+ is to be installed)", re.IGNORECASE), 92),
    DiagnosticPattern(re.compile(r"\bE: badness \d+ exceeds threshold\b", re.IGNORECASE), 90),
    DiagnosticPattern(re.compile(r"^\s*\d+/\d+\s+\S.*?\s+FAIL\s+\d", re.IGNORECASE), 90),
    DiagnosticPattern(re.compile(r"\.\w+:\d+: Failure$|^\[\s+FAILED\s+\]\s+\w", re.IGNORECASE), 88),
    DiagnosticPattern(re.compile(r"^--- FAIL: \S+", re.IGNORECASE), 88),
    DiagnosticPattern(re.compile(r":\s*line \d+: .*: No such file or directory\s*$", re.IGNORECASE), 80),
    DiagnosticPattern(re.compile(r"(?:unknown|invalid).*(?:instruction|register|architecture|target)", re.IGNORECASE), 98),
    DiagnosticPattern(re.compile(r"\bBFD\b.*assertion fail", re.IGNORECASE), 98),
    DiagnosticPattern(re.compile(r"(?:intrinsic|builtin).*(?:mismatch|requires|undeclared|not available)", re.IGNORECASE), 96),
    DiagnosticPattern(
        re.compile(
            r"(?:error:\s*invalid command|:\s*command not found|"
            r"(?:command|executable)\s+['\"`]?[-A-Za-z0-9_./+]+['\"`]?\s+"
            r"(?:was\s+)?not found)",
            re.IGNORECASE,
        ),
        96,
    ),
    DiagnosticPattern(re.compile(r"can't exec .*no such file", re.IGNORECASE), 96),
    DiagnosticPattern(re.compile(r"(?:cannot|can't) (?:access|stat|get).*(?:no such file|not found|info from)", re.IGNORECASE), 95),
    DiagnosticPattern(re.compile(r"(?:cannot find \(any matches for\)|failed to remove .*no such file)", re.IGNORECASE), 95),
    DiagnosticPattern(re.compile(r"(?:ModuleNotFoundError|ImportError|FileNotFoundError|AttributeError|AssertionError):", re.IGNORECASE), 95),
    DiagnosticPattern(re.compile(r"(?:assertion [`'][^`']+[`'] failed|assertion failed:|child killed by signal)", re.IGNORECASE), 95),
    DiagnosticPattern(re.compile(r"(?:^|\s).*found issues in", re.IGNORECASE), 97),
    DiagnosticPattern(re.compile(r"(?:^|\s).*(?:test failure|failed unexpectedly)", re.IGNORECASE), 90),
    DiagnosticPattern(re.compile(r"(?:undefined reference|undefined symbol).*(?:atomic|__atomic|__sync)", re.IGNORECASE), 94),
    DiagnosticPattern(re.compile(r"dpkg-gensymbols: error:.*(?:disappeared|symbols file)", re.IGNORECASE), 93),
    DiagnosticPattern(re.compile(r"dpkg-(?:deb|gencontrol): error:", re.IGNORECASE), 93),
    DiagnosticPattern(re.compile(r"(?:^|\s)CMake Error(?: at|:)", re.IGNORECASE), 98),
    DiagnosticPattern(re.compile(r"(?:undefined reference|undefined symbol)", re.IGNORECASE), 90),
    DiagnosticPattern(re.compile(r"(?:cannot find -l|library not found|missing package)", re.IGNORECASE), 88),
    DiagnosticPattern(
        re.compile(r"\bnothing provides \S+(?:\s*[<>=]+\s*\S+)?(?: needed by\b|\s*$)", re.IGNORECASE), 72
    ),
    DiagnosticPattern(re.compile(r"^(?:error:\s*)?unresolvable\b", re.IGNORECASE), 70),
    DiagnosticPattern(re.compile(r"configure:(?: error|.*failed)", re.IGNORECASE), 86),
    DiagnosticPattern(re.compile(r"(?:^FAIL:|^FAILED\b|Failed test|failed \d+ tests?|error: test failed|Illegal instruction|Segmentation fault|differ: byte)", re.IGNORECASE), 84),
    DiagnosticPattern(re.compile(r"(?:reference .*not found in the current environment|makes inconsistent assumptions over library)", re.IGNORECASE), 82),
    DiagnosticPattern(re.compile(r"GCS is required by -z gcs.*lacks the necessary", re.IGNORECASE), 82),
    # Tool wrappers are consequences, not the underlying diagnostic. Keep them as fallbacks
    # but score them below a concrete compiler, linker, packaging, or test failure.
    DiagnosticPattern(re.compile(r"(?:dpkg-buildpackage|dh_(?:auto_\w+|autoreconf)|collect2): error:", re.IGNORECASE), 25),
    DiagnosticPattern(re.compile(r"(?:make|ninja).*\*\*\*|exit status|returned exit (?:code|status)", re.IGNORECASE), 15),
    DiagnosticPattern(re.compile(r"(?:error:|fatal:|failed:)", re.IGNORECASE), 60),
    DiagnosticPattern(re.compile(r"rpmbuild.*\*\*\*", re.IGNORECASE), 10),
)

# Python logging records ("ERROR:root:...") in test output are not build diagnostics.
_LOGGING_RECORD = re.compile(r"^(?:ERROR|WARNING|INFO|DEBUG):[\w.]+:", re.IGNORECASE)

_NON_CAUSAL: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:^|[#\s])(?:ERROR|FAIL|XFAIL):\s*0(?:\s|$)", re.IGNORECASE),
    re.compile(r"LaTeX Warning:.*undefined references", re.IGNORECASE),
    re.compile(r"\.tex:\d+:\s*Warning:\s*Command not found:", re.IGNORECASE),
    re.compile(r"warning: failed to verify signature", re.IGNORECASE),
    re.compile(r"(?:libtool: link: warning: undefined symbols|configure: WARNING:)", re.IGNORECASE),
    re.compile(r"expected error:", re.IGNORECASE),
    re.compile(r":\s*note:.*may have been intended to silence earlier diagnostics", re.IGNORECASE),
    re.compile(
        r"^(?:\|\s*)?[-A-Za-z0-9_+.]+:\s+error:\s+unrecognized command-line "
        r"option\s+['\"‘’]-(?:V|qversion|version)['\"‘’]",
        re.IGNORECASE,
    ),
    re.compile(r"^(?:\|\s*)?[-A-Za-z0-9_+.]+:\s+fatal error:\s+no input files\s*$", re.IGNORECASE),
    re.compile(r"^(?:\|\s*)?conftest\.[A-Za-z0-9]+:\d+(?::\d+)?:\s+(?:fatal\s+)?error:", re.IGNORECASE),
    re.compile(r"^\|\s*conftest\.[A-Za-z0-9]+:\d+(?::\d+)?:\s+warning:", re.IGNORECASE),
    re.compile(r"^\|\s*configure:\s+failed program was:", re.IGNORECASE),
    re.compile(
        r"^\|\s*[-A-Za-z0-9_+.]+:\s+(?:fatal\s+|internal compiler\s+)?error:",
        re.IGNORECASE,
    ),
    # Released Launchpad logs include complete environment/package inventories and echoed
    # compiler/Cargo commands. Words such as "error" can occur inside their metadata and
    # must not outrank the later diagnostic emitted by the command.
    re.compile(r"^(?:Inherited environment:|Package versions:)", re.IGNORECASE),
    re.compile(r"^\s*Running [`'](?:CARGO|rustc)=", re.IGNORECASE),
    re.compile(
        r"^\s*['\"]?\s*CARGO_PKG_.*(?:\brustc\b.*--error-format|build-script-build)",
        re.IGNORECASE,
    ),
    re.compile(r"^(?:/bin/bash\s+)?(?:\./)?libtool\b.*--mode=(?:compile|link)\b", re.IGNORECASE),
    re.compile(
        r"^libtool:\s+(?:compile|link):\s+(?:/\S+/)?"
        r"(?:cc|c\+\+|gcc|g\+\+|clang|clang\+\+)(?=\s|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*cd\s+\S+\s+&&\s+(?:/\S+/)?"
        r"(?:cc|c\+\+|gcc|g\+\+|clang|clang\+\+)(?=\s|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:/\S+/)?(?:cc|c\+\+|gcc|g\+\+|clang|clang\+\+)(?=\s|$)\s+"
        r"(?=.*(?:\s-[DIOWfgm]|\s-o\s))",
        re.IGNORECASE,
    ),
    re.compile(r"^checking (?:for|if|whether)\b", re.IGNORECASE),
    _LOGGING_RECORD,
)

_OBS_NOISE: tuple[re.Pattern[str], ...] = (
    # Noise from OBS build-root preparation on hosted Validators (chkstat ownership
    # checks and package %post scriptlets) that does not stop the build.
    re.compile(r"^ERROR: not all operations were successful\.?$", re.IGNORECASE),
    re.compile(r"has unexpected owner \(\d+\)\. Refusing to correct", re.IGNORECASE),
    re.compile(r"^(?:warning:\s*)?%(?:pre|post)(?:un|trans)?\([^)]*\) scriptlet failed", re.IGNORECASE),
    re.compile(r"^can't open /etc/polkit-1/", re.IGNORECASE),
    # Final status lines written by the OBS wrapper and the hosted runner summarize the
    # failure; they carry a host id and clock time but no cause.
    re.compile(r"^\[build-case\]"),
    re.compile(r"^[0-9a-f]{12} failed \"build \S+\" at ", re.IGNORECASE),
)
_NON_CAUSAL = _NON_CAUSAL + _OBS_NOISE
_CONTEXT_NOISE = (*_OBS_NOISE, _LOGGING_RECORD)
_REPEATED = re.compile(r" \[repeated \d+ times\]$")


def _matchable(line: str) -> str:
    """Strip the ``[repeated N times]`` marker that ``normalize_log`` appends.

    Several patterns are anchored with ``$``, so they would miss a line that carries
    the marker.

    Args:
        line: A line from the normalized log.

    Returns:
        The line without the trailing repetition marker.
    """
    return _REPEATED.sub("", line)


def _is_non_causal(line: str) -> bool:
    """Check whether a line looks like an error but is known not to be a cause.

    This covers configure probes (``conftest.c`` errors, ``checking for ...``),
    echoed compiler and libtool command lines, Python logging records, Launchpad
    environment dumps and the OBS build-root noise in ``_OBS_NOISE``.

    Args:
        line: A line from the normalized log.

    Returns:
        True if the line must be skipped when choosing the primary diagnostic.
    """
    probe = _matchable(line)
    return any(pattern.search(probe) for pattern in _NON_CAUSAL)


def _classification_context(normalized: str) -> str:
    """Remove OBS host noise and logging records from the log used for classification.

    The classifier also looks at the wider log for secondary families. Without this
    filter, a chkstat ``ERROR:`` line or a failed ``%post`` scriptlet would add a
    family that has nothing to do with the real diagnostic.

    Args:
        normalized: The output of ``normalize_log``.

    Returns:
        The normalized log without lines matching ``_CONTEXT_NOISE``.
    """
    return "\n".join(
        line
        for line in normalized.splitlines()
        if not any(pattern.search(_matchable(line)) for pattern in _CONTEXT_NOISE)
    )

_FILE_PATTERNS = (
    re.compile(r"\[(?P<path>[A-Za-z0-9_.+@/-]+):\d+(?::[^\]]+)?\]"),
    re.compile(
        r"(?P<path>(?:/<<PKGBUILDDIR>>/|/<<BUILDDIR>>/[^/\s]+/)?"
        r"[A-Za-z0-9_.+@/-]+\.(?:c|cc|cpp|cxx|h|hh|hpp|rs|go|py|java|vala|m|mm|s|S))"
        r":\d+"
    ),
    re.compile(r"(?:python\d*(?:\.\d+)?\s+)(?P<path>[A-Za-z0-9_.+@/-]+\.py)\b"),
    re.compile(r"CMake Error at (?P<path>[A-Za-z0-9_.+@/-]+):\d+", re.IGNORECASE),
    re.compile(r"(?:cannot|can't) (?:access|stat)\s+['\"‘](?P<path>debian/[^'\"’]+)", re.IGNORECASE),
)


def normalize_log(raw_log: str) -> str:
    """Clean a raw build log so diagnostic patterns can match it reliably.

    Logs longer than ``MAX_LOG_CHARACTERS`` keep their head (at most 256,000
    characters) and tail with a marker in between, because setup context and the
    final error are what matter. Each line loses ANSI escape codes and NUL bytes and
    is cut to 10,000 characters. On OBS lines only the ``[  NNs]`` elapsed-time prefix
    is removed; on other lines a leading timestamp and a ``stdout:``, ``stderr:``,
    ``error:`` or ``warning:`` prefix are removed. Runs of identical lines are
    collapsed into one line ending in ``[repeated N times]``, and empty lines are
    dropped.

    Args:
        raw_log: The build log text as read from the Case.

    Returns:
        The normalized log as newline-separated lines, in the original order.
    """
    if len(raw_log) > MAX_LOG_CHARACTERS:
        head = min(256_000, MAX_LOG_CHARACTERS // 4)
        tail = MAX_LOG_CHARACTERS - head
        raw_log = (
            raw_log[:head]
            + "\n[... middle of oversized build log omitted ...]\n"
            + raw_log[-tail:]
        )
    raw_log = raw_log.replace("\r\n", "\n").replace("\r", "\n")
    cleaned: list[str] = []
    previous = None
    repetitions = 0
    for raw in raw_log.splitlines():
        line = _ANSI.sub("", raw).replace("\x00", "")[:10_000]
        obs_line = _OBS_ELAPSED.sub("", line)
        if obs_line != line:
            # Keep rpmbuild's own "error:" lead-in on OBS lines; it is the diagnostic.
            line = obs_line.rstrip()
        else:
            line = _PREFIX.sub("", _TIMESTAMP.sub("", line)).rstrip()
        if line == previous:
            repetitions += 1
            continue
        if repetitions and cleaned:
            cleaned[-1] += f" [repeated {repetitions + 1} times]"
        repetitions = 0
        previous = line
        if line:
            cleaned.append(line)
    if repetitions and cleaned:
        cleaned[-1] += f" [repeated {repetitions + 1} times]"
    return "\n".join(cleaned)


def _obs_build_start(lines: list[str]) -> int:
    """Find the OBS ``----- building X.spec (user abuild)`` line, if there is one.

    Args:
        lines: The normalized log split into lines.

    Returns:
        The index of the first build-start marker, or 0 when the log has none.
    """
    for index, line in enumerate(lines):
        if _OBS_BUILD_START.search(line):
            return index
    return 0


def identify_primary_diagnostic(normalized_log: str) -> tuple[str, int]:
    """Choose the line most likely to be the cause of the build failure.

    When an OBS build-start marker exists, the search begins there so errors from
    preparing the build root are ignored, and the whole log is searched only if
    nothing scores after the marker. If no line matches any pattern, the last line
    that is not known noise is used, and failing that the very last line.

    Args:
        normalized_log: The output of ``normalize_log``.

    Returns:
        A tuple of the chosen line and its index in the normalized log, or
        ``("", 0)`` for an empty log.
    """
    lines = normalized_log.splitlines()
    start = _obs_build_start(lines)
    if start:
        found = _best_diagnostic(lines, start)
        if found is not None:
            return found
    found = _best_diagnostic(lines, 0)
    if found is not None:
        return found
    for index in range(len(lines) - 1, -1, -1):
        if not _is_non_causal(lines[index]):
            return lines[index], index
    if lines:
        return lines[-1], len(lines) - 1
    return "", 0


def _best_diagnostic(lines: list[str], start: int) -> tuple[str, int] | None:
    """Score lines against ``_PATTERNS`` and return the highest-scoring one.

    A line's base score is the weight of the first pattern in ``_PATTERNS`` order
    that matches it, so table order matters as well as the weights. Lines in the
    first 400 lines after ``start`` get an early bonus of up to 20 points that drops
    by one every 20 lines, so an early cause can beat a slightly higher-weighted
    error printed much later. On a tie the earlier line is kept. Known noise lines
    are skipped.

    Args:
        lines: The normalized log split into lines.
        start: Index of the first line to consider.

    Returns:
        A tuple of the best line and its index, or None if no line from ``start``
        onwards matches any pattern.
    """
    best_line = ""
    best_score = -1
    best_index = 0
    for index in range(start, len(lines)):
        line = lines[index]
        if _is_non_causal(line):
            continue
        probe = _matchable(line)
        for pattern in _PATTERNS:
            if pattern.regex.search(probe):
                early_bonus = max(0, 20 - (index - start) // 20)
                score = pattern.causal_weight + early_bonus
                if score > best_score:
                    best_line, best_score, best_index = line, score, index
                break
    if not best_line:
        return None
    return best_line, best_index


def _normalize_repository_path(value: str) -> str | None:
    """Turn a path seen in a build log into a safe repository-relative path.

    Debian build logs write the source tree as ``/<<PKGBUILDDIR>>/`` or
    ``/<<BUILDDIR>>/<package-dir>/``; those prefixes are removed, as are quotes and a
    leading ``./``.

    Args:
        value: The raw path text matched in the log.

    Returns:
        The relative path with forward slashes, or None if it is empty, absolute, or
        contains empty, ``.`` or ``..`` parts.
    """
    path = value.replace("\\", "/").strip("'\"‘’")
    if path.startswith("/<<PKGBUILDDIR>>/"):
        path = path.removeprefix("/<<PKGBUILDDIR>>/")
    elif path.startswith("/<<BUILDDIR>>/"):
        remainder = path.removeprefix("/<<BUILDDIR>>/")
        path = remainder.split("/", 1)[1] if "/" in remainder else ""
    path = path.removeprefix("./")
    if not path or path.startswith("/") or any(part in {"", ".", ".."} for part in path.split("/")):
        return None
    return path


def identify_primary_file(diagnostic: str, causal_window: str = "") -> str | None:
    """Find the repository file named by the diagnostic or the lines around it.

    The diagnostic is searched first, then the causal window, line by line, with
    patterns for ``[file:line]`` references, ``file.c:12`` style compiler locations,
    ``python script.py`` invocations, ``CMake Error at`` locations and Debian
    ``cannot access 'debian/...'`` messages.

    Args:
        diagnostic: The primary diagnostic line.
        causal_window: Log lines around the diagnostic. Empty means only the
            diagnostic is searched.

    Returns:
        The first match as a repository-relative path, or None if no usable path
        was found.
    """
    for text in (diagnostic, causal_window):
        for line in text.splitlines():
            for pattern in _FILE_PATTERNS:
                match = pattern.search(line)
                if match:
                    normalized = _normalize_repository_path(match.group("path"))
                    if normalized:
                        return normalized
    return None


def _causal_window(normalized: str, index: int, radius: int = 3) -> str:
    """Return the lines around the primary diagnostic.

    Args:
        normalized: The output of ``normalize_log``.
        index: Index of the primary diagnostic line.
        radius: Number of lines to include on each side.

    Returns:
        Up to ``2 * radius + 1`` lines centred on ``index``, joined with newlines.
    """
    lines = normalized.splitlines()
    return "\n".join(lines[max(0, index - radius) : min(len(lines), index + radius + 1)])


def analyze_build_log(raw_log: str) -> FailureAnalysis:
    """Analyze a failed build log from start to finish.

    The log is normalized, the primary diagnostic is chosen, the failure is
    classified against the diagnostic plus the noise-filtered log, and the error
    signature, an 11-line causal window and the causal file are worked out for the
    failure frontier.

    Args:
        raw_log: The build log text as read from the Case.

    Returns:
        The analysis: normalized log, primary diagnostic, primary and secondary
        failure families, failure frontier and classification rationale.
    """
    normalized = normalize_log(raw_log)
    diagnostic, index = identify_primary_diagnostic(normalized)
    family, secondaries, rationale = classify_failure(
        _matchable(diagnostic), _classification_context(normalized)
    )
    signature = normalize_error_signature(_matchable(diagnostic))
    causal_window = _causal_window(normalized, index, radius=5)
    frontier = frontier_from_family(
        family,
        signature=signature,
        excerpt=causal_window,
        primary_file=identify_primary_file(diagnostic, causal_window),
    )
    return FailureAnalysis(
        normalized_log=normalized,
        primary_diagnostic=diagnostic,
        family=family,
        secondary_families=secondaries,
        frontier=frontier,
        rationale=rationale,
    )
