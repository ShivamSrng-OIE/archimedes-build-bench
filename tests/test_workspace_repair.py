"""Tests for the workspace layout, the path policy and the atomic edit executor.

They check which paths may be edited, that edits apply in full or not at all and
can be rolled back byte for byte, and that missing build feedback fails closed.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

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
from src.repair.policy_critic import passes_policy_critic
from src.schemas import FrontierStage, PatchCandidate, PlannedEdit, RepairFamily
from src.workspace.path_policy import PathPolicy
from src.workspace.paths import WorkspacePaths

from .support import make_workspace


def candidate(edit: PlannedEdit) -> PatchCandidate:
    """Wrap one edit in a high-confidence compiler-flag candidate.

    Args:
        edit: The single edit the candidate should carry.

    Returns:
        A PatchCandidate with id "candidate-001", confidence 0.95 and risk 0.05
        that expects the build to reach the compile stage.
    """
    return PatchCandidate(
        "candidate-001",
        RepairFamily.CONDITIONAL_COMPILER_FLAG,
        "guard a causal flag",
        [],
        [edit],
        0.95,
        0.05,
        FrontierStage.COMPILE,
        0.8,
    )


class WorkspaceRepairTests(unittest.TestCase):
    """Check workspace resolution, path rules, edit application and fail-closed defaults."""

    def test_workspace_contract_requires_input_and_repo(self) -> None:
        """Check that an empty directory is refused because input/ and work/repo/ are missing."""
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaises(WorkspaceError):
                WorkspacePaths.resolve(Path(name))

    def test_workspace_resolves_and_creates_probe_output(self) -> None:
        """Check that a valid workspace resolves and its probe scratch directory is created."""
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            make_workspace(root)
            paths = WorkspacePaths.resolve(root)
            self.assertTrue(paths.probe_temp.is_dir())

    def test_workspace_rejects_non_directory_output(self) -> None:
        """Check that a plain file at output/ is refused instead of being written over."""
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "input").mkdir()
            (root / "work" / "repo").mkdir(parents=True)
            (root / "output").write_text("not a directory", encoding="utf-8")
            with self.assertRaises(WorkspaceError):
                WorkspacePaths.resolve(root)

    def test_path_policy_rejects_traversal_absolute_and_protected(self) -> None:
        """Check that the path policy refuses traversal, absolute, .git and evaluator paths.

        New files with line breaks, NUL bytes, 256-character names or a .env prefix are
        also refused, since any of these could escape the repository or leak secrets.
        """
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            (repo / "file.txt").write_text("x", encoding="utf-8")
            policy = PathPolicy(repo)
            for unsafe in (
                "../input/case.json",
                "/workspace/input/case.json",
                ".git/config",
                "hidden-evaluator/data",
            ):
                with self.subTest(path=unsafe), self.assertRaises(PathPolicyError):
                    policy.validate_relative(unsafe)
            for unsafe in ("line\nbreak.c", "nul\0byte.c", "a" * 256):
                with self.subTest(path=unsafe), self.assertRaises(PathPolicyError):
                    policy.validate_relative(unsafe, creating=True)
            with self.assertRaises(PathPolicyError):
                policy.validate_relative(".env.local", creating=True)

    def test_path_policy_honors_optional_case_allowed_paths(self) -> None:
        """Check that allowed_paths in the Case metadata limits edits to matching paths.

        An allowed_paths entry that points outside the repository is itself refused.
        """
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            (repo / "src").mkdir()
            (repo / "docs").mkdir()
            (repo / "src" / "main.c").write_text("int x;\n", encoding="utf-8")
            (repo / "docs" / "notes.txt").write_text("notes\n", encoding="utf-8")
            policy = PathPolicy.from_metadata(repo, {"allowed_paths": ["src/**"]})
            self.assertEqual(
                policy.validate_relative("src/main.c"), repo / "src" / "main.c"
            )
            with self.assertRaises(PathPolicyError):
                policy.validate_relative("docs/notes.txt")
            with self.assertRaises(PathPolicyError):
                PathPolicy.from_metadata(repo, {"allowed_paths": ["../outside"]})

    def test_official_case_wrapper_defaults_to_input_only(self) -> None:
        """Check that an official Build-Bench Case layout limits edits to the input/ package.

        The wrapper's own case.json must stay untouched.
        """
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            package = repo / "input"
            package.mkdir()
            (package / "Makefile").write_text("all:\n\ttrue\n", encoding="utf-8")
            (repo / "case.json").write_text(
                json.dumps(
                    {
                        "schema_version": "buildbench.case.v0.1",
                        "task": {"type": "cross_arch_package_build_repair"},
                    }
                ),
                encoding="utf-8",
            )

            policy = PathPolicy.from_metadata(repo, {})

            self.assertEqual(
                policy.validate_relative("input/Makefile"), package / "Makefile"
            )
            with self.assertRaises(PathPolicyError):
                policy.validate_relative("case.json")

    def test_nonofficial_repository_keeps_generic_root_paths(self) -> None:
        """Check that a plain repository without case.json may be edited from its root."""
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            (repo / "Makefile").write_text("all:\n\ttrue\n", encoding="utf-8")

            policy = PathPolicy.from_metadata(repo, {})

            self.assertEqual(policy.validate_relative("Makefile"), repo / "Makefile")

    @unittest.skipIf(
        os.name == "nt", "symbolic-link creation may require Windows elevation"
    )
    def test_path_policy_rejects_even_internal_symbolic_link_edits(self) -> None:
        """Check that editing through a symbolic link is refused, even one pointing inside the repo.

        Skipped on Windows, where creating links may need elevated rights.
        """
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            target = repo / "target.c"
            target.write_text("int value;\n", encoding="utf-8")
            (repo / "alias.c").symlink_to(target)

            with self.assertRaises(PathPolicyError):
                PathPolicy(repo).validate_relative("alias.c")

    def test_atomic_edit_hash_guard_and_rollback(self) -> None:
        """Check that an edit guarded by the file's SHA-256 applies and rolls back byte for byte."""
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            path = repo / "Makefile"
            path.write_text("CFLAGS += -msse4.2\n", encoding="utf-8")
            original = path.read_bytes()
            edit = PlannedEdit(
                "replace_lines",
                "Makefile",
                "CFLAGS += -O2",
                expected_text="CFLAGS += -msse4.2",
                expected_hash=hashlib.sha256(original).hexdigest(),
            )
            transaction = apply_candidate(candidate(edit), PathPolicy(repo))
            self.assertIn("-O2", path.read_text(encoding="utf-8"))
            rollback_transaction(transaction)
            self.assertEqual(path.read_bytes(), original)

    def test_binary_edit_is_rejected(self) -> None:
        """Check that a file containing a NUL byte is treated as binary and not edited."""
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            (repo / "data.bin").write_bytes(b"a\x00b")
            edit = PlannedEdit("replace_lines", "data.bin", "safe", expected_text="a")
            with self.assertRaises(PatchValidationError):
                apply_candidate(candidate(edit), PathPolicy(repo))

    def test_create_file_cannot_overwrite_existing_empty_file(self) -> None:
        """Check that create_file refuses a path that already exists, even as an empty file."""
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            path = repo / "empty.conf"
            path.write_bytes(b"")
            edit = PlannedEdit("create_file", "empty.conf", "replacement\n")
            with self.assertRaises(PatchValidationError):
                apply_candidate(candidate(edit), PathPolicy(repo))
            self.assertEqual(path.read_bytes(), b"")

    def test_multiline_edit_preserves_crlf(self) -> None:
        """Check that a multi-line edit written with LF keeps the file's CRLF line endings.

        Changing every line ending would turn a small fix into a whole-file diff.
        """
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
        """Check that the policy critic refuses a candidate that empties a test file.

        Deleting tests to make a build pass is not an acceptable repair.
        """
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            (repo / "tests").mkdir()
            (repo / "tests" / "test_ok.py").write_text(
                "assert True\n", encoding="utf-8"
            )
            bad = candidate(
                PlannedEdit(
                    "replace_lines",
                    "tests/test_ok.py",
                    "",
                    expected_text="assert True\n",
                )
            )
            self.assertFalse(passes_policy_critic(bad, PathPolicy(repo)))

    def test_unavailable_build_feedback_fails_closed(self) -> None:
        """Check that the unavailable build provider says so and raises if asked for a build."""
        provider = UnavailableBuildFeedbackProvider()
        self.assertFalse(provider.available)
        with self.assertRaises(BuildFeedbackError):
            provider.request(expected_information_value=1.0)

    def test_unknown_build_limit_cannot_be_invented(self) -> None:
        """Check that a build budget with no known limit refuses to authorize any build.

        The Agent must not assume it may run builds when the limit was never given.
        """
        with self.assertRaises(BudgetExhaustedError):
            BuildBudget(None).authorize()


if __name__ == "__main__":
    unittest.main()
