from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.build.unavailable import UnavailableBuildFeedbackProvider
from src.reasoning.controller import RepairController, _clip_for_prompt
from src.repair.deterministic import _exclude_symbol_arch
from src.schemas import RepairFamily
from src.workspace.paths import WorkspacePaths

from .support import make_workspace, runtime_config


def _metadata(source: str, target: str) -> str:
    return json.dumps(
        {
            "case_id": "generic-symbols",
            "source_architecture": source,
            "target_architecture": target,
        }
    )


def _gensymbols_log(path: str, package_arch: str, removed: list[str], version: str) -> str:
    hunk = [f"-{line}" for line in removed]
    hunk.extend(f"+#MISSING: {version}#{line}" for line in removed)
    return "\n".join(
        [
            "   dh_makeshlibs -a",
            (
                "dpkg-gensymbols: error: some symbols or patterns disappeared in the symbols "
                "file: see diff output below"
            ),
            f"dpkg-gensymbols: warning: debian/pkg/DEBIAN/symbols doesn't match completely {path}",
            f"--- {path} (libdemo1_{version}_{package_arch})",
            "+++ dpkg-gensymbolsAbCdEf\t2026-01-01 00:00:00.000000000 +0000",
            "@@ -1,9 +1,9 @@",
            " demo_init@Base 1.0",
            *hunk,
            "dh_makeshlibs: error: failing due to earlier errors",
            "make: *** [debian/rules:7: binary-arch] Error 25",
            "dpkg-buildpackage: error: debian/rules binary-arch subprocess returned exit status 2",
        ]
    ) + "\n"


SYMBOLS = (
    "libdemo.so.1 libdemo1 #MINVER#\n"
    "* Build-Depends-Package: libdemo-dev\n"
    " demo_block_hash@Base 1.0\n"
    " demo_block_hash_avx2@Base 1.0\n"
    " demo_close@Base 1.0\n"
    " demo_init@Base 1.0\n"
    " demo_open@Base 1.0\n"
    " demo_read@Base 1.0\n"
    " demo_sse2_kernel@Base 1.0\n"
    " demo_version@Base 1.0\n"
    " demo_write@Base 1.0\n"
)


