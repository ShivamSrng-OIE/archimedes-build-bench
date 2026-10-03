from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.constants import MAX_LOG_CHARACTERS
from src.errors import CaseFormatError
from src.schemas import Architecture
from src.workspace.case_loader import load_case
from src.workspace.paths import WorkspacePaths


class CaseLoaderTests(unittest.TestCase):
    def _workspace(self, root: Path) -> WorkspacePaths:
        (root / "input").mkdir()
        (root / "work" / "repo").mkdir(parents=True)
        return WorkspacePaths.resolve(root)

    def test_loads_released_development_case_v01_shape(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            paths = self._workspace(root)
            metadata = {
                "schema_version": "buildbench.case.v0.1",
                "case_id": "launchpad-example-amd64-arm64-demo-123456789abc",
                "platform": {
                    "distribution": "ubuntu",
                    "source_arch": "x86_64",
                    "target_arch": "aarch64",
                    "direction": "x86_64-to-aarch64",
                },
                "task": {
                    "type": "cross_arch_package_build_repair",
                    "initial_failure_evidence": "logs/target-failure.log",
                },
                "paths": {"target_failure_log": "logs/target-failure.log"},
            }
            (paths.input / "case.json").write_text(
                json.dumps(metadata), encoding="utf-8"
            )
            (paths.input / "logs").mkdir()
            (paths.input / "logs" / "target-failure.log").write_text(
                "cc: error: unrecognized command-line option '-msse4.2'\n",
                encoding="utf-8",
            )

            case = load_case(paths)

            self.assertEqual(case.case_id, metadata["case_id"])
            self.assertEqual(case.source_architecture.canonical, Architecture.X86_64)
            self.assertEqual(case.target_architecture.canonical, Architecture.AARCH64)
            self.assertIn("unrecognized command-line option", case.build_log)

    def test_released_log_path_cannot_escape_input(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            paths = self._workspace(root)
            outside = root / "outside.log"
            outside.write_text("secret external evidence\n", encoding="utf-8")
            (paths.input / "case.json").write_text(
                json.dumps(
                    {
                        "case_id": "path-safety",
                        "platform": {
                            "source_arch": "aarch64",
                            "target_arch": "x86_64",
                        },
                        "paths": {"target_failure_log": "../outside.log"},
                    }
                ),
                encoding="utf-8",
            )

            case = load_case(paths)

            self.assertEqual(case.build_log, "")

    def test_oversized_log_preserves_terminal_failure_without_loading_middle(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            paths = self._workspace(root)
            (paths.input / "case.json").write_text(
                json.dumps({"paths": {"target_failure_log": "target-failure.log"}}),
                encoding="utf-8",
            )
            (paths.input / "target-failure.log").write_text(
                "setup evidence\n"
                + ("uninteresting output\n" * (MAX_LOG_CHARACTERS // 10))
                + "src/port.c:77: error: unsupported target architecture\n",
                encoding="utf-8",
            )

            case = load_case(paths)

            self.assertIn("setup evidence", case.build_log)
            self.assertIn("middle of oversized build log omitted", case.build_log)
            self.assertIn("unsupported target architecture", case.build_log)
            self.assertLessEqual(len(case.build_log), MAX_LOG_CHARACTERS + 100)

    def test_oversized_metadata_is_rejected_before_json_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            paths = self._workspace(root)
            (paths.input / "case.json").write_bytes(b" " * 1_000_001)
            with self.assertRaises(CaseFormatError):
                load_case(paths)

    def test_recovers_released_identity_when_runner_task_omits_it(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            paths = self._workspace(root)
            runner_metadata = {
                "schema_version": "0.1",
                "case_ordinal": 17,
                "worktree": "/workspace/work/repo",
                "initial_build_log": "/workspace/input/initial-build.log",
            }
            released_metadata = {
                "schema_version": "buildbench.case.v0.1",
                "case_id": "launchpad-fallback-demo-123456789abc",
                "platform": {
                    "source_arch": "aarch64",
                    "target_arch": "x86_64",
                },
                "task": {"type": "cross_arch_package_build_repair"},
            }
            (paths.input / "task.json").write_text(
                json.dumps(runner_metadata), encoding="utf-8"
            )
            (paths.repo / "case.json").write_text(
                json.dumps(released_metadata), encoding="utf-8"
            )

            case = load_case(paths)

            self.assertEqual(case.case_id, released_metadata["case_id"])
            self.assertEqual(case.source_architecture.canonical, Architecture.AARCH64)
            self.assertEqual(case.target_architecture.canonical, Architecture.X86_64)
            self.assertEqual(case.metadata, runner_metadata)

    def test_ignores_unrecognized_worktree_case_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            paths = self._workspace(root)
            (paths.input / "task.json").write_text("{}", encoding="utf-8")
            (paths.repo / "case.json").write_text(
                json.dumps(
                    {
                        "schema_version": "untrusted.case.v9",
                        "case_id": "not-authoritative",
                        "platform": {
                            "source_arch": "aarch64",
                            "target_arch": "x86_64",
                        },
                        "task": {"type": "cross_arch_package_build_repair"},
                    }
                ),
                encoding="utf-8",
            )

            case = load_case(paths)

            self.assertEqual(case.case_id, root.name)
            self.assertEqual(case.source_architecture.canonical, Architecture.UNKNOWN)
            self.assertEqual(case.target_architecture.canonical, Architecture.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
