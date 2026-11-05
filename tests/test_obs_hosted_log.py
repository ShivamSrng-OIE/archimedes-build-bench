from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from .support import make_workspace, runtime_config

from src.build.unavailable import UnavailableBuildFeedbackProvider
from src.forensics.log_parser import analyze_build_log, normalize_log
from src.reasoning.controller import RepairController
from src.workspace.paths import WorkspacePaths

# Condensed from the organizer's hosted Smoke initial-build.log (5 Nov 2026). The hosted
# Validator prints chkstat ownership noise while preparing the OBS build root.
HOSTED_SETUP_NOISE = """\
[    0s] Memory limit set to 175577442KB
[    0s] Doing docker build
[    9s] 2nd stage started in virtual machine
[   43s] Checking permissions and ownerships - using the permissions files
[   43s] /etc/fstab: has unexpected owner (1000). Refusing to correct due to unknown integrity.
[   43s] /usr/bin/mount: has unexpected owner (1000). Refusing to correct due to unknown integrity.
[   43s] ERROR: not all operations were successful.
[   45s] can't open /etc/polkit-1/rules.d/90-default-privs.rules.new: No such file or directory
[   45s] warning: %post(polkit-default-privs-1550+20260709.b1b58aa-160000.1.1.noarch) scriptlet failed, exit status 2
[   95s] now finalizing build dir...
"""

HOSTED_HELLO_LOG = HOSTED_SETUP_NOISE + """\
[  127s] -----------------------------------------------------------------
[  127s] ----- building buildbench-hello.spec (user abuild)
[  127s] -----------------------------------------------------------------
[  135s] + exec rpmbuild -ba --nosignature /home/abuild/rpmbuild/SOURCES/buildbench-hello.spec
[  136s] Executing(%build): /usr/bin/bash -e /var/tmp/rpm-tmp.hObqZx
[  137s] + echo 'intentional Build-Bench demo failure'
[  137s] intentional Build-Bench demo failure
[  137s] + exit 1
[  137s] error: Bad exit status from /var/tmp/rpm-tmp.hObqZx (%build)
[  137s]
[  137s] RPM build errors:
[  137s]     Bad exit status from /var/tmp/rpm-tmp.hObqZx (%build)
[  137s] cf78427532ed failed "build buildbench-hello.spec" at Thu Nov  5 02:22:18 UTC 2026.
"""


