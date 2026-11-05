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
# Open Build Service logs prefix every line with elapsed seconds, e.g. "[   43s] ".
_OBS_ELAPSED = re.compile(r"^\[\s*\d+s\]\s?")
# OBS prints this marker once the build root is ready and rpmbuild starts. Diagnostics
# before it come from preparing the build root, so a failure inside the build wins over them.
_OBS_BUILD_START = re.compile(r"^-{5,}\s+building\s+\S+(?:\s+\(user\s+\S+\))?\s*$", re.I)
_PREFIX = re.compile(r"^(?:stdout|stderr|error|warning)\s*[|:]\s*", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class DiagnosticPattern:
    regex: re.Pattern[str]
    causal_weight: int


_PATTERNS = (
    DiagnosticPattern(re.compile(r"fatal error:.*(?:not found|no such file)", re.I), 100),
    DiagnosticPattern(
        re.compile(
            r"(?:^|[/\\])(?:cc1|lto1|gcc|g\+\+|clang|clang\+\+|xgcc): "
            r"(?:fatal |internal compiler )?error:",
            re.I,
        ),
        100,
    ),
    DiagnosticPattern(
        re.compile(r"(?:^|\s)[^\s:]+:\d+(?::\d+)?: (?:fatal )?error:", re.I), 100
    ),
    DiagnosticPattern(re.compile(r"(?:^|\s)error\[E\d+\]:", re.I), 100),
    DiagnosticPattern(re.compile(r"(?:^|\s)(?:as|nasm):.*\bError:", re.I), 99),
    DiagnosticPattern(re.compile(r"(?:unrecognized|unknown|unsupported|bad value).*(?:option|argument|march|mcpu|mabi|-m)", re.I), 100),
    DiagnosticPattern(re.compile(r"(?:relocation .*can not be used|recompile with -fPIC)", re.I), 99),
    DiagnosticPattern(
        re.compile(
            r"build killed with signal|after \d+\s*(?:minutes?|mins?|hours?|hrs?) of inactivity|"
            r"job seems to be stuck here, killed",
            re.I,
        ),
        99,
    ),
    DiagnosticPattern(re.compile(r"\bOut of memory: Killed process\b", re.I), 99),
    # rpmbuild's own stage errors are causal and must outrank soft-failed test output.
    DiagnosticPattern(
        re.compile(
            r"^(?:error: )?(?:(?:File|Directory) not found(?: by glob)?: /\S*BUILDROOT|"
            r"Installed \(but unpackaged\) file\(s\) found|Failed build dependencies)|"
            r"^error: line \d+: ",
            re.I,
        ),
        97,
    ),
    DiagnosticPattern(re.compile(r"\b\d+ out of \d+ hunks? FAILED\b", re.I), 95),
    DiagnosticPattern(re.compile(r"\bDepends: \S+(?: \([^)]*\))? but (?:it is not (?:installable|going to be installed)|\S+ is to be installed)", re.I), 92),
    DiagnosticPattern(re.compile(r"\bE: badness \d+ exceeds threshold\b", re.I), 90),
    DiagnosticPattern(re.compile(r"^\s*\d+/\d+\s+\S.*?\s+FAIL\s+\d", re.I), 90),
    DiagnosticPattern(re.compile(r"\.\w+:\d+: Failure$|^\[\s+FAILED\s+\]\s+\w", re.I), 88),
    DiagnosticPattern(re.compile(r"^--- FAIL: \S+", re.I), 88),
    DiagnosticPattern(re.compile(r":\s*line \d+: .*: No such file or directory\s*$", re.I), 80),
    DiagnosticPattern(re.compile(r"(?:unknown|invalid).*(?:instruction|register|architecture|target)", re.I), 98),
    DiagnosticPattern(re.compile(r"\bBFD\b.*assertion fail", re.I), 98),
    DiagnosticPattern(re.compile(r"(?:intrinsic|builtin).*(?:mismatch|requires|undeclared|not available)", re.I), 96),
    DiagnosticPattern(
        re.compile(
            r"(?:error:\s*invalid command|:\s*command not found|"
            r"(?:command|executable)\s+['\"`]?[-A-Za-z0-9_./+]+['\"`]?\s+"
            r"(?:was\s+)?not found)",
            re.I,
        ),
        96,
    ),
    DiagnosticPattern(re.compile(r"can't exec .*no such file", re.I), 96),
    DiagnosticPattern(re.compile(r"(?:cannot|can't) (?:access|stat|get).*(?:no such file|not found|info from)", re.I), 95),
    DiagnosticPattern(re.compile(r"(?:cannot find \(any matches for\)|failed to remove .*no such file)", re.I), 95),
    DiagnosticPattern(re.compile(r"(?:ModuleNotFoundError|ImportError|FileNotFoundError|AttributeError|AssertionError):", re.I), 95),
    DiagnosticPattern(re.compile(r"(?:assertion [`'][^`']+[`'] failed|assertion failed:|child killed by signal)", re.I), 95),
    DiagnosticPattern(re.compile(r"(?:^|\s).*found issues in", re.I), 97),
    DiagnosticPattern(re.compile(r"(?:^|\s).*(?:test failure|failed unexpectedly)", re.I), 90),
    DiagnosticPattern(re.compile(r"(?:undefined reference|undefined symbol).*(?:atomic|__atomic|__sync)", re.I), 94),
    DiagnosticPattern(re.compile(r"dpkg-gensymbols: error:.*(?:disappeared|symbols file)", re.I), 93),
    DiagnosticPattern(re.compile(r"dpkg-(?:deb|gencontrol): error:", re.I), 93),
    DiagnosticPattern(re.compile(r"(?:^|\s)CMake Error(?: at|:)", re.I), 98),
    DiagnosticPattern(re.compile(r"(?:undefined reference|undefined symbol)", re.I), 90),
    DiagnosticPattern(re.compile(r"(?:cannot find -l|library not found|missing package)", re.I), 88),
    DiagnosticPattern(
        re.compile(r"\bnothing provides \S+(?:\s*[<>=]+\s*\S+)?(?: needed by\b|\s*$)", re.I), 72
    ),
    DiagnosticPattern(re.compile(r"^(?:error:\s*)?unresolvable\b", re.I), 70),
    DiagnosticPattern(re.compile(r"configure:(?: error|.*failed)", re.I), 86),
    DiagnosticPattern(re.compile(r"(?:^FAIL:|^FAILED\b|Failed test|failed \d+ tests?|error: test failed|Illegal instruction|Segmentation fault|differ: byte)", re.I), 84),
    DiagnosticPattern(re.compile(r"(?:reference .*not found in the current environment|makes inconsistent assumptions over library)", re.I), 82),
    DiagnosticPattern(re.compile(r"GCS is required by -z gcs.*lacks the necessary", re.I), 82),
    # Tool wrappers are consequences, not the underlying diagnostic. Keep them as fallbacks
    # but score them below a concrete compiler, linker, packaging, or test failure.
    DiagnosticPattern(re.compile(r"(?:dpkg-buildpackage|dh_(?:auto_\w+|autoreconf)|collect2): error:", re.I), 25),
    DiagnosticPattern(re.compile(r"(?:make|ninja).*\*\*\*|exit status|returned exit (?:code|status)", re.I), 15),
    DiagnosticPattern(re.compile(r"(?:error:|fatal:|failed:)", re.I), 60),
    DiagnosticPattern(re.compile(r"rpmbuild.*\*\*\*", re.I), 10),
)

_NON_CAUSAL: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:^|[#\s])(?:ERROR|FAIL|XFAIL):\s*0(?:\s|$)", re.I),
    re.compile(r"LaTeX Warning:.*undefined references", re.I),
    re.compile(r"\.tex:\d+:\s*Warning:\s*Command not found:", re.I),
    re.compile(r"warning: failed to verify signature", re.I),
    re.compile(r"(?:libtool: link: warning: undefined symbols|configure: WARNING:)", re.I),
    re.compile(r"expected error:", re.I),
    re.compile(r":\s*note:.*may have been intended to silence earlier diagnostics", re.I),
    re.compile(
        r"^(?:\|\s*)?[-A-Za-z0-9_+.]+:\s+error:\s+unrecognized command-line "
        r"option\s+['\"‘’]-(?:V|qversion|version)['\"‘’]",
        re.I,
    ),
    re.compile(r"^(?:\|\s*)?[-A-Za-z0-9_+.]+:\s+fatal error:\s+no input files\s*$", re.I),
    re.compile(r"^(?:\|\s*)?conftest\.[A-Za-z0-9]+:\d+(?::\d+)?:\s+(?:fatal\s+)?error:", re.I),
    re.compile(r"^\|\s*conftest\.[A-Za-z0-9]+:\d+(?::\d+)?:\s+warning:", re.I),
    re.compile(r"^\|\s*configure:\s+failed program was:", re.I),
    re.compile(
        r"^\|\s*[-A-Za-z0-9_+.]+:\s+(?:fatal\s+|internal compiler\s+)?error:",
        re.I,
    ),
    # Released Launchpad logs include complete environment/package inventories and echoed
    # compiler/Cargo commands. Words such as "error" can occur inside their metadata and
    # must not outrank the later diagnostic emitted by the command.
    re.compile(r"^(?:Inherited environment:|Package versions:)", re.I),
    re.compile(r"^\s*Running [`'](?:CARGO|rustc)=", re.I),
    re.compile(
        r"^\s*['\"]?\s*CARGO_PKG_.*(?:\brustc\b.*--error-format|build-script-build)",
        re.I,
    ),
    re.compile(r"^(?:/bin/bash\s+)?(?:\./)?libtool\b.*--mode=(?:compile|link)\b", re.I),
    re.compile(
        r"^libtool:\s+(?:compile|link):\s+(?:/\S+/)?"
        r"(?:cc|c\+\+|gcc|g\+\+|clang|clang\+\+)(?=\s|$)",
        re.I,
    ),
    re.compile(
        r"^\s*cd\s+\S+\s+&&\s+(?:/\S+/)?"
        r"(?:cc|c\+\+|gcc|g\+\+|clang|clang\+\+)(?=\s|$)",
        re.I,
    ),
    re.compile(
        r"^\s*(?:/\S+/)?(?:cc|c\+\+|gcc|g\+\+|clang|clang\+\+)(?=\s|$)\s+"
        r"(?=.*(?:\s-[DIOWfgm]|\s-o\s))",
        re.I,
    ),
    re.compile(r"^checking (?:for|if|whether)\b", re.I),
    # Python logging records ("ERROR:root:...") in test output are not build diagnostics.
    re.compile(r"^(?:ERROR|WARNING|INFO|DEBUG):[\w.]+:", re.I),
)

_OBS_NOISE: tuple[re.Pattern[str], ...] = (
    # Noise from OBS build-root preparation on hosted Validators (chkstat ownership
    # checks and package %post scriptlets) that does not stop the build.
    re.compile(r"^ERROR: not all operations were successful\.?$", re.I),
    re.compile(r"has unexpected owner \(\d+\)\. Refusing to correct", re.I),
    re.compile(r"^(?:warning:\s*)?%(?:pre|post)(?:un|trans)?\([^)]*\) scriptlet failed", re.I),
    re.compile(r"^can't open /etc/polkit-1/", re.I),
    # Final status lines written by the OBS wrapper and the hosted runner summarize the
    # failure; they carry a host id and clock time but no cause.
    re.compile(r"^\[build-case\]"),
    re.compile(r"^[0-9a-f]{12} failed \"build \S+\" at ", re.I),
)
_NON_CAUSAL = _NON_CAUSAL + _OBS_NOISE
_REPEATED = re.compile(r" \[repeated \d+ times\]$")


def _matchable(line: str) -> str:
    """Remove the normalizer's repetition marker so anchored patterns still apply."""
    return _REPEATED.sub("", line)


def _is_non_causal(line: str) -> bool:
    probe = _matchable(line)
    return any(pattern.search(probe) for pattern in _NON_CAUSAL)


def _classification_context(normalized: str) -> str:
    """Drop OBS host noise so it cannot lend a family to an unrelated diagnostic."""
    return "\n".join(
        line
        for line in normalized.splitlines()
        if not any(pattern.search(_matchable(line)) for pattern in _OBS_NOISE)
    )

_FILE_PATTERNS = (
    re.compile(r"\[(?P<path>[A-Za-z0-9_.+@/-]+):\d+(?::[^\]]+)?\]"),
    re.compile(
        r"(?P<path>(?:/<<PKGBUILDDIR>>/|/<<BUILDDIR>>/[^/\s]+/)?"
        r"[A-Za-z0-9_.+@/-]+\.(?:c|cc|cpp|cxx|h|hh|hpp|rs|go|py|java|vala|m|mm|s|S))"
        r":\d+"
    ),
    re.compile(r"(?:python\d*(?:\.\d+)?\s+)(?P<path>[A-Za-z0-9_.+@/-]+\.py)\b"),
    re.compile(r"CMake Error at (?P<path>[A-Za-z0-9_.+@/-]+):\d+", re.I),
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
    for index, line in enumerate(lines):
        if _OBS_BUILD_START.search(line):
            return index
    return 0


def identify_primary_diagnostic(normalized_log: str) -> tuple[str, int]:
    """Choose an early high-causality diagnostic instead of a final cascade line."""
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
    family, secondaries, rationale = classify_failure(
        _matchable(diagnostic), _classification_context(normalized)
    )
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
