from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from dev.package_worktree_bundle import create_bundle


class WorktreeBundleTests(unittest.TestCase):
    def test_bundle_is_deterministic_and_contains_only_final_worktrees(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replay = root / "replay"
            case_id = "case-a"
            repo = replay / "cases" / case_id / "work" / "repo"
            repo.mkdir(parents=True)
            (repo / "README").write_text("hello\n", encoding="utf-8")
            (repo / ".gitignore").write_text("build/\n", encoding="utf-8")
            (repo / ".gitmodules").write_text("[submodule]\n", encoding="utf-8")
            (repo / ".github" / "workflows").mkdir(parents=True)
            (repo / ".github" / "workflows" / "ci.yml").write_text(
                "name: CI\n", encoding="utf-8"
            )
            (repo / ".pc" / "applied-patches").parent.mkdir(parents=True)
            (repo / ".pc" / "applied-patches").write_text("fix.patch\n", encoding="utf-8")
            (repo / ".svn").mkdir()
            (repo / ".svn" / "entries").write_text("metadata\n", encoding="utf-8")
            (repo / "install-artifact-from-github").mkdir()
            (repo / "install-artifact-from-github" / "index.js").write_text(
                "module.exports = {};\n", encoding="utf-8"
            )
            (replay / "replay-summary.json").write_text(
                json.dumps({"cases": [{"case_id": case_id, "status": "completed"}]}),
                encoding="utf-8",
            )
            first = root / "first.zip"
            second = root / "second.zip"

            first_result = create_bundle(replay, first)
            second_result = create_bundle(replay, second)

            self.assertEqual(first_result["sha256"], second_result["sha256"])
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(
                    archive.namelist(),
                    [
                        "manifest.json",
                        "cases/case-a/worktree/input/README",
                        "cases/case-a/worktree/input/install-artifact-from-github/index.js",
                    ],
                )
                manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(
                set(manifest),
                {"schema_version", "suite_id", "cases"},
            )
            self.assertEqual(manifest["schema_version"], "0.1")
            self.assertEqual(manifest["suite_id"], "development-recommended-v1")
            self.assertEqual(
                manifest["cases"],
                [{"case_id": "case-a", "worktree": "cases/case-a/worktree"}],
            )

    def test_rejects_incomplete_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "replay-summary.json").write_text(
                json.dumps({"cases": [{"case_id": "case-a", "status": "agent_error"}]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "did not complete"):
                create_bundle(root, root / "bundle.zip")


if __name__ == "__main__":
    unittest.main()
