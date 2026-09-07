"""Layered extraction of the earliest useful causal build diagnostic."""

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
_PREFIX = re.compile(r"^(?:stdout|stderr|error|warning)\s*[|:]\s*", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class DiagnosticPattern:
    regex: re.Pattern[str]
    causal_weight: int


_PATTERNS = (
    DiagnosticPattern(re.compile(r"fatal error:.*(?:not found|no such file)", re.I), 100),
    DiagnosticPattern(re.compile(r"(?:unrecognized|unknown|unsupported|bad value).*(?:option|argument|march|mcpu|mabi|-m)", re.I), 100),
    DiagnosticPattern(re.compile(r"(?:relocation .*can not be used|recompile with -fPIC)", re.I), 99),
    DiagnosticPattern(re.compile(r"build killed with signal|after \d+ minutes? of inactivity", re.I), 99),
    DiagnosticPattern(re.compile(r"(?:unknown|invalid).*(?:instruction|register|architecture|target)", re.I), 98),
    DiagnosticPattern(re.compile(r"\bBFD\b.*assertion fail", re.I), 98),
    DiagnosticPattern(re.compile(r"(?:intrinsic|builtin).*(?:mismatch|requires|undeclared|not available)", re.I), 96),
    DiagnosticPattern(re.compile(r"(?:error:\s*invalid command|(?:command|executable).*not found)", re.I), 96),
    DiagnosticPattern(re.compile(r"can't exec .*no such file", re.I), 96),
    DiagnosticPattern(re.compile(r"(?:cannot|can't) (?:access|stat|get).*(?:no such file|not found|info from)", re.I), 95),
    DiagnosticPattern(re.compile(r"(?:cannot find \(any matches for\)|failed to remove .*no such file)", re.I), 95),
    DiagnosticPattern(re.compile(r"(?:ModuleNotFoundError|ImportError|FileNotFoundError|AttributeError|AssertionError):", re.I), 95),
    DiagnosticPattern(re.compile(r"(?:undefined reference|undefined symbol).*(?:atomic|__atomic|__sync)", re.I), 94),
    DiagnosticPattern(re.compile(r"dpkg-gensymbols: error:.*(?:disappeared|symbols file)", re.I), 93),
    DiagnosticPattern(re.compile(r"(?:undefined reference|undefined symbol)", re.I), 90),
    DiagnosticPattern(re.compile(r"(?:cannot find -l|library not found|missing package)", re.I), 88),
    DiagnosticPattern(re.compile(r"configure:(?: error|.*failed)", re.I), 86),
    DiagnosticPattern(re.compile(r"(?:^FAIL:|^FAILED\b|Failed test|failed \d+ tests?|Illegal instruction|Segmentation fault|differ: byte)", re.I), 84),
    DiagnosticPattern(re.compile(r"(?:reference .*not found in the current environment|makes inconsistent assumptions over library)", re.I), 82),
    DiagnosticPattern(re.compile(r"GCS is required by -z gcs.*lacks the necessary", re.I), 82),
    # Tool wrappers are consequences, not the underlying diagnostic. Keep them as fallbacks
    # but score them below a concrete compiler, linker, packaging, or test failure.
    DiagnosticPattern(re.compile(r"(?:dpkg-buildpackage|dh_(?:auto_\w+|autoreconf)|collect2): error:", re.I), 25),
    DiagnosticPattern(re.compile(r"(?:make|ninja).*\*\*\*|exit status|returned exit (?:code|status)", re.I), 15),
    DiagnosticPattern(re.compile(r"(?:error:|fatal:|failed:)", re.I), 60),
    DiagnosticPattern(re.compile(r"rpmbuild.*\*\*\*", re.I), 10),
)

_NON_CAUSAL = (
    re.compile(r"(?:^|[#\s])(?:ERROR|FAIL|XFAIL):\s*0(?:\s|$)", re.I),
    re.compile(r"LaTeX Warning:.*undefined references", re.I),
    re.compile(r"warning: failed to verify signature", re.I),
    re.compile(r"expected error:", re.I),
)

_FILE_PATTERNS = (
    re.compile(r"\[(?P<path>[A-Za-z0-9_.+@/-]+):\d+(?::[^\]]+)?\]"),
    re.compile(
        r"(?P<path>(?:/<<PKGBUILDDIR>>/|/<<BUILDDIR>>/[^/\s]+/)?"
        r"[A-Za-z0-9_.+@/-]+\.(?:c|cc|cpp|cxx|h|hh|hpp|rs|go|py|java|vala|m|mm|s|S))"
        r":\d+"
    ),
    re.compile(r"(?:python\d*(?:\.\d+)?\s+)(?P<path>[A-Za-z0-9_.+@/-]+\.py)\b"),
    re.compile(r"(?:cannot|can't) (?:access|stat)\s+['\"‘](?P<path>debian/[^'\"’]+)", re.I),
)


def normalize_log(raw_log: str) -> str:
    """Remove display noise while preserving diagnostic order and unique content."""
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


def identify_primary_diagnostic(normalized_log: str) -> tuple[str, int]:
    """Choose an early high-causality diagnostic instead of a final cascade line."""
    best_line = ""
    best_score = -1
    best_index = 0
    lines = normalized_log.splitlines()
    for index, line in enumerate(lines):
        if any(pattern.search(line) for pattern in _NON_CAUSAL):
            continue
        for pattern in _PATTERNS:
            if pattern.regex.search(line):
                early_bonus = max(0, 20 - index // 20)
                score = pattern.causal_weight + early_bonus
                if score > best_score:
                    best_line, best_score, best_index = line, score, index
                break
    if not best_line and lines:
        best_index = max(0, len(lines) - 1)
        best_line = lines[best_index]
    return best_line, best_index


def _normalize_repository_path(value: str) -> str | None:
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
    """Extract a repository-relative causal file when build output names one."""
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
    lines = normalized.splitlines()
    return "\n".join(lines[max(0, index - radius) : min(len(lines), index + radius + 1)])


def analyze_build_log(raw_log: str) -> FailureAnalysis:
    """Return a deterministic failure family, frontier, and normalized signature."""
    normalized = normalize_log(raw_log)
    diagnostic, index = identify_primary_diagnostic(normalized)
    family, secondaries, rationale = classify_failure(diagnostic, normalized)
    signature = normalize_error_signature(diagnostic)
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
