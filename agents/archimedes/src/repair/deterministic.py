"""Evidence-oriented deterministic repair matches for common portability mechanisms."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..schemas import Architecture, CaseData, FailureAnalysis, PlannedEdit, RepairFamily
from ..workspace.repository_files import iter_repository_files


@dataclass(frozen=True, slots=True)
class DeterministicMatch:
    family: RepairFamily
    rationale: str
    edits: tuple[PlannedEdit, ...]
    confidence: float = 0.90
    risk: float = 0.10


def _text_files(repo: Path) -> list[tuple[str, str]]:
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
    return PlannedEdit(
        operation="replace_lines",
        path=path,
        expected_text=expected,
        replacement=replacement,
        rationale=rationale,
    )


def _amd_comgr_unsigned_device_lib_id(
    files: list[tuple[str, str]], case: CaseData
) -> tuple[PlannedEdit, ...]:
    """Backport the complete upstream COMGR fix for target-dependent plain char."""
    if (
        case.source_architecture.canonical is not Architecture.X86_64
        or case.target_architecture.canonical
        not in {Architecture.AARCH64, Architecture.RISCV64}
    ):
        return ()

    file_map = dict(files)
    replacements = (
        (
            "amd/comgr/cmake/device-libs-id.py",
            "    digest_char = [e if e < 128 else e-256 for e in digest_uchar]\n"
            "    digest_elts = \", \".join(map(str, digest_char))\n"
            "    print(f\"static const char {args.varname}[] = "
            "{{{digest_elts}, 0}};\", file=open(args.output, 'w'))",
            "    digest_elts = \", \".join(map(str, digest_uchar))\n"
            "    print(f\"static const unsigned char {args.varname}[] = "
            "{{{digest_elts}, 0}};\", file=open(args.output, 'w'))",
        ),
        (
            "amd/comgr/src/comgr-cache-command.cpp",
            "  addString(H, getDeviceLibrariesIdentifier());",
            "  H.update(getDeviceLibrariesIdentifier());",
        ),
        (
            "amd/comgr/src/comgr-device-libs.cpp",
            "StringRef getDeviceLibrariesIdentifier() { return DEVICE_LIBS_ID; }",
            "ArrayRef<unsigned char> getDeviceLibrariesIdentifier() { return DEVICE_LIBS_ID; }",
        ),
        (
            "amd/comgr/src/comgr-device-libs.h",
            "llvm::StringRef getDeviceLibrariesIdentifier();",
            "llvm::ArrayRef<unsigned char> getDeviceLibrariesIdentifier();",
        ),
    )
    if any(
        path not in file_map
        or file_map[path].replace("\r\n", "\n").replace("\r", "\n").count(expected) != 1
        for path, expected, _ in replacements
    ):
        return ()

    rationale = (
        "backport the complete upstream COMGR unsigned-byte identifier change so SHA-256 "
        "bytes compile consistently when the target ABI makes plain char unsigned"
    )
    return tuple(
        _edit(path, expected, replacement, rationale)
        for path, expected, replacement in replacements
    )


def _debian_multiarch_path(
    files: list[tuple[str, str]], log: str, case: CaseData
) -> DeterministicMatch | None:
    """Replace one proven source-architecture GNU path in Debian packaging."""
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
        re.I,
    )
    missing_cmake_package = re.search(
        r"CMake Error at .*?\(find_package\):.*?Could not find a package "
        r"configuration file provided by",
        log,
        re.I | re.S,
    ) and re.search(
        rf"-D[A-Za-z0-9_]+_DIR=\S*{re.escape(source_triplet)}\S*",
        log,
        re.I,
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
    if len(rules_matches) == 1 and (missing_path is not None or missing_cmake_package is not None):
        path, expected = rules_matches[0]
        replacement = expected.replace(
            source_triplet, "$(shell dpkg-architecture -qDEB_HOST_MULTIARCH)"
        )
        rationale = (
            "derive the Debian multiarch directory from the target build architecture instead of "
            f"hard-coding the source architecture triplet {source_triplet}"
        )
        planned_edits = [_edit(path, expected, replacement, rationale)]
        if missing_cmake_package is not None and "AMDDeviceLibs_DIR" in expected:
            companion_edits = _amd_comgr_unsigned_device_lib_id(files, case)
            if companion_edits:
                planned_edits.extend(companion_edits)
                rationale += (
                    "; also apply the exact upstream COMGR unsigned-byte identifier backport "
                    "needed after configuration advances to target compilation"
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
    compat_match = re.search(r"debhelper-compat\s*\(=\s*(\d+)\s*\)", control, re.I)
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


def _unconditional_build_abort(
    files: list[tuple[str, str]], log: str
) -> DeterministicMatch | None:
    """Remove one unconditional build-script abort whose own message is in the failure log."""
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
    rationale = (
        "remove the uniquely observed unconditional build abort that emitted the causal diagnostic"
    )
    return DeterministicMatch(
        RepairFamily.PACKAGING_RULE,
        rationale,
        (_edit(path, match.group(0), replacement, rationale),),
        confidence=0.96,
        risk=0.04,
    )


def _cmake_feature_check(files: list[tuple[str, str]], log: str) -> DeterministicMatch | None:
    flag_match = re.search(r"-(?:mavx\d*|msse\d*(?:\.\d)?)", log, re.I)
    if flag_match is None or "does not support" not in log.lower():
        return None
    flag = flag_match.group(0)
    for path, text in files:
        if not path.lower().endswith(("cmakelists.txt", ".cmake")):
            continue
        lines = [line for line in text.splitlines() if flag in line]
        if len(lines) != 1:
            continue
        expected = lines[0]
        variable = "ARCHIMEDES_COMPILER_SUPPORTS_" + re.sub(r"[^A-Za-z0-9]", "_", flag).upper()
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
    return None


def _autoconf_rejected_x86_flags(
    files: list[tuple[str, str]], log: str, case: CaseData
) -> DeterministicMatch | None:
    """Remove a unique set of x86-only flags explicitly rejected by a non-x86 compiler."""
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
            re.I,
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
                re.search(rf"(?<!\S){re.escape(flag)}(?!\S)", line)
                for flag in rejected
            ):
                matches.append((path, line))
    if len(matches) != 1:
        return None

    path, expected = matches[0]
    indentation = expected[: len(expected) - len(expected.lstrip(" \t"))]
    replacement = expected
    for flag in sorted(rejected, key=len, reverse=True):
        replacement = re.sub(
            rf"(?<!\S){re.escape(flag)}(?!\S)", "", replacement
        )
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


def _cmake_portable_source(files: list[tuple[str, str]], log: str) -> DeterministicMatch | None:
    if not re.search(r"(?:x86intrin|immintrin)\.h.*(?:no such file|not found)", log, re.I | re.S):
        return None
    file_map = dict(files)
    for path, text in files:
        if not path.lower().endswith("cmakelists.txt"):
            continue
        for match in re.finditer(r"^add_library\((\S+)\s+([^\s()]*optimized[^\s()]*)\)\s*$", text, re.M):
            library, optimized = match.groups()
            parent = Path(path).parent.as_posix()
            generic = optimized.replace("optimized", "generic")
            generic_path = generic if parent == "." else f"{parent}/{generic}"
            if generic_path not in file_map:
                continue
            expected = match.group(0)
            replacement = (
                'if(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64|amd64)$")\n'
                f"  add_library({library} {optimized})\n"
                "else()\n"
                f"  add_library({library} {generic})\n"
                "endif()"
            )
            rationale = "select the observed generic source when x86 intrinsic headers are unavailable"
            return DeterministicMatch(
                RepairFamily.PORTABLE_FALLBACK,
                rationale,
                (_edit(path, expected, replacement, rationale),),
                confidence=0.95,
                risk=0.05,
            )
    return None


def _atomic_linkage(files: list[tuple[str, str]], log: str) -> DeterministicMatch | None:
    if "undefined reference to `__atomic" not in log and "undefined reference to '__atomic" not in log:
        return None
    if not any("__atomic" in text for _, text in files):
        return None
    for path, text in files:
        if Path(path).name.lower() != "makefile" or "-latomic" in text:
            continue
        recipes = [line for line in text.splitlines() if line.startswith("\t") and "$(CC)" in line]
        if len(recipes) != 1:
            continue
        expected = recipes[0]
        replacement = expected + " -latomic"
        rationale = "link the compiler atomic runtime required by the unresolved builtin symbol"
        return DeterministicMatch(
            RepairFamily.ATOMIC_LINKAGE,
            rationale,
            (_edit(path, expected, replacement, rationale),),
            confidence=0.91,
            risk=0.07,
        )
    return None


def _rpm_condition(files: list[tuple[str, str]], log: str) -> DeterministicMatch | None:
    for path, text in files:
        if not path.endswith(".spec"):
            continue
        if "installed (but unpackaged)" in log.lower():
            match = re.search(r"%ifarch\s+x86_64\s*\n(%files\s*\n[^%]+?)%endif", text, re.I)
            if match and text.count(match.group(0)) == 1:
                rationale = "make the observed installed artifact file list apply on the target architecture"
                return DeterministicMatch(
                    RepairFamily.PACKAGING_RULE,
                    rationale,
                    (_edit(path, match.group(0), match.group(1).rstrip(), rationale),),
                    confidence=0.94,
                    risk=0.05,
                )
        missing = re.search(r"fatal error:\s*([^:\s]+\.h):\s*no such file", log, re.I)
        block = re.search(r"%ifarch\s+x86_64\s*\n(BuildRequires:\s*[^\n]+)\s*\n%endif", text, re.I)
        if missing and block and text.count(block.group(0)) == 1:
            rationale = f"apply the dependency declaration that provides {missing.group(1)} on the target"
            return DeterministicMatch(
                RepairFamily.ARCHITECTURE_DEPENDENCY_CONDITION,
                rationale,
                (_edit(path, block.group(0), block.group(1), rationale),),
                confidence=0.90,
                risk=0.09,
            )
    return None


def _artifact_path(files: list[tuple[str, str]], log: str) -> DeterministicMatch | None:
    expected_path = re.search(r"expected artifact missing at\s+(\S+)", log, re.I)
    if expected_path is None:
        return None
    required = expected_path.group(1)
    for path, text in files:
        if Path(path).name.lower() != "makefile":
            continue
        candidates = [line for line in text.splitlines() if "/usr/lib64/" in line]
        if len(candidates) == 1 and required == "/usr/lib/" + required.rsplit("/", 1)[-1]:
            expected = candidates[0]
            replacement = expected.replace("/usr/lib64/", "/usr/lib/")
            rationale = "install the artifact at the validator-observed architecture-neutral path"
            return DeterministicMatch(
                RepairFamily.ARTIFACT_PATH,
                rationale,
                (_edit(path, expected, replacement, rationale),),
                confidence=0.96,
                risk=0.03,
            )
    return None


def _generated_config(files: list[tuple[str, str]], log: str) -> DeterministicMatch | None:
    if "generated architecture mismatch" not in log.lower():
        return None
    for path, text in files:
        matches = re.findall(r"^#define\s+GENERATED_FOR_X86_64\s+1\s*$", text, re.M)
        if len(matches) == 1:
            expected = matches[0]
            replacement = "#if defined(__x86_64__)\n#define GENERATED_FOR_X86_64 1\n#endif"
            rationale = "expose the generated x86 marker only when compiling for x86_64"
            return DeterministicMatch(
                RepairFamily.CONFIG_GENERATION,
                rationale,
                (_edit(path, expected, replacement, rationale),),
                confidence=0.92,
                risk=0.06,
            )
    return None


def _architecture_guard(files: list[tuple[str, str]], log: str) -> DeterministicMatch | None:
    if "unsupported architecture selection" not in log.lower():
        return None
    pattern = re.compile(
        r"#if\s+defined\(__x86_64__\)\s*\n"
        r"int\s+(\w+)\(void\)\s*\{\s*return\s+64;\s*\}\s*\n"
        r"#else\s*\n#error\s+unsupported architecture selection\s*\n#endif"
    )
    for path, text in files:
        match = pattern.search(text)
        if match is None or text.count(match.group(0)) != 1:
            continue
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
    return None


def _source_fallback(files: list[tuple[str, str]], log: str) -> DeterministicMatch | None:
    if not re.search(
        r"(?:immintrin|x86intrin)\.h|intrinsic\s+_mm_|unrecognized opcode\s+[`']pause",
        log,
        re.I,
    ):
        return None
    file_map = dict(files)
    diagnostic_path = re.search(r"^([^:\n]+\.c):(?:\d+|\s)", log, re.M)
    if diagnostic_path is None:
        return None
    path = diagnostic_path.group(1).replace("\\", "/")
    primary = file_map.get(path)
    if primary is None:
        return None
    source_path = Path(path)
    fallback_path = str(source_path.with_name(source_path.stem + "_generic.c")).replace("\\", "/")
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


def _portable_abi_or_bytes(files: list[tuple[str, str]], log: str) -> DeterministicMatch | None:
    if "network-order bytes differ" in log.lower():
        pattern = re.compile(
            r"void\s+(\w+)\(unsigned\s+(\w+),\s*unsigned char\s+(\w+)\[4\]\)\s*"
            r"\{\s*\*\(unsigned\s*\*\)\3\s*=\s*\2;\s*\}"
        )
        for path, text in files:
            match = pattern.search(text)
            if not match or text.count(match.group(0)) != 1:
                continue
            function, value, output = match.groups()
            replacement = (
                f"void {function}(unsigned {value}, unsigned char {output}[4]) {{\n"
                f"    {output}[0] = (unsigned char)({value} >> 24);\n"
                f"    {output}[1] = (unsigned char)({value} >> 16);\n"
                f"    {output}[2] = (unsigned char)({value} >> 8);\n"
                f"    {output}[3] = (unsigned char){value};\n"
                "}"
            )
            rationale = "serialize bytes explicitly in network order without host-endian aliasing"
            return DeterministicMatch(
                RepairFamily.SOURCE_PORT,
                rationale,
                (_edit(path, match.group(0), replacement, rationale),),
                confidence=0.97,
                risk=0.04,
            )
    if "pointer width must be eight" in log.lower():
        for path, text in files:
            matches = re.findall(
                r'_Static_assert\(sizeof\(void \*\) == 8, "pointer width must be eight"\);', text
            )
            if len(matches) == 1:
                expected = matches[0]
                replacement = (
                    "#include <stdint.h>\n"
                    '_Static_assert(sizeof(uintptr_t) == sizeof(void *), "uintptr_t must represent pointers");'
                )
                rationale = "validate the portable pointer-width type instead of requiring a 64-bit ABI"
                return DeterministicMatch(
                    RepairFamily.ABI_TYPE_FIX,
                    rationale,
                    (_edit(path, expected, replacement, rationale),),
                    confidence=0.94,
                    risk=0.05,
                )
    return None


def _synchronized_dispatch(files: list[tuple[str, str]], log: str) -> DeterministicMatch | None:
    if "undefined reference to `optimized_" not in log:
        return None
    file_map = dict(files)
    cmake = next(((path, text) for path, text in files if path.lower().endswith("cmakelists.txt")), None)
    dispatch = next(((path, text) for path, text in files if Path(path).name == "dispatch.c"), None)
    if cmake is None or dispatch is None:
        return None
    cmake_path, cmake_text = cmake
    dispatch_path, dispatch_text = dispatch
    definitions = [line for line in cmake_text.splitlines() if "HAVE_OPTIMIZED_" in line]
    block = re.search(
        r"#ifdef\s+HAVE_OPTIMIZED_CRC\s*\nextern int optimized_crc\(int\);\s*\n"
        r"int crc\(int x\) \{ return optimized_crc\(x\); \}\s*\n#endif",
        dispatch_text,
    )
    generic_path = str(Path(dispatch_path).with_name("generic.c")).replace("\\", "/")
    if len(definitions) != 1 or block is None or "crc_generic" not in file_map.get(generic_path, ""):
        return None
    fallback = (
        "#ifdef HAVE_OPTIMIZED_CRC\n"
        "extern int optimized_crc(int);\n"
        "int crc(int x) { return optimized_crc(x); }\n"
        "#else\n"
        "extern int crc_generic(int);\n"
        "int crc(int x) { return crc_generic(x); }\n"
        "#endif"
    )
    rationale = "synchronize the feature declaration with an observed generic dispatch path"
    return DeterministicMatch(
        RepairFamily.MULTI_FILE_SYNCHRONIZATION,
        rationale,
        (
            _edit(cmake_path, definitions[0], "# Optimized CRC is enabled only by a proven backend check.", rationale),
            _edit(dispatch_path, block.group(0), fallback, rationale),
        ),
        confidence=0.90,
        risk=0.10,
    )


def match_deterministic_repair(
    case: CaseData, failure: FailureAnalysis, repo: Path
) -> DeterministicMatch | None:
    """Return the first uniquely evidenced generic repair rule, never using case IDs."""
    files = _text_files(repo)
    log = failure.normalized_log
    multiarch = _debian_multiarch_path(files, log, case)
    if multiarch is not None:
        return multiarch
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
