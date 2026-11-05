"""Tests for reading Open Build Service (OBS) logs as the hosted Validator prints them.

Hosted logs put an elapsed-time prefix such as "[  137s] " on every line, start with
build-root preparation noise (chkstat ownership errors, a failed polkit scriptlet),
mark the real build with a "----- building foo.spec (user abuild)" line, and end with
rpmbuild's "RPM build errors:" block and a host line such as
'cf78427532ed failed "build foo.spec" at <date>'. The tests check that
analyze_build_log() picks the real cause, classifies its family and gives a
signature that stays the same across attempts. Some samples use sbuild output too.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.build.unavailable import UnavailableBuildFeedbackProvider
from src.forensics.log_parser import analyze_build_log, normalize_log
from src.reasoning.controller import RepairController
from src.workspace.paths import WorkspacePaths

from .support import make_workspace, runtime_config

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
    """Check diagnostic selection, family classification and signatures for OBS logs."""

    def test_obs_elapsed_prefix_is_removed(self) -> None:
        """Check that normalize_log() strips the "[  137s] " elapsed-time prefix from each line."""
        normalized = normalize_log("[  137s] intentional failure\n[ 1234s] next\n")
        self.assertEqual(normalized.splitlines(), ["intentional failure", "next"])

    def test_build_failure_outranks_build_root_preparation_noise(self) -> None:
        """Check that rpmbuild's "Bad exit status ... (%build)" wins over chkstat setup errors.

        The build-root preparation errors appear in every hosted log and are never the cause.
        """
        analysis = analyze_build_log(HOSTED_HELLO_LOG)
        self.assertIn("bad exit status", analysis.primary_diagnostic.lower())
        self.assertIn("%build", analysis.primary_diagnostic)
        self.assertNotIn("not all operations", analysis.primary_diagnostic)

    def test_setup_noise_alone_is_not_treated_as_a_diagnostic(self) -> None:
        """Check that chkstat ownership lines and the polkit scriptlet warning are never chosen."""
        analysis = analyze_build_log(HOSTED_SETUP_NOISE)
        self.assertNotIn("not all operations", analysis.primary_diagnostic)
        self.assertNotIn("unexpected owner", analysis.primary_diagnostic)
        self.assertNotIn("scriptlet failed", analysis.primary_diagnostic)

    def test_real_preparation_failure_is_kept_when_build_never_starts(self) -> None:
        """Check that an "unresolvable: nothing provides" error before the build marker is kept.

        A dependency solver failure is a real cause even though it sits in the setup phase.
        """
        log = HOSTED_SETUP_NOISE + "[   96s] error: unresolvable: nothing provides libfoo-devel\n"
        analysis = analyze_build_log(log)
        self.assertIn("nothing provides libfoo-devel", analysis.primary_diagnostic)

    def test_preparation_diagnostic_is_fallback_when_build_section_is_clean(self) -> None:
        """Check that an error before the build marker is used when the build section has none."""
        log = (
            "[   50s] configure: error: cannot find required tool\n"
            "[  127s] ----- building pkg.spec (user abuild)\n"
            "[  130s] still running\n"
        )
        analysis = analyze_build_log(log)
        self.assertIn("cannot find required tool", analysis.primary_diagnostic)

    def test_marker_without_user_suffix_and_crlf_endings(self) -> None:
        """Check that a marker without "(user abuild)" and CRLF line endings are both handled."""
        log = HOSTED_HELLO_LOG.replace(" (user abuild)", "").replace("\n", "\r\n")
        analysis = analyze_build_log(log)
        self.assertIn("bad exit status", analysis.primary_diagnostic.lower())

    def test_separator_rule_is_not_a_build_marker(self) -> None:
        """Check that the "-----..." separator line alone does not start the build section."""
        log = (
            "[   50s] configure: error: cannot find required tool\n"
            "[  127s] -----------------------------------------------------------------\n"
        )
        analysis = analyze_build_log(log)
        self.assertIn("cannot find required tool", analysis.primary_diagnostic)

    def test_first_build_marker_bounds_preparation_phase(self) -> None:
        """Check that a second "----- building" marker does not move the end of the setup phase."""
        log = HOSTED_HELLO_LOG + (
            "[  140s] ----- building second.spec (user abuild)\n"
            "[  141s] still running\n"
        )
        analysis = analyze_build_log(log)
        self.assertIn("bad exit status", analysis.primary_diagnostic.lower())

    def test_empty_and_prefix_only_logs_do_not_crash(self) -> None:
        """Check that empty logs and logs with only timestamp prefixes give an empty diagnostic."""
        for log in ("", "[   0s] \n[   1s]\n", "\n\n"):
            with self.subTest(log=log):
                analysis = analyze_build_log(log)
                self.assertEqual(analysis.primary_diagnostic, "")

    def test_compiler_error_inside_build_keeps_path_without_prefix(self) -> None:
        """Check that a compiler error is reported as "src/lib.c:1:47: error:" with no OBS prefix.

        The file path must stay at the start so later stages can locate the source line.
        """
        log = HOSTED_SETUP_NOISE + (
            "[  127s] ----- building pkg.spec (user abuild)\n"
            "[  403s] src/lib.c:1:47: error: narrowing conversion of '-81'\n"
            "[  404s] error: Bad exit status from /var/tmp/rpm-tmp.x (%build)\n"
        )
        analysis = analyze_build_log(log)
        self.assertTrue(analysis.primary_diagnostic.startswith("src/lib.c:1:47: error:"))
        self.assertEqual(analysis.family.value, "compilation")

    def test_hosted_hello_failure_is_classified_as_packaging(self) -> None:
        """Check that the hosted hello demo failure, a bad %build exit, is classed as packaging."""
        analysis = analyze_build_log(HOSTED_HELLO_LOG)
        self.assertEqual(analysis.family.value, "packaging")

    def test_unresolvable_without_nothing_provides_is_found(self) -> None:
        """Check that "conflict for providers" and "have choice for" lines are dependency errors."""
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
        """Check that an object file name containing "unresolvable" is not a solver error."""
        log = (
            "[  127s] ----- building pkg.spec (user abuild)\n"
            "[  130s] CC libfoo_la-test_unresolvable.lo\n"
            "[  131s] src/a.c:3:1: error: expected ';' before '}' token\n"
        )
        self.assertIn("expected ';'", analyze_build_log(log).primary_diagnostic)

    def test_rpmbuild_error_lines_keep_their_lead_in(self) -> None:
        """Check that rpmbuild errors keep their "error: " lead-in and the host id stays out.

        The hex container id in the final "failed" line changes every run, so it must
        not reach the error signature.
        """
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
        """Check that the error signature does not change when only the elapsed times differ.

        The controller compares signatures between attempts to see whether a fix made progress.
        """
        first = analyze_build_log(HOSTED_HELLO_LOG)
        second = analyze_build_log(HOSTED_HELLO_LOG.replace("[  137s]", "[  412s]"))
        self.assertEqual(first.frontier.error_signature, second.frontier.error_signature)

    def test_in_build_compiler_error_outranks_solver_text(self) -> None:
        """Check that a compiler error beats "nothing provides" text printed by a test suite."""
        log = (
            "[  127s] ----- building libsolv.spec (user abuild)\n"
            "[  150s] testcase: problem 1: nothing provides A needed by B (expected)\n"
            "[  151s] src/x.c:9:2: error: 'foo' undeclared\n"
        )
        self.assertIn("'foo' undeclared", analyze_build_log(log).primary_diagnostic)

    def test_hosted_failure_lines_never_become_the_fallback(self) -> None:
        """Check that the host's 'failed "build foo.spec"' line and [build-case] events are skipped.

        With no recognised error, the last ordinary build line is used instead.
        """
        log = HOSTED_SETUP_NOISE + (
            "[  127s] ----- building foo.spec (user abuild)\n"
            "[  300s] something unusual happened\n"
            '[  301s] bfdc1acce7cf failed "build foo.spec" at Thu Nov  5 02:22:18 UTC 2026.\n'
            '[build-case] [event] {"event":"stage","stage":"package_build","state":"failed"}\n'
        )
        self.assertEqual(analyze_build_log(log).primary_diagnostic, "something unusual happened")

    def test_specific_failures_outrank_generic_lines(self) -> None:
        """Check that specific test, patch, OOM, rpmlint and rpm errors beat generic FAIL lines.

        Each sample also checks the failure family the specific line maps to.
        """
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
        """Check versioned OBS "nothing provides" lines and sbuild unmet-dependency output."""
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

    def test_host_noise_does_not_lend_a_family(self) -> None:
        """Check that setup noise and an unrecognised build line leave the family as unknown."""
        for body in (
            "[  95s] now finalizing build dir...\n",
            "[ 127s] ----- building foo.spec (user abuild)\n[ 300s] something unusual happened\n",
        ):
            with self.subTest(body=body):
                self.assertEqual(analyze_build_log(HOSTED_SETUP_NOISE + body).family.value, "unknown")

    def test_repeated_lines_still_match_anchored_patterns(self) -> None:
        """Check that patterns anchored at the line start still match when a line repeats.

        Repeated lines are collapsed, and that must not hide the real diagnostic.
        """
        noise = (
            "[   18s] ERROR: not all operations were successful.\n"
            "[   19s] ERROR: not all operations were successful.\n"
            "[   20s] now finalizing build dir...\n"
            "[   21s] post-build-checks: something odd\n"
        )
        self.assertEqual(analyze_build_log(noise).primary_diagnostic, "post-build-checks: something odd")
        shell = (
            "[ 127s] ----- building foo.spec (user abuild)\n"
            "[ 140s] /var/tmp/rpm-tmp.X: line 40: ./autogen.sh: No such file or directory\n"
            "[ 140s] /var/tmp/rpm-tmp.X: line 40: ./autogen.sh: No such file or directory\n"
            "[ 141s] error: Bad exit status from /var/tmp/rpm-tmp.X (%build)\n"
        )
        self.assertIn("autogen.sh: No such file", analyze_build_log(shell).primary_diagnostic)

    def test_direct_build_requires_without_needed_by(self) -> None:
        """Check that "nothing provides libfoo-devel" without "needed by" is a dependency error."""
        log = (
            "[ 3s] expansion error\n[ 3s]   nothing provides libfoo-devel\n"
            "[ 3s] build: cleanup\n"
            '[build-case] [event] {"event":"stage","stage":"package_build","state":"failed"}\n'
        )
        analysis = analyze_build_log(log)
        self.assertIn("nothing provides libfoo-devel", analysis.primary_diagnostic)
        self.assertEqual(analysis.family.value, "dependency")

    def test_rpm_spec_and_directory_errors_are_packaging(self) -> None:
        """Check that spec tag and missing BUILDROOT directory errors are packaging failures.

        A Python "ERROR:root:" logging line earlier in the build must not win.
        """
        for message in (
            "line 25: Unknown tag: Foo: bar",
            "Directory not found: /home/abuild/rpmbuild/BUILDROOT/f/usr/share/foo",
        ):
            with self.subTest(message=message):
                log = (
                    "[ 127s] ----- building foo.spec (user abuild)\n"
                    "[ 150s] ERROR:root:optional backend not available\n"
                    f"[ 200s] error: {message}\n"
                )
                analysis = analyze_build_log(log)
                self.assertEqual(analysis.primary_diagnostic, f"error: {message}")
                self.assertEqual(analysis.family.value, "packaging")

    def test_debian_dependency_forms_and_modern_meson(self) -> None:
        """Check other sbuild Depends forms and the current Meson test summary line format."""
        for line in (
            " sbuild-build-depends-main-dummy:arm64 : Depends: libfoo-dev:arm64 (>= 1.2.3) but it is not going to be installed",
            " sbuild-build-depends-main-dummy:arm64 : Depends: libbar-dev but 1.0-1 is to be installed",
        ):
            with self.subTest(line=line):
                log = f"{line}\nE: Unable to correct problems.\nNot removing build depends: as requested\n"
                analysis = analyze_build_log(log)
                self.assertIn("Depends: lib", analysis.primary_diagnostic)
                self.assertEqual(analysis.family.value, "dependency")
        meson = (
            "[ 127s] ----- building glib2.spec (user abuild)\n"
            "[ 300s]  3/36 glib:glib+core / array            FAIL            0.21s   exit status 1\n"
            "[ 300s] Fail:               1\n"
        )
        analysis = analyze_build_log(meson)
        self.assertIn("glib+core / array", analysis.primary_diagnostic)
        self.assertEqual(analysis.family.value, "test")

    def test_compiler_oom_kill_is_environment(self) -> None:
        """Check that "Killed signal terminated program cc1plus" is an environment failure.

        An out-of-memory kill is not something a source edit can fix.
        """
        log = (
            "[ 127s] ----- building foo.spec (user abuild)\n"
            "[ 300s] g++: fatal error: Killed signal terminated program cc1plus\n"
        )
        self.assertEqual(analyze_build_log(log).family.value, "environment")

    def test_python_logging_records_do_not_lend_a_family(self) -> None:
        """Check that a Python "ERROR:root:" logging record does not set the failure family."""
        log = HOSTED_SETUP_NOISE + (
            "[ 127s] ----- building foo.spec (user abuild)\n"
            "[ 200s] ERROR:root:optional backend\n"
            "[ 200s] something unusual happened\n"
        )
        analysis = analyze_build_log(log)
        self.assertEqual(analysis.primary_diagnostic, "something unusual happened")
        self.assertEqual(analysis.family.value, "unknown")

    def test_signature_ignores_repetition_count(self) -> None:
        """Check that a line repeated three or five times gives the same error signature."""
        line = "[ 140s] /var/tmp/rpm-tmp.X: line 1: ./a: No such file or directory\n"
        head = "[ 127s] ----- building foo.spec (user abuild)\n"
        three = analyze_build_log(head + line * 3)
        five = analyze_build_log(head + line * 5)
        self.assertEqual(three.frontier.error_signature, five.frontier.error_signature)
        self.assertNotIn("repeated", three.frontier.error_signature)

    def test_hosted_hello_log_is_repaired(self) -> None:
        """Check that the controller fixes the hosted hello demo by editing buildbench-hello.spec.

        This runs the whole repair against the real hosted log shape, without a build gateway.
        """
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
