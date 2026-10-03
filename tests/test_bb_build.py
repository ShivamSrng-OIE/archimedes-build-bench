from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.build.bb_build import BbBuildFeedbackProvider
from src.errors import BuildFeedbackError


class BbBuildFeedbackProviderTests(unittest.TestCase):
    def _workspace(self, root: Path, feedback: object) -> tuple[Path, Path]:
        input_dir = root / "input"
        repo = root / "work" / "repo"
        input_dir.mkdir(parents=True)
        repo.mkdir(parents=True)
        (input_dir / "task.json").write_text(
            json.dumps(
                {
                    "schema_version": "0.1",
                    "build_feedback": feedback,
                }
            ),
            encoding="utf-8",
        )
        return input_dir, repo

    def test_discovery_requires_exact_declared_capability(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_dir, repo = self._workspace(
                Path(temporary),
                {"command": "bb-build", "attempt_limit": 2},
            )
            provider = BbBuildFeedbackProvider.discover(
                executable="/opt/buildbench/bin/bb-build",
                input_dir=input_dir,
                repo_root=repo,
            )
        self.assertTrue(provider.available)
        self.assertEqual(provider.request_limit, 2)
        self.assertIsNone(provider.reason_unavailable)

    def test_discovery_fails_closed_on_invalid_metadata(self) -> None:
        invalid = (
            None,
            {"command": "other", "attempt_limit": 1},
            {"command": "bb-build", "attempt_limit": True},
            {"command": "bb-build", "attempt_limit": 0},
            {"command": "bb-build", "attempt_limit": 101},
        )
        for feedback in invalid:
            with self.subTest(feedback=feedback), tempfile.TemporaryDirectory() as temporary:
                input_dir, repo = self._workspace(Path(temporary), feedback)
                provider = BbBuildFeedbackProvider.discover(
                    executable="bb-build", input_dir=input_dir, repo_root=repo
                )
                self.assertFalse(provider.available)
                self.assertEqual(provider.request_limit, 0)
                self.assertTrue(provider.reason_unavailable)

    def test_discovery_rejects_malformed_missing_and_oversized_task_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_dir, repo = self._workspace(
                root, {"command": "bb-build", "attempt_limit": 1}
            )
            task = input_dir / "task.json"
            malformed_payloads = (
                b"[]",
                b'{"schema_version":"0.2","build_feedback":{"command":"bb-build","attempt_limit":1}}',
                b'{"schema_version":"0.1","build_feedback":{"command":"bb-build","attempt_limit":NaN}}',
            )
            for payload in malformed_payloads:
                with self.subTest(payload=payload):
                    task.write_bytes(payload)
                    provider = BbBuildFeedbackProvider.discover(
                        executable="bb-build", input_dir=input_dir, repo_root=repo
                    )
                    self.assertFalse(provider.available)
            task.write_bytes(b" " * 1_000_001)
            oversized = BbBuildFeedbackProvider.discover(
                executable="bb-build", input_dir=input_dir, repo_root=repo
            )
            self.assertFalse(oversized.available)
            task.unlink()
            missing = BbBuildFeedbackProvider.discover(
                executable="bb-build", input_dir=input_dir, repo_root=repo
            )
            self.assertFalse(missing.available)

    def test_timeout_is_bounded_and_invalid_values_use_the_default(self) -> None:
        expectations = (("0", 6), ("99999", 3605), ("invalid", 905))
        for raw, expected in expectations:
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as temporary:
                input_dir, repo = self._workspace(
                    Path(temporary), {"command": "bb-build", "attempt_limit": 1}
                )
                with patch.dict(
                    os.environ, {"BB_BUILD_GATEWAY_TIMEOUT_SECONDS": raw}
                ):
                    provider = BbBuildFeedbackProvider.discover(
                        executable="bb-build", input_dir=input_dir, repo_root=repo
                    )
                self.assertEqual(provider.timeout_seconds, expected)

    @unittest.skipIf(os.name == "nt", "symbolic-link creation may require Windows elevation")
    def test_discovery_rejects_symlinked_task_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_dir = root / "input"
            repo = root / "work" / "repo"
            input_dir.mkdir(parents=True)
            repo.mkdir(parents=True)
            target = root / "task-target.json"
            target.write_text(
                '{"schema_version":"0.1","build_feedback":{"command":"bb-build","attempt_limit":1}}',
                encoding="utf-8",
            )
            (input_dir / "task.json").symlink_to(target)
            provider = BbBuildFeedbackProvider.discover(
                executable="bb-build", input_dir=input_dir, repo_root=repo
            )
        self.assertFalse(provider.available)

    def test_success_response_is_strictly_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_dir, repo = self._workspace(
                Path(temporary),
                {"command": "bb-build", "attempt_limit": 1},
            )
            provider = BbBuildFeedbackProvider.discover(
                executable="/trusted/bb-build",
                input_dir=input_dir,
                repo_root=repo,
            )
            payload = {
                "schema_version": "0.1",
                "ok": True,
                "attempt": 1,
                "attempt_limit": 1,
                "status": "succeeded",
                "message": "build succeeded",
                "log_excerpt": "",
            }
            completed = subprocess.CompletedProcess(
                ["/trusted/bb-build"], 0, json.dumps(payload).encode(), b""
            )
            with patch(
                "src.build.bb_build.subprocess.run", return_value=completed
            ) as run:
                result = provider.request(expected_information_value=0.8)
        self.assertTrue(result.success)
        self.assertEqual(result.request_id, "bb-build-attempt-1")
        self.assertEqual(result.raw_log, "build succeeded")
        self.assertEqual(run.call_args.args[0], ["/trusted/bb-build"])
        self.assertEqual(run.call_args.kwargs["cwd"], repo)
        self.assertFalse(run.call_args.kwargs["check"])

    def test_unavailable_and_failed_invocations_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_dir, repo = self._workspace(root, None)
            unavailable = BbBuildFeedbackProvider.discover(
                executable="bb-build", input_dir=input_dir, repo_root=repo
            )
            with self.assertRaises(BuildFeedbackError):
                unavailable.request(expected_information_value=0.8)

            (input_dir / "task.json").write_text(
                '{"schema_version":"0.1","build_feedback":{"command":"bb-build","attempt_limit":1}}',
                encoding="utf-8",
            )
            available = BbBuildFeedbackProvider.discover(
                executable="bb-build", input_dir=input_dir, repo_root=repo
            )
            failures = (OSError("missing"), subprocess.TimeoutExpired("bb-build", 1))
            for failure in failures:
                with self.subTest(failure=type(failure).__name__), patch(
                    "src.build.bb_build.subprocess.run", side_effect=failure
                ), self.assertRaises(BuildFeedbackError):
                    available.request(expected_information_value=0.8)

            oversized = subprocess.CompletedProcess(
                ["bb-build"], 0, b"{" + b" " * (64 * 1024), b""
            )
            with patch(
                "src.build.bb_build.subprocess.run", return_value=oversized
            ), self.assertRaises(BuildFeedbackError):
                available.request(expected_information_value=0.8)

    def test_invalid_or_rejected_response_fails_closed(self) -> None:
        responses = (
            subprocess.CompletedProcess(["bb-build"], 0, b"not-json", b""),
            subprocess.CompletedProcess(
                ["bb-build"],
                1,
                b'{"schema_version":"0.1","ok":false,"error":"attempt_limit_exceeded"}',
                b"",
            ),
            subprocess.CompletedProcess(
                ["bb-build"],
                0,
                b'{"schema_version":"0.1","ok":true,"attempt":true,"attempt_limit":1,"status":"succeeded","message":"","log_excerpt":""}',
                b"",
            ),
            subprocess.CompletedProcess(
                ["bb-build"],
                1,
                b'{"schema_version":"0.1","ok":true,"attempt":1,"attempt_limit":1,"status":"succeeded","message":"","log_excerpt":""}',
                b"",
            ),
            subprocess.CompletedProcess(
                ["bb-build"],
                0,
                b'{"schema_version":"0.1","ok":true,"attempt":NaN,"attempt_limit":1,"status":"failed","message":"","log_excerpt":""}',
                b"",
            ),
            subprocess.CompletedProcess(
                ["bb-build"],
                2,
                b'{"schema_version":"0.1","ok":true}',
                b"",
            ),
            subprocess.CompletedProcess(
                ["bb-build"],
                0,
                b'{"schema_version":"0.2","ok":true}',
                b"",
            ),
            subprocess.CompletedProcess(
                ["bb-build"],
                1,
                b'{"schema_version":"0.1","ok":false,"error":123}',
                b"",
            ),
        )
        for completed in responses:
            with self.subTest(stdout=completed.stdout), tempfile.TemporaryDirectory() as temporary:
                input_dir, repo = self._workspace(
                    Path(temporary),
                    {"command": "bb-build", "attempt_limit": 1},
                )
                provider = BbBuildFeedbackProvider.discover(
                    executable="bb-build", input_dir=input_dir, repo_root=repo
                )
                with patch(
                    "src.build.bb_build.subprocess.run", return_value=completed
                ), self.assertRaises(BuildFeedbackError):
                    provider.request(expected_information_value=0.8)


if __name__ == "__main__":
    unittest.main()
