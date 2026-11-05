"""Tests for dev/replay_development_cases.py, which runs the Agent over local dev Cases."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from dev.replay_development_cases import discover_cases, replay_case, write_summary


class DevelopmentReplayTests(unittest.TestCase):
    """Check Case discovery, a single replay and the replay summary."""

    def test_replay_preserves_complete_modified_worktree_and_summary(self) -> None:
        """Check that a replay keeps the edited worktree and counts the Case in the summary.

        A stub Agent edits hello.txt; the edit, its modified path and the summary
        counts must all survive so later tools can bundle and score the result.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "dataset"
            case = dataset / "cases" / "x86_64-to-aarch64" / "case-1"
            source = case / "source" / "input"
            logs = case / "logs"
            source.mkdir(parents=True)
            logs.mkdir()
            (source / "hello.txt").write_text("before\n", encoding="utf-8")
            (logs / "target-failure.log").write_text("compiler error\n", encoding="utf-8")
            (case / "case.json").write_text(
                json.dumps(
                    {
                        "case_id": "case-1",
                        "platform": {"direction": "x86_64-to-aarch64"},
                        "paths": {
                            "source_input": "source/input",
                            "target_failure_log": "logs/target-failure.log",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (dataset / "manifest.json").write_text("{}\n", encoding="utf-8")

            agent = root / "agent"
            package = agent / "src"
            package.mkdir(parents=True)
            (agent / "agent.yaml").write_text("version: test\n", encoding="utf-8")
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "main.py").write_text(
                """import json, os
from pathlib import Path
workspace = Path(os.environ["BB_WORKSPACE"])
(workspace / "work/repo/hello.txt").write_text("after\\n", encoding="utf-8")
(workspace / "output/agent-result.json").write_text(
    json.dumps({"schema_version": "0.1", "status": "completed", "modified_paths": ["hello.txt"]}),
    encoding="utf-8",
)
""",
                encoding="utf-8",
            )

            discovered = discover_cases(dataset)
            output = root / "output"
            result = replay_case(
                case_dir=discovered["case-1"],
                output_root=output,
                agent_dir=agent,
                python=Path(sys.executable),
                dpkg_source_root=root / "unused-dpkg-source",
                timeout_seconds=10,
            )
            write_summary(
                output,
                dataset_root=dataset,
                agent_dir=agent,
                python=Path(sys.executable),
                results=[result],
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.modified_paths, ("hello.txt",))
            self.assertEqual(result.actual_modified_paths, ("hello.txt",))
            self.assertEqual(
                (output / "cases/case-1/work/repo/hello.txt").read_text(encoding="utf-8"),
                "after\n",
            )
            summary = json.loads((output / "replay-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["case_count"], 1)
            self.assertEqual(summary["completed_count"], 1)
            self.assertEqual(summary["modified_case_count"], 1)


if __name__ == "__main__":
    unittest.main()