class HostedObsLogTests(unittest.TestCase):
    def test_obs_elapsed_prefix_is_removed(self) -> None:
        normalized = normalize_log("[  137s] intentional failure\n[ 1234s] next\n")
        self.assertEqual(normalized.splitlines(), ["intentional failure", "next"])

    def test_build_failure_outranks_build_root_preparation_noise(self) -> None:
        analysis = analyze_build_log(HOSTED_HELLO_LOG)
        self.assertIn("bad exit status", analysis.primary_diagnostic.lower())
        self.assertIn("%build", analysis.primary_diagnostic)
        self.assertNotIn("not all operations", analysis.primary_diagnostic)

    def test_setup_noise_alone_is_not_treated_as_a_diagnostic(self) -> None:
        analysis = analyze_build_log(HOSTED_SETUP_NOISE)
        self.assertNotIn("not all operations", analysis.primary_diagnostic)
        self.assertNotIn("unexpected owner", analysis.primary_diagnostic)
        self.assertNotIn("scriptlet failed", analysis.primary_diagnostic)

    def test_real_preparation_failure_is_kept_when_build_never_starts(self) -> None:
        log = HOSTED_SETUP_NOISE + "[   96s] error: unresolvable: nothing provides libfoo-devel\n"
        analysis = analyze_build_log(log)
        self.assertIn("nothing provides libfoo-devel", analysis.primary_diagnostic)

    def test_preparation_diagnostic_is_fallback_when_build_section_is_clean(self) -> None:
        log = (
            "[   50s] configure: error: cannot find required tool\n"
            "[  127s] ----- building pkg.spec (user abuild)\n"
            "[  130s] still running\n"
        )
        analysis = analyze_build_log(log)
        self.assertIn("cannot find required tool", analysis.primary_diagnostic)

    def test_marker_without_user_suffix_and_crlf_endings(self) -> None:
        log = HOSTED_HELLO_LOG.replace(" (user abuild)", "").replace("\n", "\r\n")
        analysis = analyze_build_log(log)
        self.assertIn("bad exit status", analysis.primary_diagnostic.lower())

    def test_separator_rule_is_not_a_build_marker(self) -> None:
        log = (
            "[   50s] configure: error: cannot find required tool\n"
            "[  127s] -----------------------------------------------------------------\n"
        )
        analysis = analyze_build_log(log)
        self.assertIn("cannot find required tool", analysis.primary_diagnostic)

    def test_first_build_marker_bounds_preparation_phase(self) -> None:
        log = HOSTED_HELLO_LOG + (
            "[  140s] ----- building second.spec (user abuild)\n"
            "[  141s] still running\n"
        )
        analysis = analyze_build_log(log)
        self.assertIn("bad exit status", analysis.primary_diagnostic.lower())

    def test_empty_and_prefix_only_logs_do_not_crash(self) -> None:
        for log in ("", "[   0s] \n[   1s]\n", "\n\n"):
            with self.subTest(log=log):
                analysis = analyze_build_log(log)
                self.assertEqual(analysis.primary_diagnostic, "")

    def test_compiler_error_inside_build_keeps_path_without_prefix(self) -> None:
        log = HOSTED_SETUP_NOISE + (
            "[  127s] ----- building pkg.spec (user abuild)\n"
            "[  403s] src/lib.c:1:47: error: narrowing conversion of '-81'\n"
            "[  404s] error: Bad exit status from /var/tmp/rpm-tmp.x (%build)\n"
        )
        analysis = analyze_build_log(log)
        self.assertTrue(analysis.primary_diagnostic.startswith("src/lib.c:1:47: error:"))
        self.assertEqual(analysis.family.value, "compilation")

    def test_hosted_hello_failure_is_classified_as_packaging(self) -> None:
        analysis = analyze_build_log(HOSTED_HELLO_LOG)
        self.assertEqual(analysis.family.value, "packaging")

    def test_unresolvable_without_nothing_provides_is_found(self) -> None:
        for line in (
            "unresolvable: conflict for providers of 'libfoo' needed by bar",
            "unresolvable: have choice for pkgconfig(x) needed by foo: a b",
        ):
            with self.subTest(line=line):
                log = HOSTED_SETUP_NOISE + f"[   96s] {line}\n[   96s] build failed\n"
                analysis = analyze_build_log(log)
                self.assertTrue(analysis.primary_diagnostic.startswith("unresolvable:"))
                self.assertEqual(analysis.family.value, "dependency")

    def test_identifier_containing_unresolvable_is_not_a_dependency_failure(self) -> None:
        log = (
            "[  127s] ----- building pkg.spec (user abuild)\n"
            "[  130s] CC libfoo_la-test_unresolvable.lo\n"
            "[  131s] src/a.c:3:1: error: expected ';' before '}' token\n"
        )
        self.assertIn("expected ';'", analyze_build_log(log).primary_diagnostic)

    def test_rpmbuild_error_lines_keep_their_lead_in(self) -> None:
        for message in (
            "File not found: /home/abuild/rpmbuild/BUILDROOT/foo-1.0/usr/bin/foo",
            "Installed (but unpackaged) file(s) found:",
            "line 12: Unknown tag: Bogus: x",
        ):
            with self.subTest(message=message):
                log = HOSTED_SETUP_NOISE + (
                    "[  127s] ----- building foo.spec (user abuild)\n"
                    f"[  200s] error: {message}\n"
                    "[  200s] RPM build errors:\n"
                    f"[  200s]     {message}\n"
                    '[  201s] cf78427532ed failed "build foo.spec" at Thu Nov  5 02:22:18 UTC 2026.\n'
                )
                analysis = analyze_build_log(log)
                self.assertEqual(analysis.primary_diagnostic, f"error: {message}")
                self.assertNotIn("cf78427532ed", analysis.frontier.error_signature)

    def test_signature_is_stable_across_attempt_timestamps(self) -> None:
        first = analyze_build_log(HOSTED_HELLO_LOG)
        second = analyze_build_log(HOSTED_HELLO_LOG.replace("[  137s]", "[  412s]"))
        self.assertEqual(first.frontier.error_signature, second.frontier.error_signature)

    def test_in_build_compiler_error_outranks_solver_text(self) -> None:
        log = (
            "[  127s] ----- building libsolv.spec (user abuild)\n"
            "[  150s] testcase: problem 1: nothing provides A needed by B (expected)\n"
            "[  151s] src/x.c:9:2: error: 'foo' undeclared\n"
        )
        self.assertIn("'foo' undeclared", analyze_build_log(log).primary_diagnostic)

    def test_hosted_failure_lines_never_become_the_fallback(self) -> None:
        log = HOSTED_SETUP_NOISE + (
            "[  127s] ----- building foo.spec (user abuild)\n"
            "[  300s] something unusual happened\n"
            '[  301s] bfdc1acce7cf failed "build foo.spec" at Thu Nov  5 02:22:18 UTC 2026.\n'
            '[build-case] [event] {"event":"stage","stage":"package_build","state":"failed"}\n'
        )
        self.assertEqual(analyze_build_log(log).primary_diagnostic, "something unusual happened")

    def test_specific_failures_outrank_generic_lines(self) -> None:
        cases = (
            ("[ 200s] FAIL: t_flaky\n[ 201s] + :\n"
             "[ 210s] error: File not found: /home/abuild/rpmbuild/BUILDROOT/f/usr/lib64/libfoo.so.1\n",
             "error: File not found:", "packaging"),
            ("[ 200s] /home/abuild/rpmbuild/BUILD/foo/test/endian.cc:22: Failure\n"
             "[ 200s] [  FAILED  ] Endian.Swap (0 ms)\n"
             "[ 201s] error: Bad exit status from /var/tmp/rpm-tmp.X (%check)\n",
             "endian.cc:22: Failure", "test"),
            ("[ 200s] --- FAIL: TestEndian (0.00s)\n[ 200s] FAIL\tgithub.com/x/foo\t0.01s\n"
             "[ 201s] error: Bad exit status from /var/tmp/rpm-tmp.X (%check)\n",
             "--- FAIL: TestEndian", "test"),
            ("[ 200s]  2/5 foo:endian          FAIL            0.02s   exit status 1\n"
             "[ 200s] Fail:               1\n",
             "foo:endian", "test"),
            ("[ 200s] 1 out of 3 hunks FAILED -- saving rejects to file src/arch.c.rej\n"
             "[ 201s] error: Bad exit status from /var/tmp/rpm-tmp.X (%prep)\n",
             "hunks FAILED", "build_preparation"),
            ("[ 300s] Out of memory: Killed process 4242 (cc1plus)\n"
             "[ 300s] make: *** [Makefile:12: big.o] Error 137\n",
             "Out of memory", "environment"),
            ("[ 300s] (none): E: badness 10000 exceeds threshold 1000, aborting.\n",
             "badness 10000", "packaging"),
        )
        for body, expected, family in cases:
            with self.subTest(expected=expected):
                log = HOSTED_SETUP_NOISE + "[ 127s] ----- building foo.spec (user abuild)\n" + body
                analysis = analyze_build_log(log)
                self.assertIn(expected, analysis.primary_diagnostic)
                self.assertEqual(analysis.family.value, family)

    def test_versioned_and_debian_dependency_failures(self) -> None:
        obs = HOSTED_SETUP_NOISE + (
            "[ 96s]   nothing provides cross-aarch64-gcc13 >= 13.2 needed by foo\n"
            "[ 96s]   nothing provides libfoo-devel needed by foo\n"
        )
        self.assertIn("cross-aarch64-gcc13", analyze_build_log(obs).primary_diagnostic)
        sbuild = (
            "The following packages have unmet dependencies:\n"
            " sbuild-build-depends-main-dummy:arm64 : Depends: libfoo-dev:arm64 but it is not installable\n"
            "E: Unable to correct problems, you have held broken packages.\n"
            "Not removing build depends: as requested\n"
        )
        analysis = analyze_build_log(sbuild)
        self.assertIn("libfoo-dev:arm64 but it is not installable", analysis.primary_diagnostic)
        self.assertEqual(analysis.family.value, "dependency")

    def test_hosted_hello_log_is_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            repo, _ = make_workspace(
                root, metadata=json.dumps({"case_id": "hello"}), log=HOSTED_HELLO_LOG
            )
            package_input = repo / "input"
            package_input.mkdir()
            spec = package_input / "buildbench-hello.spec"
            spec.write_text(
                "Name: buildbench-hello\n%build\n"
                "# BUILD-BENCH-DEMO-BROKEN\n"
                'echo "intentional Build-Bench demo failure" >&2\n'
                "exit 1\n%install\n",
                encoding="utf-8",
            )
            outcome = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
            ).run()
            self.assertEqual(outcome.result.modified_paths, ["input/buildbench-hello.spec"])
            self.assertNotIn("exit 1", spec.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
