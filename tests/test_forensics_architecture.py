from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.architecture.aliases import normalize_architecture
from src.architecture.familiarity import assess_familiarity
from src.architecture.hazard_scanner import scan_architecture_hazards
from src.architecture.profile import construct_profile
from src.architecture.hazard_scanner import ArchitectureHazard
from src.architecture.semantic_anchor import extract_anchor, validate_anchor
from src.context.repository_search import inventory_repository, search_regex
from src.forensics.error_signature import normalize_error_signature
from src.forensics.frontier import FrontierMovement, compare_frontiers
from src.forensics.log_parser import analyze_build_log, identify_primary_file, normalize_log
from src.schemas import Architecture, FailureFrontier, FrontierStage, SemanticAnchor


class ForensicsArchitectureTests(unittest.TestCase):
    def test_repository_scans_skip_inaccessible_directory_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            (repo / "good.c").write_text("int x86_portable;\n", encoding="utf-8")
            inaccessible = repo / "inaccessible"
            inaccessible.write_text("-march=native\n", encoding="utf-8")
            original_is_file = Path.is_file

            def selective_is_file(path: Path) -> bool:
                if path.name == "inaccessible":
                    raise OSError("simulated inaccessible entry")
                return original_is_file(path)

            with patch.object(Path, "is_file", selective_is_file):
                inventory = inventory_repository(repo)
                hazards = scan_architecture_hazards(repo)
                snippets = search_regex(repo, "x86_portable")

            self.assertEqual(inventory.files, ["good.c"])
            self.assertEqual(hazards, [])
            self.assertEqual([snippet.path for snippet in snippets], ["good.c"])

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

    def test_release_log_does_not_treat_documentation_command_as_missing_tool(self) -> None:
        analysis = analyze_build_log(
            "canvas.h:205: warning: argument 'cx' of command @param is not found in the "
            "argument list\n"
            "canvas.cc:18:7: error: use of undeclared identifier 'cx'\n"
        )
        self.assertEqual(
            analysis.primary_diagnostic,
            "canvas.cc:18:7: error: use of undeclared identifier 'cx'",
        )

    def test_release_log_ignores_tex_and_configure_warnings(self) -> None:
        analysis = analyze_build_log(
            "chapter.tex:397: Warning: Command not found: \\downarrow\n"
            "configure: WARNING: unrecognized options: --disable-werror\n"
            "configure: error: C compiler cannot create executables\n"
        )
        self.assertEqual(
            analysis.primary_diagnostic,
            "configure: error: C compiler cannot create executables",
        )
        self.assertEqual(analysis.frontier.stage, FrontierStage.CONFIGURE)

    def test_release_log_ignores_libtool_command_before_test_assertion(self) -> None:
        analysis = analyze_build_log(
            "/bin/bash ./libtool --tag=CC --mode=compile gcc -DERROR_LABEL=error -c test.c\n"
            "libtool: compile: gcc -DERROR_LABEL=error -c test.c\n"
            "Error: test/lib/server.c:16: assertion failed: *fd != -1\n"
            "Error: child killed by signal 6 (Aborted)\n"
        )
        self.assertIn("assertion failed", analysis.primary_diagnostic)
        self.assertEqual(analysis.frontier.stage, FrontierStage.TEST)

    def test_release_log_ignores_split_cargo_build_script_command(self) -> None:
        analysis = analyze_build_log(
            "' CARGO_PKG_NAME=demo CARGO_PKG_DESCRIPTION='Error helper' "
            "target/build/demo/build-script-build`\n"
            "store-test: db.cc:16: Assertion `last_ref' failed.\n"
            "error: test failed, to rerun pass `-p demo --test store`\n"
        )
        self.assertIn("Assertion `last_ref' failed", analysis.primary_diagnostic)
        self.assertEqual(analysis.frontier.stage, FrontierStage.TEST)

    def test_release_log_ignores_autoconf_compiler_version_probes(self) -> None:
        analysis = analyze_build_log(
            "gcc: error: unrecognized command-line option '-V'\n"
            "gcc: fatal error: no input files\n"
            "gcc: error: unrecognized command-line option '-qversion'; did you mean '--version'?\n"
            "gcc: fatal error: no input files\n"
            "gcc: error: unrecognized command-line option '-fno-cf-protection'; did you mean "
            "'-fcf-protection'?\n"
            "configure: error: C compiler cannot create executables\n"
        )
        self.assertIn("-fno-cf-protection", analysis.primary_diagnostic)
        self.assertEqual(analysis.frontier.stage, FrontierStage.COMPILE)

    def test_release_log_ignores_autoconf_conftest_failures(self) -> None:
        analysis = analyze_build_log(
            "| conftest.c:49:10: fatal error: minix/config.h: No such file or directory\n"
            "| conftest.c:72:6: warning: conflicting types for built-in function 'memchr'\n"
            "conftest.c:105:14: error: too few arguments to function 'strerror_r'\n"
            "| gcc: error: unrecognized command-line option '-fdebug-macro'\n"
            "| configure: failed program was:\n"
            "FAIL: run-debugedit-self-test\n"
            "AssertionError: generated debug sections differ\n"
        )
        self.assertEqual(
            analysis.primary_diagnostic,
            "AssertionError: generated debug sections differ",
        )
        self.assertEqual(analysis.frontier.stage, FrontierStage.TEST)

    def test_release_log_prefers_specific_test_issue_over_failure_summary(self) -> None:
        analysis = analyze_build_log(
            "ERROR: All 58 tests were run, 5 failed unexpectedly.\n"
            "Failed tests:\n"
            "ERROR: eu-elflint found issues in tests/testsuite.dir/54/subdir_build/bar\n"
            "make: *** [check] Error 1\n"
        )
        self.assertIn("eu-elflint found issues", analysis.primary_diagnostic)
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

    def test_release_log_ignores_environment_and_echoed_cargo_commands(self) -> None:
        analysis = analyze_build_log(
            "Inherited environment: CARGO_PKG_DESCRIPTION='Flexible Error type'\n"
            "     Running `CARGO=/usr/bin/cargo CARGO_PKG_NAME=anyhow rustc --crate-name anyhow`\n"
            "src/arch.rs:41:9: error[E0432]: unresolved import `mach_o`\n"
            "error: could not compile `mach-o-sys` due to 1 previous error\n"
        )
        self.assertEqual(
            analysis.primary_diagnostic,
            "src/arch.rs:41:9: error[E0432]: unresolved import `mach_o`",
        )
        self.assertEqual(analysis.frontier.primary_file, "src/arch.rs")

    def test_release_log_prefers_compiler_error_over_echoed_compile_command(self) -> None:
        analysis = analyze_build_log(
            "cd /<<PKGBUILDDIR>>/obj-aarch64-linux-gnu && /usr/bin/c++ "
            "-DERROR_LABEL=error -O2 -c source.c\n"
            "source.c:17:4: error: unknown type name 'target_word'\n"
            "make: *** [Makefile:20: source.o] Error 1\n"
        )
        self.assertEqual(
            analysis.primary_diagnostic,
            "source.c:17:4: error: unknown type name 'target_word'",
        )
        self.assertEqual(analysis.frontier.primary_file, "source.c")

    def test_release_log_selects_dpkg_control_error(self) -> None:
        analysis = analyze_build_log(
            "dpkg-deb: error: parsing file 'debian/pkg/DEBIAN/control' near line 8\n"
            "dpkg-buildpackage: error: debian/rules binary subprocess returned exit status 2\n"
        )
        self.assertTrue(analysis.primary_diagnostic.startswith("dpkg-deb: error:"))
        self.assertEqual(analysis.frontier.stage, FrontierStage.PACKAGE)

    def test_release_log_selects_cmake_package_error_and_file(self) -> None:
        analysis = analyze_build_log(
            "CMake Error at CMakeLists.txt:108 (find_package):\n"
            '  Could not find a package configuration file provided by "DeviceLibs"\n'
            "dh_auto_configure: error: cmake returned exit code 1\n"
        )
        self.assertTrue(analysis.primary_diagnostic.startswith("CMake Error at"))
        self.assertEqual(analysis.frontier.stage, FrontierStage.CONFIGURE)
        self.assertEqual(analysis.frontier.primary_file, "CMakeLists.txt")

    def test_release_log_ignores_compiler_note_after_link_failure(self) -> None:
        analysis = analyze_build_log(
            "/usr/src/pkg/source.cpp:227:(.text+0xebd0): undefined reference to "
            "`optimized_sse2'\n"
            "cc1plus: note: unrecognized command-line option '-Wno-unused' may have been "
            "intended to silence earlier diagnostics\n"
            "collect2: error: ld returned 1 exit status\n"
        )
        self.assertIn("undefined reference", analysis.primary_diagnostic)
        self.assertEqual(analysis.frontier.stage, FrontierStage.LINK)

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