class DebianSymbolsRepairTests(unittest.TestCase):
    def _run(self, metadata: str, log: str, symbols: str, name: str = "libdemo1.symbols"):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        repo, _ = make_workspace(root, metadata=metadata, log=log)
        (repo / "debian").mkdir()
        (repo / "debian" / "rules").write_text("#!/usr/bin/make -f\n%:\n\tdh $@\n", encoding="utf-8")
        target = repo / "debian" / name
        target.write_bytes(symbols.encode("utf-8"))
        outcome = RepairController(
            config=runtime_config(root),
            workspace=WorkspacePaths.resolve(root),
            build_feedback=UnavailableBuildFeedbackProvider(),
        ).run()
        return outcome, target

    def test_x86_only_symbols_are_excluded_on_arm64_and_kept_elsewhere(self) -> None:
        removed = [" demo_block_hash_avx2@Base 1.0", " demo_sse2_kernel@Base 1.0"]
        log = _gensymbols_log("debian/libdemo1.symbols", "arm64", removed, "1.0-2")
        outcome, target = self._run(_metadata("x86_64", "aarch64"), log, SYMBOLS)

        self.assertEqual(outcome.metrics.selected_repair_family, RepairFamily.PACKAGING_RULE.value)
        self.assertEqual(outcome.result.modified_paths, ["debian/libdemo1.symbols"])
        repaired = target.read_text(encoding="utf-8")
        self.assertIn(" (arch=!arm64)demo_block_hash_avx2@Base 1.0\n", repaired)
        self.assertIn(" (arch=!arm64)demo_sse2_kernel@Base 1.0\n", repaired)
        # Non-adjacent changes are composed into one edit; everything else is untouched.
        self.assertEqual(
            repaired.replace("(arch=!arm64)", ""),
            SYMBOLS,
        )

    def test_positive_architecture_list_drops_only_the_target(self) -> None:
        symbols = SYMBOLS.replace(
            " demo_version@Base 1.0\n", " (arch=amd64 ppc64el)demo_version@Base 1.0\n"
        )
        removed = [" (arch=amd64 ppc64el)demo_version@Base 1.0"]
        log = _gensymbols_log("debian/libdemo1.symbols", "amd64", removed, "1.0-2")
        outcome, target = self._run(_metadata("aarch64", "x86_64"), log, symbols)

        self.assertEqual(outcome.result.modified_paths, ["debian/libdemo1.symbols"])
        self.assertIn(" (arch=ppc64el)demo_version@Base 1.0\n", target.read_text(encoding="utf-8"))

    def test_target_only_symbol_uses_dpkg_missing_marker(self) -> None:
        symbols = SYMBOLS.replace(
            " demo_version@Base 1.0\n", " (arch=amd64)demo_version@Base 1.0\n"
        )
        removed = [" (arch=amd64)demo_version@Base 1.0"]
        log = _gensymbols_log("debian/libdemo1.symbols", "amd64", removed, "1:1.0-2ubuntu1")
        outcome, target = self._run(_metadata("aarch64", "x86_64"), log, symbols)

        self.assertEqual(outcome.result.modified_paths, ["debian/libdemo1.symbols"])
        self.assertIn(
            "#MISSING: 1:1.0-2ubuntu1# (arch=amd64)demo_version@Base 1.0\n",
            target.read_text(encoding="utf-8"),
        )

    def test_architecture_specific_symbols_file_and_crlf_are_preserved(self) -> None:
        symbols = SYMBOLS.replace("\n", "\r\n")
        removed = [" demo_read@Base 1.0"]
        log = _gensymbols_log("debian/libdemo1.symbols.amd64", "amd64", removed, "1.0-2")
        outcome, target = self._run(
            _metadata("aarch64", "x86_64"), log, symbols, name="libdemo1.symbols.amd64"
        )

        self.assertEqual(outcome.result.modified_paths, ["debian/libdemo1.symbols.amd64"])
        data = target.read_bytes()
        self.assertIn(b" (arch=!amd64)demo_read@Base 1.0\r\n", data)
        self.assertNotIn(b"\r\r", data)
        self.assertEqual(data.count(b"\r\n"), symbols.count("\r\n"))

    def test_broken_library_with_most_symbols_gone_is_not_masked(self) -> None:
        removed = [line for line in SYMBOLS.splitlines() if line.startswith(" ")][:4]
        log = _gensymbols_log("debian/libdemo1.symbols", "arm64", removed, "1.0-2")
        outcome, target = self._run(_metadata("x86_64", "aarch64"), log, SYMBOLS)

        self.assertEqual(outcome.result.modified_paths, [])
        self.assertEqual(target.read_text(encoding="utf-8"), SYMBOLS)

    def test_unlocatable_symbol_fails_closed(self) -> None:
        removed = [" demo_read@Base 1.0", " demo_not_in_file@Base 1.0"]
        log = _gensymbols_log("debian/libdemo1.symbols", "arm64", removed, "1.0-2")
        outcome, target = self._run(_metadata("x86_64", "aarch64"), log, SYMBOLS)

        self.assertEqual(outcome.result.modified_paths, [])
        self.assertEqual(target.read_text(encoding="utf-8"), SYMBOLS)

    def test_log_architecture_must_match_case_target(self) -> None:
        removed = [" demo_read@Base 1.0"]
        log = _gensymbols_log("debian/libdemo1.symbols", "amd64", removed, "1.0-2")
        outcome, target = self._run(_metadata("x86_64", "aarch64"), log, SYMBOLS)

        self.assertEqual(outcome.result.modified_paths, [])
        self.assertEqual(target.read_text(encoding="utf-8"), SYMBOLS)

    def test_new_symbols_warning_alone_changes_nothing(self) -> None:
        log = (
            "dpkg-gensymbols: warning: some new symbols appeared in the symbols file: "
            "see diff output below\n"
            "--- debian/libdemo1.symbols (libdemo1_1.0-2_arm64)\n"
            "+++ dpkg-gensymbolsAbCdEf\t2026-01-01 00:00:00.000000000 +0000\n"
            "+ demo_extra@Base 1.0-2\n"
            "dpkg-buildpackage: error: debian/rules binary-arch subprocess returned exit status 2\n"
        )
        outcome, target = self._run(_metadata("x86_64", "aarch64"), log, SYMBOLS)

        self.assertEqual(outcome.result.modified_paths, [])
        self.assertEqual(target.read_text(encoding="utf-8"), SYMBOLS)


class SymbolTagTests(unittest.TestCase):
    def test_tag_forms(self) -> None:
        cases = {
            " sym@Base 1": " (arch=!arm64)sym@Base 1",
            ' (c++|optional)"ns::f()@Base" 1': ' (c++|optional|arch=!arm64)"ns::f()@Base" 1',
            " (arch=!i386)sym@Base 1": " (arch=!i386 !arm64)sym@Base 1",
            " (arch=arm64 armhf)sym@Base 1": " (arch=armhf)sym@Base 1",
            " (optional=templinst|arch=arm64 ppc64el)sym@Base 1": (
                " (optional=templinst|arch=ppc64el)sym@Base 1"
            ),
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.assertEqual(_exclude_symbol_arch(line, "arm64"), expected)

    def test_ambiguous_tags_are_refused(self) -> None:
        for line in (
            " (arch=!arm64)sym@Base 1",
            " (arch=amd64)sym@Base 1",
            " (arch=arm64)sym@Base 1",
            " (arch=amd64 !i386)sym@Base 1",
            " (arch=amd64|arch=i386)sym@Base 1",
            "#MISSING: 1# sym@Base 1",
            "libdemo.so.1 libdemo1 #MINVER#",
            "* Build-Depends-Package: libdemo-dev",
            " | libother1",
        ):
            with self.subTest(line=line):
                self.assertIsNone(_exclude_symbol_arch(line, "arm64"))


class PromptClipTests(unittest.TestCase):
    def test_short_text_is_unchanged_and_long_text_is_bounded(self) -> None:
        self.assertEqual(_clip_for_prompt("abc"), "abc")
        clipped = _clip_for_prompt("x" * 50_000, limit=6_000)
        self.assertLess(len(clipped), 6_100)
        self.assertIn("44000 characters omitted", clipped)


if __name__ == "__main__":
    unittest.main()
