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
