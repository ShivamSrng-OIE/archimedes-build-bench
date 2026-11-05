"""Hand-written repair rules for common cross-architecture build failures.

Each rule needs one specific signal in the normalized failure log, then looks for the
unique repository text that causes it, and returns exact-text edits. When the evidence
is missing or ambiguous (nothing found, or more than one place), a rule returns None
instead of guessing. Rules never look at Case IDs or package names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..schemas import Architecture, CaseData, FailureAnalysis, PlannedEdit, RepairFamily
from ..workspace.repository_files import iter_repository_files


@dataclass(frozen=True, slots=True)
class DeterministicMatch:
    """A repair produced by one of the hand-written rules.

    Attributes:
        family: The kind of repair, used by the critics and the candidate ranker.
        rationale: A short explanation of the change, copied into each edit.
        edits: The exact-text edits to apply, at most one per file.
        confidence: How sure the rule is that the edit fixes the failure, from 0 to 1.
        risk: How likely the edit is to cause harm elsewhere, from 0 to 1.
    """

    family: RepairFamily
    rationale: str
    edits: tuple[PlannedEdit, ...]
    confidence: float = 0.90
    risk: float = 0.10


def _text_files(repo: Path) -> list[tuple[str, str]]:
    """Read every text file in the repository that the rules may search.

    Files over 2,000,000 bytes, files with a NUL byte in the first 8 KiB, files that
    are not valid UTF-8 and files that cannot be read are skipped.

    Args:
        repo: The root of the package worktree.

    Returns:
        Pairs of (repository-relative POSIX path, file text), in the deterministic
        order produced by ``iter_repository_files``.
    """
    files: list[tuple[str, str]] = []
    for path in iter_repository_files(repo):
        try:
            if path.stat().st_size > 2_000_000:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeError:
            continue
        files.append((path.relative_to(repo).as_posix(), text))
    return files


def _edit(path: str, expected: str, replacement: str, rationale: str) -> PlannedEdit:
    """Build a ``replace_lines`` edit that swaps one exact piece of text.

    Args:
        path: Repository-relative path of the file to change.
        expected: The text that must occur exactly once in the file.
        replacement: The text to put in its place.
        rationale: Why the edit is made.

    Returns:
        The planned edit.
    """
    return PlannedEdit(
        operation="replace_lines",
        path=path,
        expected_text=expected,
        replacement=replacement,
        rationale=rationale,
    )


def _unsigned_digest_identifier(
    files: list[tuple[str, str]], case: CaseData
) -> tuple[PlannedEdit, ...]:
    """Switch a generated digest array from plain ``char`` to ``unsigned char``.

    This is a companion to the Debian multiarch rule and runs only after that rule
    has seen a CMake ``find_package`` failure. On aarch64 and riscv64 plain ``char``
    is unsigned, so the negative values a Python generator writes into a
    ``static const char`` array no longer fit, and the C++ braced initializer fails
    as a narrowing conversion. Once configuration is fixed, compilation would hit
    exactly that.

    The rule needs, across the whole repository, exactly one generator file holding
    the ``digest_char = [e if e < 128 else e-256 ...]`` snippet, exactly one
    ``StringRef NAME() { return ARRAY; }`` definition, exactly one file with the
    ``llvm::StringRef NAME();`` declaration and exactly one line that is just
    ``addString(H, NAME());``. It then makes four edits: the generator prints an
    ``unsigned char`` array, the definition and declaration return
    ``ArrayRef<unsigned char>``, and the consumer calls ``H.update(NAME());``.

    Args:
        files: The repository text files from ``_text_files``.
        case: The Case, used for its source and target architectures.

    Returns:
        The four edits, or an empty tuple when the source is not x86_64, the target
        is not aarch64 or riscv64, or any of the pieces is missing or not unique.
    """
    if (
        case.source_architecture.canonical is not Architecture.X86_64
        or case.target_architecture.canonical
        not in {Architecture.AARCH64, Architecture.RISCV64}
    ):
        return ()

    generator_expected = (
        "    digest_char = [e if e < 128 else e-256 for e in digest_uchar]\n"
        '    digest_elts = ", ".join(map(str, digest_char))\n'
        '    print(f"static const char {args.varname}[] = '
        "{{{digest_elts}, 0}};\", file=open(args.output, 'w'))"
    )
    generator_replacement = (
        '    digest_elts = ", ".join(map(str, digest_uchar))\n'
        '    print(f"static const unsigned char {args.varname}[] = '
        "{{{digest_elts}, 0}};\", file=open(args.output, 'w'))"
    )
    normalized = [
        (path, text.replace("\r\n", "\n").replace("\r", "\n")) for path, text in files
    ]
    generators = [
        path for path, text in normalized if text.count(generator_expected) == 1
    ]
    functions: list[tuple[str, str, str, str]] = []
    function_pattern = re.compile(
        r"(?m)^(?P<expected>StringRef[ \t]+(?P<name>[A-Za-z_]\w*)\(\)[ \t]*"
        r"\{[ \t]*return[ \t]+(?P<array>[A-Za-z_]\w*);[ \t]*\})$"
    )
    for path, text in normalized:
        for match in function_pattern.finditer(text):
            functions.append(
                (
                    path,
                    match.group("expected"),
                    match.group("name"),
                    match.group("array"),
                )
            )
    if len(generators) != 1 or len(functions) != 1:
        return ()

    function_path, function_expected, function_name, array_name = functions[0]
    declaration_expected = f"llvm::StringRef {function_name}();"
    consumer_expected = f"addString(H, {function_name}());"
    declarations = [
        path for path, text in normalized if text.count(declaration_expected) == 1
    ]
    consumers: list[tuple[str, str]] = []
    for path, text in normalized:
        for line in text.splitlines():
            if line.strip() == consumer_expected:
                consumers.append((path, line))
    if len(declarations) != 1 or len(consumers) != 1:
        return ()

    rationale = (
        "propagate generated digest bytes as unsigned data through the discovered declaration, "
        "definition, and hash consumer so values compile consistently when plain char is unsigned"
    )
    consumer_path, consumer_line = consumers[0]
    return (
        _edit(generators[0], generator_expected, generator_replacement, rationale),
        _edit(
            consumer_path,
            consumer_line,
            consumer_line.replace(consumer_expected, f"H.update({function_name}());"),
            rationale,
        ),
        _edit(
            function_path,
            function_expected,
            f"ArrayRef<unsigned char> {function_name}() {{ return {array_name}; }}",
            rationale,
        ),
        _edit(
            declarations[0],
            declaration_expected,
            f"llvm::ArrayRef<unsigned char> {function_name}();",
            rationale,
        ),
    )


def _debian_multiarch_path(
    files: list[tuple[str, str]], log: str, case: CaseData
) -> DeterministicMatch | None:
    """Replace a hard-coded source-architecture multiarch triplet in Debian packaging.

    Log signal: either a ``cannot access``/``cannot stat`` or ``cannot find (any
    matches for)`` message naming a path with the source triplet (for example
    ``x86_64-linux-gnu``), or a CMake ``find_package`` "Could not find a package
    configuration file" error together with a ``-D<NAME>_DIR=`` option in the log that
    contains the source triplet.

    If exactly one line of ``debian/rules`` contains the source triplet, the triplet
    is replaced with ``$(shell dpkg-architecture -qDEB_HOST_MULTIARCH)``. For the
    CMake signal, the unsigned-digest companion edits are added when they match.

    Otherwise the package must use debhelper compat 13 or later (read from
    ``debhelper-compat (= N)`` in ``debian/control``, else from ``debian/compat``),
    because only then do ``.install`` files expand ``${DEB_HOST_MULTIARCH}``. Every
    ``debian/*.install`` line that contains the source triplet and whose text appears
    verbatim in the log gets the triplet replaced with that variable.

    Args:
        files: The repository text files from ``_text_files``.
        log: The normalized failure log.
        case: The Case, used for its source and target architectures.

    Returns:
        The match, or None when the source and target are not x86_64 and aarch64 in
        some order, the log has neither signal, or ``debian/rules`` has no unique
        triplet line and the compat level is below 13 or no ``.install`` line is
        quoted in the log.
    """
    triplets = {
        Architecture.X86_64: "x86_64-linux-gnu",
        Architecture.AARCH64: "aarch64-linux-gnu",
    }
    source_triplet = triplets.get(case.source_architecture.canonical)
    target_triplet = triplets.get(case.target_architecture.canonical)
    if not source_triplet or not target_triplet or source_triplet == target_triplet:
        return None
    missing_path = re.search(
        rf"(?:(?:cannot|can't) (?:access|stat).*{re.escape(source_triplet)}.*"
        rf"(?:no such file|not found)|cannot find \(any matches for\).*"
        rf"{re.escape(source_triplet)})",
        log,
        re.IGNORECASE,
    )
    missing_cmake_package = re.search(
        r"CMake Error at .*?\(find_package\):.*?Could not find a package "
        r"configuration file provided by",
        log,
        re.IGNORECASE | re.DOTALL,
    ) and re.search(
        rf"-D[A-Za-z0-9_]+_DIR=\S*{re.escape(source_triplet)}\S*",
        log,
        re.IGNORECASE,
    )
    if missing_path is None and missing_cmake_package is None:
        return None
    rules_matches: list[tuple[str, str]] = []
    for path, text in files:
        if path != "debian/rules":
            continue
        for line in text.splitlines():
            if source_triplet in line:
                rules_matches.append((path, line))
    if len(rules_matches) == 1 and (
        missing_path is not None or missing_cmake_package is not None
    ):
        path, expected = rules_matches[0]
        replacement = expected.replace(
            source_triplet, "$(shell dpkg-architecture -qDEB_HOST_MULTIARCH)"
        )
        rationale = (
            "derive the Debian multiarch directory from the target build architecture instead of "
            f"hard-coding the source architecture triplet {source_triplet}"
        )
        planned_edits = [_edit(path, expected, replacement, rationale)]
        if missing_cmake_package is not None:
            companion_edits = _unsigned_digest_identifier(files, case)
            if companion_edits:
                planned_edits.extend(companion_edits)
                rationale += (
                    "; also propagate the structurally discovered generated digest as unsigned "
                    "bytes before configuration advances to target compilation"
                )
        return DeterministicMatch(
            RepairFamily.PACKAGING_RULE,
            rationale,
            tuple(planned_edits),
            confidence=0.97,
            risk=0.03,
        )

    file_map = dict(files)
    control = file_map.get("debian/control", "")
    compat_match = re.search(r"debhelper-compat\s*\(=\s*(\d+)\s*\)", control, re.IGNORECASE)
    compat = int(compat_match.group(1)) if compat_match else 0
    legacy_compat = file_map.get("debian/compat", "").strip()
    if not compat and legacy_compat.isdigit():
        compat = int(legacy_compat)
    if compat < 13:
        return None

    manifest_matches: list[tuple[str, str]] = []
    for path, text in files:
        if not path.startswith("debian/") or not path.endswith(".install"):
            continue
        for line in text.splitlines():
            token = line.strip()
            if source_triplet in token and token in log:
                manifest_matches.append((path, line))
    if not manifest_matches:
        return None
    rationale = (
        "use debhelper's compat-13 target multiarch substitution for install paths that the "
        "failure log explicitly reports missing"
    )
    manifest_edits = tuple(
        _edit(
            path,
            expected,
            expected.replace(source_triplet, "${DEB_HOST_MULTIARCH}"),
            rationale,
        )
        for path, expected in manifest_matches
    )
    return DeterministicMatch(
        RepairFamily.PACKAGING_RULE,
        rationale,
        manifest_edits,
        confidence=0.97,
        risk=0.03,
    )


_DEBIAN_ARCH = {
    Architecture.X86_64: "amd64",
    Architecture.AARCH64: "arm64",
    Architecture.RISCV64: "riscv64",
}
# dpkg-gensymbols prints a unified diff between the packaged symbols file and what the
# build produced. Its header reads "--- debian/libfoo1.symbols (libfoo1_1.2-3_arm64)",
# a vanished symbol is a "-" line, and the same symbol comes back as a
# "+#MISSING: <version># <original line>" line. _SYMBOLS_LINE matches one symbol entry
# with its optional "(tag|tag)" prefix. It requires leading whitespace, so library header
# lines and the column-0 "|" and "*" lines never match, and it skips indented comments.
_SYMBOLS_ERROR = re.compile(
    r"^dpkg-gensymbols: error: some symbols or patterns disappeared", re.MULTILINE
)
_SYMBOLS_HEADER = re.compile(
    r"^--- (?P<path>debian/[A-Za-z0-9_.+-]+\.symbols(?:\.[a-z0-9-]+)?) "
    r"\(\S+_(?P<arch>[a-z0-9]+)\)$"
)
_SYMBOLS_MISSING = re.compile(r"^\+#MISSING: [^#\s]+#(?P<line>\s.*)$")
_SYMBOLS_LINE = re.compile(r"^(?P<lead>[ \t]+)(?:\((?P<tags>[^()]*)\))?(?P<rest>[^\s(#|*].*)$")
_MAX_DISAPPEARED_PER_FILE = 200
_MAX_DISAPPEARED_FRACTION = 0.25


def _exclude_symbol_arch(line: str, arch: str) -> str | None:
    """Tag a symbols-file line so dpkg-gensymbols stops expecting it on one architecture.

    A line without an ``arch=`` tag gets ``arch=!<arch>`` added to its tag list (for
    example `` foo@Base 1.0`` becomes `` (arch=!arm64)foo@Base 1.0``). A negated list
    such as ``arch=!armel`` gets ``!<arch>`` appended. A positive list such as
    ``arch=amd64 arm64`` loses ``<arch>``.

    Args:
        line: The symbol line as it appears in the symbols file, without the newline.
        arch: The Debian architecture name to exclude, such as ``arm64``.

    Returns:
        The tagged line, or None if the line is not a symbol entry, has more than one
        ``arch=`` tag, mixes negated and positive values, already excludes ``arch``,
        has a positive list that does not include ``arch``, or would be left with an
        empty list.
    """
    match = _SYMBOLS_LINE.match(line)
    if match is None:
        return None
    tags = [tag for tag in (match.group("tags") or "").split("|") if tag]
    arch_tags = [index for index, tag in enumerate(tags) if tag.startswith("arch=")]
    if len(arch_tags) > 1:
        return None
    if not arch_tags:
        tags.append(f"arch=!{arch}")
    else:
        index = arch_tags[0]
        values = tags[index][len("arch="):].split()
        negated = [value.startswith("!") for value in values]
        if values and all(negated):
            if f"!{arch}" in values:
                return None
            values.append(f"!{arch}")
        elif values and not any(negated):
            if arch not in values:
                return None
            values = [value for value in values if value != arch]
            if not values:
                return None
        else:
            return None
        tags[index] = "arch=" + " ".join(values)
    return f"{match.group('lead')}({'|'.join(tags)}){match.group('rest')}"


def _only_target_arch(line: str, arch: str) -> bool:
    """Check whether a symbol is declared for exactly one architecture, ``arch``.

    Args:
        line: The symbol line as it appears in the symbols file.
        arch: The Debian architecture name, such as ``arm64``.

    Returns:
        True if the line's only ``arch=`` tag is exactly ``arch=<arch>``.
    """
    match = _SYMBOLS_LINE.match(line)
    if match is None:
        return False
    tags = [tag for tag in (match.group("tags") or "").split("|") if tag]
    return [tag for tag in tags if tag.startswith("arch=")] == [f"arch={arch}"]


def _disappeared_symbols(log: str) -> dict[str, tuple[str, dict[str, str]]]:
    """Collect the symbols that dpkg-gensymbols reported as disappeared.

    Each diff starts at a ``--- debian/<pkg>.symbols (<pkg>_<version>_<arch>)`` header
    followed by a ``+++ dpkg-gensymbols`` line and runs until a line that is not part
    of a hunk. A removed line counts only when the diff also has the matching
    ``+#MISSING:`` line for it. Diffs for the same file are merged.

    Args:
        log: The normalized failure log.

    Returns:
        A mapping from symbols-file path to a pair of (Debian build architecture,
        {removed symbol line: dpkg's ``#MISSING:`` line}). It is empty when no diff was
        found, or when the same file is reported for two different architectures,
        since then it is unclear which one to exclude.
    """
    reported: dict[str, tuple[str, dict[str, str]]] = {}
    lines = log.splitlines()
    index = 0
    while index < len(lines):
        header = _SYMBOLS_HEADER.match(lines[index])
        if header is None or index + 1 >= len(lines) or not lines[index + 1].startswith(
            "+++ dpkg-gensymbols"
        ):
            index += 1
            continue
        removed: list[str] = []
        missing: dict[str, str] = {}
        index += 2
        while index < len(lines) and lines[index][:1] in {"@", " ", "-", "+"}:
            line = lines[index]
            if line.startswith("--- "):
                break
            if line.startswith("-"):
                removed.append(line[1:])
            else:
                marker = _SYMBOLS_MISSING.match(line)
                if marker is not None:
                    missing[marker.group("line")] = line[1:]
            index += 1
        path, arch = header.group("path"), header.group("arch")
        if path in reported and reported[path][0] != arch:
            return {}
        merged = dict(reported.get(path, (arch, {}))[1])
        for line in removed:
            if line in missing:
                merged.setdefault(line, missing[line])
        reported[path] = (arch, merged)
    return reported


def _debian_symbols_arch(
    files: list[tuple[str, str]], log: str, case: CaseData
) -> DeterministicMatch | None:
    """Exclude the symbols that dpkg-gensymbols found missing on the target architecture.

    Log signal: ``dpkg-gensymbols: error: some symbols or patterns disappeared``
    followed by its diff. The edit follows what Debian maintainers do for
    architecture-specific ABI: each reported symbol gets an ``arch=!<target>`` tag, or
    loses the target from a positive ``arch=`` list, so every other architecture keeps
    checking it. A symbol declared only for the target (``arch=<target>``) has no
    architecture left, so its line is replaced with dpkg's own ``#MISSING:`` line.

    Every reported symbols file must exist in the repository, the diff's architecture
    must match the Debian name of the Case's target (when the target is known), and
    each reported line must occur exactly once in its file, ignoring trailing
    whitespace. All changes to one file are composed into a single edit spanning the
    first to the last changed line, widened upwards until the expected text is unique,
    because the executor allows one operation per file.

    Args:
        files: The repository text files from ``_text_files``.
        log: The normalized failure log.
        case: The Case, used for its target architecture.

    Returns:
        The match with one edit per symbols file, or None if any file fails a check.
        The rule also refuses when more than 200 symbols, or more than 25% of a
        file's symbol lines, disappeared, since that points to a broken library
        rather than an architecture difference.
    """
    if _SYMBOLS_ERROR.search(log) is None:
        return None
    reported = _disappeared_symbols(log)
    if not reported or not any(lines for _, lines in reported.values()):
        return None
    expected_arch = _DEBIAN_ARCH.get(case.target_architecture.canonical)
    file_map = dict(files)
    rationale = (
        "mark symbols that dpkg-gensymbols reports as absent on the target architecture "
        "with an architecture exclusion tag, keeping them checked on every other architecture"
    )
    planned: list[PlannedEdit] = []
    for path in sorted(reported):
        arch, disappeared = reported[path]
        if not disappeared:
            continue
        if expected_arch is not None and arch != expected_arch:
            return None
        text = file_map.get(path)
        if text is None:
            return None
        file_lines = text.splitlines(keepends=True)
        stripped = [line.rstrip(" \t\r\n") for line in file_lines]
        symbol_count = sum(1 for line in stripped if _SYMBOLS_LINE.match(line))
        if (
            len(disappeared) > _MAX_DISAPPEARED_PER_FILE
            or len(disappeared) > symbol_count * _MAX_DISAPPEARED_FRACTION
        ):
            # A large share of vanished ABI is a broken library, not an architecture
            # difference; tagging it would only hide the failure.
            return None
        replacements: dict[int, str] = {}
        for symbol, missing_marker in disappeared.items():
            positions = [i for i, line in enumerate(stripped) if line == symbol]
            if len(positions) != 1:
                return None
            tagged = _exclude_symbol_arch(symbol, arch)
            if tagged is None:
                if not _only_target_arch(symbol, arch):
                    return None
                # The symbol was declared for the target architecture alone and is gone
                # there, so no architecture remains; record dpkg-gensymbols' own marker.
                tagged = missing_marker
            position = positions[0]
            ending = file_lines[position][len(file_lines[position].rstrip("\r\n")):]
            replacements[position] = tagged + ending
        # The executor accepts one operation per file, so compose every change in this
        # file into a single replacement spanning the first to the last tagged line.
        first, last = min(replacements), max(replacements)
        expected = "".join(file_lines[first : last + 1])
        replacement = "".join(
            replacements.get(i, file_lines[i]) for i in range(first, last + 1)
        )
        while text.count(expected) != 1 and first > 0:
            first -= 1
            expected = file_lines[first] + expected
            replacement = file_lines[first] + replacement
        if text.count(expected) != 1:
            return None
        planned.append(_edit(path, expected, replacement, rationale))
    if not planned:
        return None
    return DeterministicMatch(
        RepairFamily.PACKAGING_RULE,
        rationale,
        tuple(planned),
        confidence=0.95,
        risk=0.05,
    )


def _unconditional_build_abort(
    files: list[tuple[str, str]], log: str
) -> DeterministicMatch | None:
    """Remove an unconditional ``echo ... >&2; exit N`` abort whose message is in the log.

    Log signal: the quoted message (8 to 300 characters) of an ``echo "message" >&2``
    line that is directly followed by ``exit N`` (N of 1 or more) at the same
    indentation. Only ``.spec``, ``.sh`` and ``.mk`` files and files named
    ``Makefile`` are searched, and the message is compared case-insensitively.

    The two lines are replaced with a single comment at the same indentation. This is
    the repair for the Hosted Smoke ``hello`` Case.

    Args:
        files: The repository text files from ``_text_files``.
        log: The normalized failure log.

    Returns:
        The match, or None unless exactly one such abort in the repository has its
        message in the log.
    """
    pattern = re.compile(
        r"(?m)^(?P<indent>[ \t]*)echo[ \t]+"
        r"(?P<message>\"[^\"\r\n]{8,300}\"|'[^'\r\n]{8,300}')[ \t]+>&2[ \t]*\r?\n"
        r"(?P=indent)exit[ \t]+[1-9][0-9]*[ \t]*\r?$"
    )
    matches: list[tuple[str, re.Match[str]]] = []
    for path, text in files:
        name = Path(path).name.lower()
        if not (path.lower().endswith((".spec", ".sh", ".mk")) or name == "makefile"):
            continue
        for match in pattern.finditer(text):
            message = match.group("message")[1:-1]
            if message.casefold() in log.casefold():
                matches.append((path, match))
    if len(matches) != 1:
        return None
    path, match = matches[0]
    indent = match.group("indent")
    replacement = (
        f"{indent}# Removed an unconditional build abort whose emitted diagnostic "
        "matched the observed failure log."
    )
    rationale = "remove the uniquely observed unconditional build abort that emitted the causal diagnostic"
    return DeterministicMatch(
        RepairFamily.PACKAGING_RULE,
        rationale,
        (_edit(path, match.group(0), replacement, rationale),),
        confidence=0.96,
        risk=0.04,
    )


def _cmake_feature_check(
    files: list[tuple[str, str]], log: str
) -> DeterministicMatch | None:
    """Enable an x86 SIMD flag in CMake only when the compiler accepts it.

    Log signal: an ``-mavx...`` or ``-msse...`` flag anywhere in the log plus the
    phrase "does not support". The first flag found is used. Exactly one line across
    all ``CMakeLists.txt`` and ``*.cmake`` files must contain that flag. The line is
    wrapped in ``include(CheckCCompilerFlag)``, a ``check_c_compiler_flag`` call that
    sets ``ARCHIMEDES_COMPILER_SUPPORTS_<FLAG>``, and an ``if()``/``endif()`` block.

    Args:
        files: The repository text files from ``_text_files``.
        log: The normalized failure log.

    Returns:
        The match, or None when the signal is missing or the flag appears on zero or
        several CMake lines.
    """
    flag_match = re.search(r"-(?:mavx\d*|msse\d*(?:\.\d)?)", log, re.IGNORECASE)
    if flag_match is None or "does not support" not in log.lower():
        return None
    flag = flag_match.group(0)
    matches: list[tuple[str, str]] = []
    for path, text in files:
        if not path.lower().endswith(("cmakelists.txt", ".cmake")):
            continue
        matches.extend((path, line) for line in text.splitlines() if flag in line)
    if len(matches) != 1:
        return None
    path, expected = matches[0]
    variable = (
        "ARCHIMEDES_COMPILER_SUPPORTS_" + re.sub(r"[^A-Za-z0-9]", "_", flag).upper()
    )
    replacement = (
        "include(CheckCCompilerFlag)\n"
        f'check_c_compiler_flag("{flag}" {variable})\n'
        f"if({variable})\n  {expected.strip()}\nendif()"
    )
    rationale = f"enable {flag} only after the target compiler accepts it"
    return DeterministicMatch(
        RepairFamily.TOOLCHAIN_FEATURE_DETECTION,
        rationale,
        (_edit(path, expected, replacement, rationale),),
        confidence=0.94,
        risk=0.05,
    )


def _autoconf_rejected_x86_flags(
    files: list[tuple[str, str]], log: str, case: CaseData
) -> DeterministicMatch | None:
    """Remove x86-only flags that the target compiler rejected from ``configure.ac``.

    Log signal: one or more ``unrecognized command-line option '-msse...'`` messages
    (also ``-mssse3``, ``-mavx...`` and ``-mfpmath=sse``, with any quote style). Only
    applies when the source is x86_64 and the target is aarch64 or riscv64. Exactly
    one line in a file named ``configure.ac`` must contain every rejected flag as a
    whitespace-separated word. Those flags are removed from that line, its
    indentation is kept and leftover runs of spaces are collapsed; the other flags on
    the line stay.

    Args:
        files: The repository text files from ``_text_files``.
        log: The normalized failure log.
        case: The Case, used for its source and target architectures.

    Returns:
        The match, or None when the architectures do not fit, no flag was rejected,
        no single ``configure.ac`` line holds all of them, or removing them changes
        nothing.
    """
    if (
        case.source_architecture.canonical is not Architecture.X86_64
        or case.target_architecture.canonical
        not in {Architecture.AARCH64, Architecture.RISCV64}
    ):
        return None

    rejected = {
        match.group("flag")
        for match in re.finditer(
            r"unrecognized command-line option\s+[\"'‘’]"
            r"(?P<flag>-(?:m(?:sse\d*(?:\.\d)?|ssse3|avx\d*|fpmath=sse)))"
            r"[\"'‘’]",
            log,
            re.IGNORECASE,
        )
    }
    if not rejected:
        return None

    matches: list[tuple[str, str]] = []
    for path, text in files:
        if Path(path).name != "configure.ac":
            continue
        for line in text.splitlines():
            if all(
                re.search(rf"(?<!\S){re.escape(flag)}(?!\S)", line) for flag in rejected
            ):
                matches.append((path, line))
    if len(matches) != 1:
        return None

    path, expected = matches[0]
    indentation = expected[: len(expected) - len(expected.lstrip(" \t"))]
    replacement = expected
    for flag in sorted(rejected, key=len, reverse=True):
        replacement = re.sub(rf"(?<!\S){re.escape(flag)}(?!\S)", "", replacement)
    replacement = indentation + replacement.lstrip(" \t")
    replacement = re.sub(r"(?<=\S)[ \t]{2,}", " ", replacement)
    if replacement == expected:
        return None
    flags = ", ".join(sorted(rejected))
    rationale = (
        "remove the uniquely located x86-only optimization flags that the target compiler "
        f"explicitly rejected ({flags}), preserving the remaining portable flags"
    )
    return DeterministicMatch(
        RepairFamily.PORTABLE_FALLBACK,
        rationale,
        (_edit(path, expected, replacement, rationale),),
        confidence=0.96,
        risk=0.04,
    )


def _cmake_portable_source(
    files: list[tuple[str, str]], log: str
) -> DeterministicMatch | None:
    """Build an existing generic source file instead of the x86 one on other targets.

    Log signal: ``x86intrin.h`` or ``immintrin.h`` followed, anywhere later in the
    log, by "no such file" or "not found". Exactly one ``add_library(<lib> <src>)``
    line in a ``CMakeLists.txt`` must name a source containing ``optimized`` whose
    sibling, with ``optimized`` replaced by ``generic``, exists next to that
    ``CMakeLists.txt``. The line becomes an ``if(CMAKE_SYSTEM_PROCESSOR MATCHES ...)``
    block that keeps the optimized source on x86_64 and uses the generic one
    elsewhere.

    Args:
        files: The repository text files from ``_text_files``.
        log: The normalized failure log.

    Returns:
        The match, or None when the signal is missing or there is not exactly one
        such ``add_library`` line.
    """
    if not re.search(
        r"(?:x86intrin|immintrin)\.h.*(?:no such file|not found)", log, re.IGNORECASE | re.DOTALL
    ):
        return None
    file_map = dict(files)
    matches: list[tuple[str, str, str, str]] = []
    for path, text in files:
        if not path.lower().endswith("cmakelists.txt"):
            continue
        for match in re.finditer(
            r"^add_library\((\S+)\s+([^\s()]*optimized[^\s()]*)\)\s*$", text, re.MULTILINE
        ):
            library, optimized = match.groups()
            parent = Path(path).parent.as_posix()
            generic = optimized.replace("optimized", "generic")
            generic_path = generic if parent == "." else f"{parent}/{generic}"
            if generic_path not in file_map:
                continue
            matches.append((path, match.group(0), library, optimized))
    if len(matches) != 1:
        return None
    path, expected, library, optimized = matches[0]
    generic = optimized.replace("optimized", "generic")
    replacement = (
        'if(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64|amd64)$")\n'
        f"  add_library({library} {optimized})\n"
        "else()\n"
        f"  add_library({library} {generic})\n"
        "endif()"
    )
    rationale = (
        "select the observed generic source when x86 intrinsic headers are unavailable"
    )
    return DeterministicMatch(
        RepairFamily.PORTABLE_FALLBACK,
        rationale,
        (_edit(path, expected, replacement, rationale),),
        confidence=0.95,
        risk=0.05,
    )


def _atomic_linkage(
    files: list[tuple[str, str]], log: str
) -> DeterministicMatch | None:
    """Link ``-latomic`` when the linker cannot find ``__atomic`` builtins.

    Log signal: ``undefined reference to `__atomic`` (with a backtick or a straight
    quote). Some repository file must mention ``__atomic``. Among files named
    ``Makefile`` that do not already use ``-latomic``, there must be exactly one
    recipe line (starting with a tab) that calls ``$(CC)``; ``-latomic`` is appended
    to it. On some non-x86 targets GCC turns certain atomic operations (for example
    16-byte ones on aarch64) into libatomic calls that x86_64 builds never needed.

    Args:
        files: The repository text files from ``_text_files``.
        log: The normalized failure log.

    Returns:
        The match, or None when the signal or the ``__atomic`` usage is missing, or
        there is not exactly one candidate recipe line.
    """
    if (
        "undefined reference to `__atomic" not in log
        and "undefined reference to '__atomic" not in log
    ):
        return None
    if not any("__atomic" in text for _, text in files):
        return None
    matches: list[tuple[str, str]] = []
    for path, text in files:
        if Path(path).name.lower() != "makefile" or "-latomic" in text:
            continue
        recipes = [
            line
            for line in text.splitlines()
            if line.startswith("\t") and "$(CC)" in line
        ]
        matches.extend((path, recipe) for recipe in recipes)
    if len(matches) != 1:
        return None
    path, expected = matches[0]
    replacement = expected + " -latomic"
    rationale = (
        "link the compiler atomic runtime required by the unresolved builtin symbol"
    )
    return DeterministicMatch(
        RepairFamily.ATOMIC_LINKAGE,
        rationale,
        (_edit(path, expected, replacement, rationale),),
        confidence=0.91,
        risk=0.07,
    )


def _rpm_condition(files: list[tuple[str, str]], log: str) -> DeterministicMatch | None:
    """Drop an ``%ifarch x86_64`` condition that keeps a spec section off the target.

    Two signals are handled. If the log says ``Installed (but unpackaged)``, a
    ``.spec`` file with exactly one ``%ifarch x86_64`` block around a ``%files``
    section is unwrapped so the file list applies on every architecture. Otherwise,
    if the log has ``fatal error: <name>.h: No such file``, a ``.spec`` file with
    exactly one ``%ifarch x86_64`` block around a single ``BuildRequires:`` line is
    unwrapped so the dependency is installed on the target too. The rule does not
    check that the package actually provides the missing header.

    Args:
        files: The repository text files from ``_text_files``.
        log: The normalized failure log.

    Returns:
        The match, or None unless exactly one spec file matches the chosen signal.
    """
    installed_matches: list[tuple[str, str, str]] = []
    dependency_matches: list[tuple[str, str, str, str]] = []
    for path, text in files:
        if not path.endswith(".spec"):
            continue
        if "installed (but unpackaged)" in log.lower():
            match = re.search(
                r"%ifarch\s+x86_64\s*\n(%files\s*\n[^%]+?)%endif", text, re.IGNORECASE
            )
            if match and text.count(match.group(0)) == 1:
                installed_matches.append(
                    (path, match.group(0), match.group(1).rstrip())
                )
        missing = re.search(r"fatal error:\s*([^:\s]+\.h):\s*no such file", log, re.IGNORECASE)
        block = re.search(
            r"%ifarch\s+x86_64\s*\n(BuildRequires:\s*[^\n]+)\s*\n%endif", text, re.IGNORECASE
        )
        if missing and block and text.count(block.group(0)) == 1:
            dependency_matches.append(
                (path, block.group(0), block.group(1), missing.group(1))
            )
    if len(installed_matches) == 1:
        path, expected, replacement = installed_matches[0]
        rationale = "make the observed installed artifact file list apply on the target architecture"
        return DeterministicMatch(
            RepairFamily.PACKAGING_RULE,
            rationale,
            (_edit(path, expected, replacement, rationale),),
            confidence=0.94,
            risk=0.05,
        )
    if len(installed_matches) > 1 or len(dependency_matches) != 1:
        return None
    path, expected, replacement, header = dependency_matches[0]
    rationale = f"apply the dependency declaration that provides {header} on the target"
    return DeterministicMatch(
        RepairFamily.ARCHITECTURE_DEPENDENCY_CONDITION,
        rationale,
        (_edit(path, expected, replacement, rationale),),
        confidence=0.90,
        risk=0.09,
    )


def _artifact_path(files: list[tuple[str, str]], log: str) -> DeterministicMatch | None:
    """Change a Makefile install path from ``/usr/lib64/`` to ``/usr/lib/``.

    Log signal: ``expected artifact missing at <path>`` where the path sits directly
    in ``/usr/lib/``. Exactly one line across all files named ``Makefile`` must
    contain ``/usr/lib64/``; that prefix is replaced on that line. The rule does not
    check that the line installs the same file name.

    Args:
        files: The repository text files from ``_text_files``.
        log: The normalized failure log.

    Returns:
        The match, or None when the signal is missing, the path is not directly in
        ``/usr/lib/``, or there is not exactly one ``/usr/lib64/`` Makefile line.
    """
    expected_path = re.search(r"expected artifact missing at\s+(\S+)", log, re.IGNORECASE)
    if expected_path is None:
        return None
    required = expected_path.group(1)
    matches: list[tuple[str, str]] = []
    for path, text in files:
        if Path(path).name.lower() != "makefile":
            continue
        candidates = [line for line in text.splitlines() if "/usr/lib64/" in line]
        if required == "/usr/lib/" + required.rsplit("/", 1)[-1]:
            matches.extend((path, line) for line in candidates)
    if len(matches) != 1:
        return None
    path, expected = matches[0]
    replacement = expected.replace("/usr/lib64/", "/usr/lib/")
    rationale = (
        "install the artifact at the validator-observed architecture-neutral path"
    )
    return DeterministicMatch(
        RepairFamily.ARTIFACT_PATH,
        rationale,
        (_edit(path, expected, replacement, rationale),),
        confidence=0.96,
        risk=0.03,
    )


def _generated_config(
    files: list[tuple[str, str]], log: str
) -> DeterministicMatch | None:
    """Guard a generated ``GENERATED_FOR_X86_64`` define with ``__x86_64__``.

    Log signal: "generated architecture mismatch". Exactly one
    ``#define GENERATED_FOR_X86_64 1`` line must exist in the repository. It is
    wrapped in ``#if defined(__x86_64__)`` and ``#endif``.

    Args:
        files: The repository text files from ``_text_files``.
        log: The normalized failure log.

    Returns:
        The match, or None when the signal is missing or the define is not unique.
    """
    if "generated architecture mismatch" not in log.lower():
        return None
    matches: list[tuple[str, str]] = []
    for path, text in files:
        matches.extend(
            (path, match)
            for match in re.findall(
                r"^#define\s+GENERATED_FOR_X86_64\s+1\s*$", text, re.MULTILINE
            )
        )
    if len(matches) != 1:
        return None
    path, expected = matches[0]
    replacement = "#if defined(__x86_64__)\n#define GENERATED_FOR_X86_64 1\n#endif"
    rationale = "expose the generated x86 marker only when compiling for x86_64"
    return DeterministicMatch(
        RepairFamily.CONFIG_GENERATION,
        rationale,
        (_edit(path, expected, replacement, rationale),),
        confidence=0.92,
        risk=0.06,
    )


def _architecture_guard(
    files: list[tuple[str, str]], log: str
) -> DeterministicMatch | None:
    """Replace an ``#error`` for non-x86 targets with a pointer-width calculation.

    Log signal: "unsupported architecture selection". Exactly one block of the form
    ``#if defined(__x86_64__)`` / ``int NAME(void) { return 64; }`` / ``#else`` /
    ``#error unsupported architecture selection`` / ``#endif`` must exist. The
    ``#error`` branch becomes ``int NAME(void) { return (int)(sizeof(void *) * 8); }``.

    Args:
        files: The repository text files from ``_text_files``.
        log: The normalized failure log.

    Returns:
        The match, or None when the signal is missing or the block is not unique.
    """
    if "unsupported architecture selection" not in log.lower():
        return None
    pattern = re.compile(
        r"#if\s+defined\(__x86_64__\)\s*\n"
        r"int\s+(\w+)\(void\)\s*\{\s*return\s+64;\s*\}\s*\n"
        r"#else\s*\n#error\s+unsupported architecture selection\s*\n#endif"
    )
    matches: list[tuple[str, re.Match[str]]] = []
    for path, text in files:
        matches.extend((path, match) for match in pattern.finditer(text))
    if len(matches) != 1:
        return None
    path, match = matches[0]
    function = match.group(1)
    replacement = (
        "#if defined(__x86_64__)\n"
        f"int {function}(void) {{ return 64; }}\n"
        "#else\n"
        f"int {function}(void) {{ return (int)(sizeof(void *) * 8); }}\n"
        "#endif"
    )
    rationale = "replace a source-only architecture rejection with the observed pointer-width semantics"
    return DeterministicMatch(
        RepairFamily.ARCHITECTURE_GUARD,
        rationale,
        (_edit(path, match.group(0), replacement, rationale),),
        confidence=0.89,
        risk=0.09,
    )


def _source_fallback(
    files: list[tuple[str, str]], log: str
) -> DeterministicMatch | None:
    """Fall back to an existing ``<name>_generic.c`` when an x86 source fails to compile.

    Log signal: ``immintrin.h`` or ``x86intrin.h``, ``intrinsic _mm_...``, or
    ``unrecognized opcode `pause``. The first log line that starts with
    ``<path>.c:`` names the failing file; that path must be a repository file with a
    ``<stem>_generic.c`` sibling, and both must contain an ``int``, ``unsigned`` or
    ``void`` function. The whole failing file is replaced with an
    ``#if defined(__x86_64__) || defined(__i386__)`` block that keeps the original
    code for x86 and uses the generic code elsewhere, with the generic function's
    first occurrence renamed to the original function's name.

    Args:
        files: The repository text files from ``_text_files``.
        log: The normalized failure log.

    Returns:
        The match, or None when the signal, the failing file, the generic sibling or
        a function definition in either file is missing.
    """
    if not re.search(
        r"(?:immintrin|x86intrin)\.h|intrinsic\s+_mm_|unrecognized opcode\s+[`']pause",
        log,
        re.IGNORECASE,
    ):
        return None
    file_map = dict(files)
    diagnostic_path = re.search(r"^([^:\n]+\.c):(?:\d+|\s)", log, re.MULTILINE)
    if diagnostic_path is None:
        return None
    path = diagnostic_path.group(1).replace("\\", "/")
    primary = file_map.get(path)
    if primary is None:
        return None
    source_path = Path(path)
    fallback_path = str(source_path.with_name(source_path.stem + "_generic.c")).replace(
        "\\", "/"
    )
    fallback = file_map.get(fallback_path)
    if fallback is None:
        return None
    primary_function = re.search(r"\b(?:int|unsigned|void)\s+(\w+)\s*\(", primary)
    fallback_function = re.search(r"\b(?:int|unsigned|void)\s+(\w+)\s*\(", fallback)
    if primary_function is None or fallback_function is None:
        return None
    portable = fallback.strip()
    if fallback_function.group(1) != primary_function.group(1):
        portable = re.sub(
            rf"\b{re.escape(fallback_function.group(1))}\b",
            primary_function.group(1),
            portable,
            count=1,
        )
    replacement = (
        "#if defined(__x86_64__) || defined(__i386__)\n"
        f"{primary.rstrip()}\n"
        "#else\n"
        f"{portable}\n"
        "#endif\n"
    )
    rationale = "preserve the x86 implementation and select the observed semantic fallback elsewhere"
    return DeterministicMatch(
        RepairFamily.PORTABLE_FALLBACK,
        rationale,
        (_edit(path, primary, replacement, rationale),),
        confidence=0.93,
        risk=0.07,
    )


def _portable_abi_or_bytes(
    files: list[tuple[str, str]], log: str
) -> DeterministicMatch | None:
    """Fix byte-order or pointer-width assumptions that fail on the target.

    For "network-order bytes differ", exactly one function of the form
    ``void NAME(unsigned VALUE, unsigned char OUT[4]) { *(unsigned *)OUT = VALUE; }``
    must exist; its body is replaced with four explicit big-endian byte stores, so
    the result no longer depends on host endianness. For "pointer width must be
    eight", exactly one ``_Static_assert(sizeof(void *) == 8, ...)`` with that
    message must exist; it is replaced with an assert that ``uintptr_t`` is as wide
    as a pointer, plus ``#include <stdint.h>``.

    Args:
        files: The repository text files from ``_text_files``.
        log: The normalized failure log.

    Returns:
        The match, or None when neither signal is present or the matching code is not
        unique. When the log has the byte-order signal, the pointer-width fix is not
        tried.
    """
    if "network-order bytes differ" in log.lower():
        pattern = re.compile(
            r"void\s+(\w+)\(unsigned\s+(\w+),\s*unsigned char\s+(\w+)\[4\]\)\s*"
            r"\{\s*\*\(unsigned\s*\*\)\3\s*=\s*\2;\s*\}"
        )
        network_matches = [
            (path, match) for path, text in files for match in pattern.finditer(text)
        ]
        if len(network_matches) != 1:
            return None
        path, match = network_matches[0]
        function, value, output = match.groups()
        replacement = (
            f"void {function}(unsigned {value}, unsigned char {output}[4]) {{\n"
            f"    {output}[0] = (unsigned char)({value} >> 24);\n"
            f"    {output}[1] = (unsigned char)({value} >> 16);\n"
            f"    {output}[2] = (unsigned char)({value} >> 8);\n"
            f"    {output}[3] = (unsigned char){value};\n"
            "}"
        )
        rationale = (
            "serialize bytes explicitly in network order without host-endian aliasing"
        )
        return DeterministicMatch(
            RepairFamily.SOURCE_PORT,
            rationale,
            (_edit(path, match.group(0), replacement, rationale),),
            confidence=0.97,
            risk=0.04,
        )
    if "pointer width must be eight" in log.lower():
        pointer_matches: list[tuple[str, str]] = []
        for path, text in files:
            pointer_matches.extend(
                (path, match)
                for match in re.findall(
                    r'_Static_assert\(sizeof\(void \*\) == 8, "pointer width must be eight"\);',
                    text,
                )
            )
        if len(pointer_matches) != 1:
            return None
        path, expected = pointer_matches[0]
        replacement = (
            "#include <stdint.h>\n"
            '_Static_assert(sizeof(uintptr_t) == sizeof(void *), "uintptr_t must represent pointers");'
        )
        rationale = (
            "validate the portable pointer-width type instead of requiring a 64-bit ABI"
        )
        return DeterministicMatch(
            RepairFamily.ABI_TYPE_FIX,
            rationale,
            (_edit(path, expected, replacement, rationale),),
            confidence=0.94,
            risk=0.05,
        )
    return None


def _synchronized_dispatch(
    files: list[tuple[str, str]], log: str
) -> DeterministicMatch | None:
    """Stop defining an optimized CRC backend that is not linked, and add a fallback.

    Log signal: ``undefined reference to `optimized_``. Exactly one line across all
    ``CMakeLists.txt`` files must contain ``HAVE_OPTIMIZED_``, and exactly one
    ``dispatch.c`` must hold the ``#ifdef HAVE_OPTIMIZED_CRC`` block that calls
    ``optimized_crc``, with a sibling ``generic.c`` that defines ``crc_generic``. The
    CMake line is replaced with a comment, and the dispatch block gains an ``#else``
    branch that calls ``crc_generic``. Both edits go into one match so the feature
    definition and the dispatch code stay consistent.

    Args:
        files: The repository text files from ``_text_files``.
        log: The normalized failure log.

    Returns:
        The match, or None when the signal is missing or either piece is not unique.
    """
    if "undefined reference to `optimized_" not in log:
        return None
    file_map = dict(files)
    cmake_matches = [
        (path, line)
        for path, text in files
        if path.lower().endswith("cmakelists.txt")
        for line in text.splitlines()
        if "HAVE_OPTIMIZED_" in line
    ]
    block_pattern = re.compile(
        r"#ifdef\s+HAVE_OPTIMIZED_CRC\s*\nextern int optimized_crc\(int\);\s*\n"
        r"int crc\(int x\) \{ return optimized_crc\(x\); \}\s*\n#endif",
    )
    dispatch_matches = [
        (path, match)
        for path, text in files
        if Path(path).name == "dispatch.c"
        for match in block_pattern.finditer(text)
        if "crc_generic"
        in file_map.get(str(Path(path).with_name("generic.c")).replace("\\", "/"), "")
    ]
    if len(cmake_matches) != 1 or len(dispatch_matches) != 1:
        return None
    cmake_path, definition = cmake_matches[0]
    dispatch_path, block = dispatch_matches[0]
    fallback = (
        "#ifdef HAVE_OPTIMIZED_CRC\n"
        "extern int optimized_crc(int);\n"
        "int crc(int x) { return optimized_crc(x); }\n"
        "#else\n"
        "extern int crc_generic(int);\n"
        "int crc(int x) { return crc_generic(x); }\n"
        "#endif"
    )
    rationale = (
        "synchronize the feature declaration with an observed generic dispatch path"
    )
    return DeterministicMatch(
        RepairFamily.MULTI_FILE_SYNCHRONIZATION,
        rationale,
        (
            _edit(
                cmake_path,
                definition,
                "# Optimized CRC is enabled only by a proven backend check.",
                rationale,
            ),
            _edit(dispatch_path, block.group(0), fallback, rationale),
        ),
        confidence=0.90,
        risk=0.10,
    )


def match_deterministic_repair(
    case: CaseData, failure: FailureAnalysis, repo: Path
) -> DeterministicMatch | None:
    """Try each repair rule in a fixed order and return the first that matches.

    The order is: Debian multiarch path, Debian symbols, autoconf rejected x86 flags,
    then the rules that need only the files and the log, from the unconditional build
    abort to the synchronized dispatch. Rules decide from the log and the repository
    contents alone, never from Case IDs or package names.

    Args:
        case: The loaded Case, used for its source and target architectures.
        failure: The forensics result; only its normalized log is used.
        repo: The root of the package worktree.

    Returns:
        The first rule match, or None if no rule found unique evidence.
    """
    files = _text_files(repo)
    log = failure.normalized_log
    multiarch = _debian_multiarch_path(files, log, case)
    if multiarch is not None:
        return multiarch
    symbols = _debian_symbols_arch(files, log, case)
    if symbols is not None:
        return symbols
    autoconf_flags = _autoconf_rejected_x86_flags(files, log, case)
    if autoconf_flags is not None:
        return autoconf_flags
    matchers = (
        _unconditional_build_abort,
        _cmake_feature_check,
        _cmake_portable_source,
        _atomic_linkage,
        _rpm_condition,
        _artifact_path,
        _generated_config,
        _architecture_guard,
        _source_fallback,
        _portable_abi_or_bytes,
        _synchronized_dispatch,
    )
    for matcher in matchers:
        match = matcher(files, log)
        if match is not None:
            return match
    return None
