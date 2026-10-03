from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from .support import AGENT, make_workspace

from src.build.feedback import BuildBudget
from src.build.unavailable import UnavailableBuildFeedbackProvider
from src.errors import (
    BudgetExhaustedError,
    BuildFeedbackError,
    PatchValidationError,
    PathPolicyError,
    WorkspaceError,
)
from src.repair.executor import apply_candidate, rollback_transaction
from src.repair.patterns import load_rules
from src.repair.policy_critic import passes_policy_critic
from src.schemas import FrontierStage, PatchCandidate, PlannedEdit, RepairFamily
from src.workspace.path_policy import PathPolicy
from src.workspace.paths import WorkspacePaths


def candidate(edit: PlannedEdit) -> PatchCandidate:
    return PatchCandidate(
        "candidate-001", RepairFamily.CONDITIONAL_COMPILER_FLAG, "guard a causal flag", [], [edit],
        0.95, 0.05, FrontierStage.COMPILE, 0.8,
    )


class WorkspaceRepairTests(unittest.TestCase):
    def test_workspace_contract_requires_input_and_repo(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaises(WorkspaceError):
                WorkspacePaths.resolve(Path(name))

    def test_workspace_resolves_and_creates_probe_output(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            make_workspace(root)
            paths = WorkspacePaths.resolve(root)
            self.assertTrue(paths.probe_temp.is_dir())

    def test_workspace_rejects_non_directory_output(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "input").mkdir()
            (root / "work" / "repo").mkdir(parents=True)
            (root / "output").write_text("not a directory", encoding="utf-8")
            with self.assertRaises(WorkspaceError):
                WorkspacePaths.resolve(root)

    def test_path_policy_rejects_traversal_absolute_and_protected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            (repo / "file.txt").write_text("x", encoding="utf-8")
            policy = PathPolicy(repo)
            for unsafe in ("../input/case.json", "/workspace/input/case.json", ".git/config", "hidden-evaluator/data"):
                with self.subTest(path=unsafe), self.assertRaises(PathPolicyError):
                    policy.validate_relative(unsafe)
            with self.assertRaises(PathPolicyError):
                policy.validate_relative(".env.local", creating=True)

    def test_path_policy_honors_optional_case_allowed_paths(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            (repo / "src").mkdir()
            (repo / "docs").mkdir()
            (repo / "src" / "main.c").write_text("int x;\n", encoding="utf-8")
            (repo / "docs" / "notes.txt").write_text("notes\n", encoding="utf-8")
            policy = PathPolicy.from_metadata(repo, {"allowed_paths": ["src/**"]})
            self.assertEqual(policy.validate_relative("src/main.c"), repo / "src" / "main.c")
            with self.assertRaises(PathPolicyError):
                policy.validate_relative("docs/notes.txt")
            with self.assertRaises(PathPolicyError):
                PathPolicy.from_metadata(repo, {"allowed_paths": ["../outside"]})

    def test_atomic_edit_hash_guard_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            path = repo / "Makefile"
            path.write_text("CFLAGS += -msse4.2\n", encoding="utf-8")
            original = path.read_bytes()
            edit = PlannedEdit(
                "replace_lines", "Makefile", "CFLAGS += -O2",
                expected_text="CFLAGS += -msse4.2",
                expected_hash=hashlib.sha256(original).hexdigest(),
            )
            transaction = apply_candidate(candidate(edit), PathPolicy(repo))
            self.assertIn("-O2", path.read_text(encoding="utf-8"))
            rollback_transaction(transaction)
            self.assertEqual(path.read_bytes(), original)

    def test_binary_edit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            (repo / "data.bin").write_bytes(b"a\x00b")
            edit = PlannedEdit("replace_lines", "data.bin", "safe", expected_text="a")
            with self.assertRaises(PatchValidationError):
                apply_candidate(candidate(edit), PathPolicy(repo))

    def test_create_file_cannot_overwrite_existing_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            path = repo / "empty.conf"
            path.write_bytes(b"")
            edit = PlannedEdit("create_file", "empty.conf", "replacement\n")
            with self.assertRaises(PatchValidationError):
                apply_candidate(candidate(edit), PathPolicy(repo))
            self.assertEqual(path.read_bytes(), b"")

    def test_multiline_edit_preserves_crlf(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            path = repo / "build.txt"
            path.write_bytes(b"before\r\nold one\r\nold two\r\nafter\r\n")
            edit = PlannedEdit(
                "replace_lines",
                "build.txt",
                "new one\nnew two\n",
                expected_text="old one\nold two\n",
            )
            apply_candidate(candidate(edit), PathPolicy(repo))
            self.assertEqual(
                path.read_bytes(),
                b"before\r\nnew one\r\nnew two\r\nafter\r\n",
            )

    def test_policy_critic_rejects_test_deletion_and_evaluator_path(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            (repo / "tests").mkdir()
            (repo / "tests" / "test_ok.py").write_text("assert True\n", encoding="utf-8")
            bad = candidate(PlannedEdit("replace_lines", "tests/test_ok.py", "", expected_text="assert True\n"))
            self.assertFalse(passes_policy_critic(bad, PathPolicy(repo)))

    def test_generic_rulebook_loads(self) -> None:
        rules = load_rules(AGENT / "src" / "knowledge" / "portability_rules.json")
        self.assertGreaterEqual(len(rules), 5)
        self.assertNotIn("package", rules[0].id)

    def test_unavailable_build_feedback_fails_closed(self) -> None:
        provider = UnavailableBuildFeedbackProvider()
        self.assertFalse(provider.available)
        with self.assertRaises(BuildFeedbackError):
            provider.request(expected_information_value=1.0)

    def test_unknown_build_limit_cannot_be_invented(self) -> None:
        with self.assertRaises(BudgetExhaustedError):
            BuildBudget(None).authorize()


if __name__ == "__main__":
    unittest.main()
