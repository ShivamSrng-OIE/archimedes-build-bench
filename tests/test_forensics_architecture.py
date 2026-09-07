from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.architecture.aliases import normalize_architecture
from src.architecture.familiarity import assess_familiarity
from src.architecture.hazard_scanner import scan_architecture_hazards
from src.architecture.profile import construct_profile
from src.architecture.hazard_scanner import ArchitectureHazard
from src.architecture.semantic_anchor import extract_anchor, validate_anchor
from src.forensics.error_signature import normalize_error_signature
from src.forensics.frontier import FrontierMovement, compare_frontiers
from src.forensics.log_parser import analyze_build_log, identify_primary_file, normalize_log
from src.schemas import Architecture, FailureFrontier, FrontierStage, SemanticAnchor


class ForensicsArchitectureTests(unittest.TestCase):
    def test_architecture_aliases_and_unknown_raw_value(self) -> None:
        self.assertEqual(normalize_architecture("AMD64").canonical, Architecture.X86_64)
        self.assertEqual(normalize_architecture("arm64").canonical, Architecture.AARCH64)
        self.assertEqual(normalize_architecture("riscv64gc").canonical, Architecture.RISCV64)
        unknown = normalize_architecture("loongarch64")
        self.assertEqual(unknown.canonical, Architecture.UNKNOWN)
        self.assertEqual(unknown.raw, "loongarch64")

    def test_log_normalization_and_first_causal_error(self) -> None:
        raw = (
            "\x1b[31m2026-08-20T10:00:00Z cc: error: unrecognized command-line "
            "option '-msse4.2'\x1b[0m\r\n"
            "make: *** [all] Error 2\r\nmake: *** [all] Error 2\r\n"
        )
        normalized = normalize_log(raw)
        self.assertNotIn("\x1b", normalized)
        self.assertIn("repeated 2 times", normalized)
        analysis = analyze_build_log(raw)
        self.assertIn("-msse4.2", analysis.primary_diagnostic)
        self.assertEqual(analysis.frontier.stage, FrontierStage.COMPILE)

    def test_release_log_ignores_zero_error_summary_and_selects_failed_test(self) -> None:
        raw = (
            "# ERROR: 0\n"
            "FAIL: tests/swig_perl\n"
            "# Failed test 'Adding mer to hash'\n"
            "dh_auto_test: error: make -j4 check returned exit code 2\n"
        )
        analysis = analyze_build_log(raw)
        self.assertEqual(analysis.primary_diagnostic, "FAIL: tests/swig_perl")
        self.assertEqual(analysis.frontier.stage, FrontierStage.TEST)

    def test_release_log_prefers_specific_packaging_path_over_dpkg_wrapper(self) -> None:
        raw = (
            "chmod: cannot access 'debian/node-re2/usr/lib/x86_64-linux-gnu/nodejs/tool.js': "
            "No such file or directory\n"
            "make[1]: *** [debian/rules:25: override_dh_fixperms] Error 1\n"
            "dpkg-buildpackage: error: debian/rules binary-arch subprocess returned exit status 2\n"
        )
        analysis = analyze_build_log(raw)
        self.assertTrue(analysis.primary_diagnostic.startswith("chmod: cannot access"))
        self.assertEqual(analysis.frontier.stage, FrontierStage.PACKAGE)
        self.assertEqual(analysis.frontier.primary_file, "debian/node-re2/usr/lib/x86_64-linux-gnu/nodejs/tool.js")

    def test_release_log_ignores_latex_warning_before_test_assertion(self) -> None:
        raw = (
            "LaTeX Warning: There were undefined references.\n"
            "FAIL: test_blastn (test_NCBI_BLAST_tools.Pairwise)\n"
            "AssertionError: 132 != 0 : Got error code 132 back\n"
        )
        analysis = analyze_build_log(raw)
        self.assertEqual(analysis.primary_diagnostic, "AssertionError: 132 != 0 : Got error code 132 back")
        self.assertEqual(analysis.frontier.stage, FrontierStage.TEST)

    def test_primary_file_normalizes_launchpad_build_paths(self) -> None:
        self.assertEqual(
            identify_primary_file(
                "/<<PKGBUILDDIR>>/setup.py:33: SyntaxWarning",
                "python3 setup.py build_sphinx",
            ),
            "setup.py",
        )
        self.assertEqual(
            identify_primary_file("make[1]: *** [debian/rules:28: override_dh_installdocs] Error 1"),
            "debian/rules",
        )

    def test_release_log_classifies_inactivity_kill_as_environment(self) -> None:
        analysis = analyze_build_log(
            "E: Build killed with signal TERM after 150 minutes of inactivity\n"
            "E: Build failure (dpkg-buildpackage died)\n"
        )
        self.assertIn("150 minutes of inactivity", analysis.primary_diagnostic)
        self.assertEqual(analysis.frontier.stage, FrontierStage.ENVIRONMENT)

    def test_release_log_prefers_specific_missing_packaging_input(self) -> None:
        analysis = analyze_build_log(
            'dh_installdocs: Cannot find (any matches for) "debian/tests/run-tests" '
            "(tried in ., debian/tmp)\n"
            "dpkg-buildpackage: error: fakeroot debian/rules binary subprocess returned exit status 2\n"
        )
        self.assertIn("Cannot find (any matches for)", analysis.primary_diagnostic)
        self.assertEqual(analysis.frontier.stage, FrontierStage.PACKAGE)

    def test_release_log_treats_target_binary_crash_as_test_failure(self) -> None:
        analysis = analyze_build_log(
            "LD_LIBRARY_PATH=. ./tests/convolution.test\n"
            "Segmentation fault (core dumped)\n"
            "dh_auto_test: error: make -j4 test returned exit code 2\n"
        )
        self.assertTrue(analysis.primary_diagnostic.startswith("Segmentation fault"))
        self.assertEqual(analysis.frontier.stage, FrontierStage.TEST)

    def test_error_signature_removes_unstable_material(self) -> None:
        first = "C:/Temp/a123/file.c:42:9: error: bad value '-march=x86-64-v3'"
        second = "C:/Temp/b456/file.c:99:3: error: bad value '-march=x86-64-v3'"
        self.assertEqual(normalize_error_signature(first), normalize_error_signature(second))

    def test_frontier_ordering(self) -> None:
        compile_failure = FailureFrontier(FrontierStage.COMPILE, "compile-a")
        link_failure = FailureFrontier(FrontierStage.LINK, "link-a")
        other_compile = FailureFrontier(FrontierStage.COMPILE, "compile-b")
        self.assertEqual(compare_frontiers(compile_failure, link_failure), FrontierMovement.PROGRESS)
        self.assertEqual(compare_frontiers(link_failure, compile_failure), FrontierMovement.REGRESSION)
        self.assertEqual(compare_frontiers(compile_failure, other_compile), FrontierMovement.PARTIAL_PROGRESS)
        self.assertEqual(compare_frontiers(compile_failure, compile_failure), FrontierMovement.INEFFECTIVE)

    def test_hazard_scanner_finds_flags_intrinsics_assembly_and_abi(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            (repo / "build.mk").write_text("CFLAGS += -mavx2\n", encoding="utf-8")
            (repo / "code.c").write_text(
                "#include <immintrin.h>\nvoid f(void){ __asm__(\"pause\"); }\n"
                "_Static_assert(sizeof(void *) == 8, \"width\");\n",
                encoding="utf-8",
            )
            categories = {hazard.category for hazard in scan_architecture_hazards(repo)}
            self.assertTrue({"compiler_flag", "intrinsic_header", "inline_assembly", "abi_assumption"} <= categories)

    def test_unknown_architecture_has_lower_familiarity(self) -> None:
        known = assess_familiarity(construct_profile(normalize_architecture("aarch64")))
        unknown = assess_familiarity(construct_profile(normalize_architecture("mystery128")))
        self.assertGreater(known.score, unknown.score)
        self.assertTrue(unknown.requires_discovery)

    def test_semantic_anchor_validation(self) -> None:
        anchor = SemanticAnchor("src/a.c:2", "f", "intrinsic", "", confidence=0.5)
        findings = validate_anchor(anchor)
        self.assertIn("functional purpose is unknown", findings)
        self.assertIn("semantic invariants are absent", findings)

    def test_extracts_evidence_bounded_compiler_flag_anchor(self) -> None:
        hazard = ArchitectureHazard(
            "Makefile", 2, "compiler_flag", "-msse4.2", "CFLAGS += -msse4.2", 0.88
        )
        anchor = extract_anchor(hazard, evidence_ids=["evidence-002"])
        self.assertIsNotNone(anchor)
        assert anchor is not None
        self.assertEqual(anchor.site, "Makefile:2")
        self.assertEqual(anchor.evidence_ids, ["evidence-002"])
        self.assertEqual(validate_anchor(anchor), [])


if __name__ == "__main__":
    unittest.main()
