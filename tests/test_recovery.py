"""Tests for how the controller recovers across repair attempts.

Scripted build gateways return chosen results so the tests can check progress
retention, rollback on regression or no change, fail-closed handling of ambiguous
matches, and the behaviour when the build gateway fails or is not worth calling.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.build.feedback import BuildFeedbackResult
from src.build.unavailable import UnavailableBuildFeedbackProvider
from src.config import FeatureFlags
from src.errors import BuildFeedbackError
from src.forensics.log_parser import analyze_build_log
from src.reasoning.controller import RepairController
from src.schemas import (
    FailureFrontier,
    FrontierStage,
    PatchCandidate,
    PlannedEdit,
    RepairFamily,
)
from src.workspace.paths import WorkspacePaths

from .support import make_workspace, runtime_config


class SequenceBuildFeedback:
    """Build gateway stand-in that returns a fixed list of results in order.

    Attributes:
        results: The results still to be returned, oldest first.
        request_limit: How many builds may be requested, equal to the number of results.
        reason_unavailable: Always None, because this gateway is always available.
    """

    def __init__(self, results: list[BuildFeedbackResult]):
        """Store a copy of the results to hand out.

        Args:
            results: The build results to return, one per request.

        Returns:
            None.
        """
        self.results = list(results)
        self.request_limit = len(results)
        self.reason_unavailable = None

    @property
    def available(self) -> bool:
        """Report that build feedback can be requested.

        Returns:
            Always True.
        """
        return True

    def request(self, *, expected_information_value: float) -> BuildFeedbackResult:
        """Return the next scripted build result.

        Args:
            expected_information_value: The controller's estimate of what the
                build would teach it; must be above zero.

        Returns:
            The oldest result not yet returned.

        Raises:
            AssertionError: If the controller asks for a build it expects to
                learn nothing from.
            IndexError: If more builds are requested than results were given.
        """
        if expected_information_value <= 0:
            raise AssertionError("build experiments require positive information value")
        return self.results.pop(0)


class FailingBuildFeedback:
    """Build gateway stand-in whose single allowed request always fails.

    Attributes:
        available: Always True, so the controller tries to use it.
        request_limit: One build request.
        reason_unavailable: Always None.
    """

    available = True
    request_limit = 1
    reason_unavailable = None

    def request(self, *, expected_information_value: float) -> BuildFeedbackResult:
        """Fail the build request the way an unreachable gateway would.

        Args:
            expected_information_value: The controller's estimate of what the
                build would teach it; must be above zero.

        Returns:
            Never returns.

        Raises:
            AssertionError: If the expected information value is zero or less.
            BuildFeedbackError: On every valid call, to simulate a gateway failure.
        """
        if expected_information_value <= 0:
            raise AssertionError("build experiments require positive information value")
        raise BuildFeedbackError("simulated gateway failure")


class LowInformationController(RepairController):
    """Controller whose candidates promise too little information to justify a build."""

    def _candidate_for(self, **kwargs):
        """Return the normal candidate with its expected information gain lowered to 0.10.

        Args:
            **kwargs: The keyword arguments RepairController._candidate_for() takes,
                passed through unchanged.

        Returns:
            The parent's candidate with a low expected_information_gain_if_built,
            or None if the parent found no candidate.
        """
        candidate = super()._candidate_for(**kwargs)
        if candidate is not None:
            candidate.expected_information_gain_if_built = 0.10
        return candidate


class InvalidCandidateController(RepairController):
    """Controller that always proposes a file inside a directory that does not exist."""

    def _candidate_for(self, **kwargs):
        """Return a candidate that creates missing/parent/new.c, which must fail to apply.

        Args:
            **kwargs: The keyword arguments RepairController._candidate_for() takes;
                ignored.

        Returns:
            A high-confidence source-port candidate whose only edit breaks the
            executor's precondition that the parent directory exists.
        """
        del kwargs
        return PatchCandidate(
            id="candidate-invalid-parent",
            repair_family=RepairFamily.SOURCE_PORT,
            rationale="exercise atomic precondition failure",
            assumption_claim_ids=[],
            planned_edits=[
                PlannedEdit(
                    operation="create_file",
                    path="missing/parent/new.c",
                    replacement="int value;\n",
                    rationale="synthetic invalid parent",
                )
            ],
            confidence=0.95,
            risk=0.05,
            expected_frontier=FrontierStage.COMPILE,
            expected_information_gain_if_built=0.8,
        )


def feedback(
    log: str, stage: FrontierStage, signature: str, request_id: str
) -> BuildFeedbackResult:
    """Build a scripted gateway result for the given failure frontier.

    Args:
        log: The raw build log text the gateway would return.
        stage: The frontier stage the build reached; SUCCESS marks the build successful.
        signature: The error signature for the frontier.
        request_id: The identifier of the build request.

    Returns:
        A BuildFeedbackResult with a frontier confidence of 0.99.
    """
    return BuildFeedbackResult(
        success=stage is FrontierStage.SUCCESS,
        raw_log=log,
        frontier=FailureFrontier(stage, signature, confidence=0.99),
        request_id=request_id,
    )


class RecoveryTests(unittest.TestCase):
    """Check attempt-by-attempt decisions: keep, roll back, skip or fail closed."""

    def _metadata(self) -> str:
        """Return Case metadata for an x86_64 to riscv64 port.

        Returns:
            The metadata as a JSON string with case_id "iterative".
        """
        return json.dumps(
            {
                "case_id": "iterative",
                "source_architecture": "x86_64",
                "target_architecture": "riscv64",
            }
        )

    def test_progress_is_retained_and_second_repair_reaches_success(self) -> None:
        """Check that a kept first repair stays while a second repair fixes the next failure.

        The -mavx2 fix moves the failure to a link error, the -latomic fix then reaches
        success, and both edits must be in the final worktree with no rollback.
        """
        initial_log = "CMake Error: compiler does not support requested option -mavx2\n"
        atomic_log = (
            "ld: main.o: undefined reference to `__atomic_compare_exchange_16'\n"
        )
        provider = SequenceBuildFeedback(
            [
                feedback(
                    atomic_log,
                    FrontierStage.LINK,
                    "undefined __atomic_compare_exchange_16",
                    "build-1",
                ),
                feedback(
                    "build succeeded\n", FrontierStage.SUCCESS, "success", "build-2"
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, _ = make_workspace(root, metadata=self._metadata(), log=initial_log)
            cmake = repo / "CMakeLists.txt"
            cmake.write_text(
                'set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -mavx2")\nadd_executable(demo main.c)\n',
                encoding="utf-8",
            )
            makefile = repo / "Makefile"
            makefile.write_text("demo: main.c\n\t$(CC) -o $@ $<\n", encoding="utf-8")
            (repo / "main.c").write_text(
                "int main(void) { __int128 x = 0; return __atomic_compare_exchange_n(&x, &x, 1, 0, 5, 5); }\n",
                encoding="utf-8",
            )
            outcome = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=provider,
            ).run()
            self.assertTrue(outcome.metrics.success)
            self.assertEqual(outcome.metrics.attempts, 2)
            self.assertEqual(outcome.metrics.build_requests, 2)
            self.assertEqual(outcome.metrics.rollback_count, 0)
            self.assertEqual(
                outcome.result.modified_paths, ["CMakeLists.txt", "Makefile"]
            )
            self.assertIn("check_c_compiler_flag", cmake.read_text(encoding="utf-8"))
            self.assertIn("-latomic", makefile.read_text(encoding="utf-8"))
            self.assertEqual(outcome.episode.attempts[0].decision.value, "keep")
            self.assertEqual(outcome.episode.attempts[1].decision.value, "success")

    def test_cmake_feature_repair_fails_closed_when_repository_match_is_ambiguous(
        self,
    ) -> None:
        """Check that the CMake flag rule edits nothing when two CMakeLists.txt files match.

        Guessing between equal matches could change the wrong file, so the rule fails closed.
        """
        initial_log = "CMake Error: compiler does not support requested option -mavx2\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, _ = make_workspace(root, metadata=self._metadata(), log=initial_log)
            for directory in (repo / "first", repo / "second"):
                directory.mkdir()
                (directory / "CMakeLists.txt").write_text(
                    'set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -mavx2")\n',
                    encoding="utf-8",
                )

            outcome = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
            ).run()

            self.assertEqual(outcome.result.modified_paths, [])

    def test_atomic_link_repair_fails_closed_when_link_recipe_is_ambiguous(
        self,
    ) -> None:
        """Check that the -latomic rule edits nothing when two Makefiles could link the program."""
        atomic_log = (
            "ld: main.o: undefined reference to `__atomic_compare_exchange_16'\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, _ = make_workspace(root, metadata=self._metadata(), log=atomic_log)
            (repo / "main.c").write_text(
                "int f(void) { return __atomic_compare_exchange_n(0, 0, 0, 0, 0, 0); }\n",
                encoding="utf-8",
            )
            for directory in (repo / "first", repo / "second"):
                directory.mkdir()
                (directory / "Makefile").write_text(
                    "all: main.c\n\t$(CC) -o demo main.c\n", encoding="utf-8"
                )

            outcome = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
            ).run()

            self.assertEqual(outcome.result.modified_paths, [])

    def test_explicit_success_overrides_an_uninformative_frontier(self) -> None:
        """Check that a successful build counts as success even if the frontier looks unchanged.

        The success flag from the build gateway is the authority, not the parsed log.
        """
        initial_log = "cc: error: unrecognized command-line option '-msse4.2'\n"
        initial_frontier = analyze_build_log(initial_log).frontier
        provider = SequenceBuildFeedback(
            [
                BuildFeedbackResult(
                    True, "build succeeded\n", initial_frontier, "build-1"
                )
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, _ = make_workspace(root, metadata=self._metadata(), log=initial_log)
            makefile = repo / "Makefile"
            makefile.write_text("CFLAGS += -msse4.2\n", encoding="utf-8")
            outcome = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=provider,
            ).run()
        self.assertTrue(outcome.metrics.success)
        self.assertEqual(outcome.metrics.rollback_count, 0)
        self.assertEqual(outcome.episode.attempts[0].build_result, "success")

    def test_gateway_failure_preserves_the_evidence_supported_repair(self) -> None:
        """Check that a build gateway error keeps the applied fix and reports a safe failure.

        The edit is backed by the log evidence, so losing the build check alone is no
        reason to roll it back.
        """
        initial_log = "cc: error: unrecognized command-line option '-msse4.2'\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, _ = make_workspace(root, metadata=self._metadata(), log=initial_log)
            makefile = repo / "Makefile"
            makefile.write_text("CFLAGS += -msse4.2\n", encoding="utf-8")
            outcome = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=FailingBuildFeedback(),
            ).run()
            updated = makefile.read_text(encoding="utf-8")
        self.assertIn("ARCHIMEDES_TARGET_TRIPLE", updated)
        self.assertEqual(outcome.result.modified_paths, ["Makefile"])
        self.assertEqual(outcome.metrics.rollback_count, 0)
        self.assertEqual(outcome.episode.attempts[0].build_result, "unavailable")
        self.assertIn("failed safely", outcome.result.message)

    def test_low_information_build_is_skipped_without_misreporting_rollback(
        self,
    ) -> None:
        """Check that a build worth too little information is skipped and not called a rollback.

        The fix stays, no build request is made, and the message names the reason.
        """
        initial_log = "cc: error: unrecognized command-line option '-msse4.2'\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, _ = make_workspace(root, metadata=self._metadata(), log=initial_log)
            makefile = repo / "Makefile"
            makefile.write_text("CFLAGS += -msse4.2\n", encoding="utf-8")

            outcome = LowInformationController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=FailingBuildFeedback(),
            ).run()

            self.assertEqual(outcome.result.modified_paths, ["Makefile"])
            self.assertEqual(outcome.metrics.build_requests, 0)
            self.assertIn("expected information value", outcome.result.message)
            self.assertNotIn("rolled back", outcome.result.message)

    def test_atomic_candidate_failure_finishes_without_partial_edit_or_agent_error(
        self,
    ) -> None:
        """Check that a candidate whose edit cannot be applied leaves no files and still completes.

        The failure is counted as an invalid patch rather than crashing the Agent.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, _ = make_workspace(
                root,
                metadata=self._metadata(),
                log="src/value.c:1: error: invalid value\n",
            )

            outcome = InvalidCandidateController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
            ).run()

            self.assertEqual(outcome.result.status, "completed")
            self.assertEqual(outcome.result.modified_paths, [])
            self.assertEqual(outcome.metrics.invalid_patch_count, 1)
            self.assertFalse((repo / "missing").exists())
            self.assertIn("atomic edit precondition", outcome.result.message)

    def test_regression_rolls_back_the_current_attempt(self) -> None:
        """Check that a build that falls back from compile to configure rolls the attempt back.

        The Makefile must return to its original text and the hypothesis is marked falsified.
        """
        initial_log = "cc: error: unrecognized command-line option '-msse4.2'\n"
        provider = SequenceBuildFeedback(
            [
                feedback(
                    "configure: error: compiler unavailable\n",
                    FrontierStage.CONFIGURE,
                    "compiler unavailable",
                    "build-1",
                )
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, _ = make_workspace(root, metadata=self._metadata(), log=initial_log)
            makefile = repo / "Makefile"
            original = "CFLAGS += -msse4.2\n"
            makefile.write_text(original, encoding="utf-8")
            outcome = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=provider,
            ).run()
            self.assertEqual(makefile.read_text(encoding="utf-8"), original)
            self.assertEqual(outcome.result.modified_paths, [])
            self.assertEqual(outcome.metrics.rollback_count, 1)
            self.assertEqual(outcome.metrics.hypotheses_falsified, 1)
            self.assertEqual(outcome.episode.attempts[0].decision.value, "rollback")
            self.assertTrue(outcome.episode.contradictions)

    def test_same_frontier_new_signature_preserves_partial_progress(self) -> None:
        """Check that a new error at the same compile stage is kept as partial progress."""
        initial_log = "cc: error: unrecognized command-line option '-msse4.2'\n"
        next_log = "src/other.c:1: error: unrelated portable declaration missing\n"
        provider = SequenceBuildFeedback(
            [
                feedback(
                    next_log,
                    FrontierStage.COMPILE,
                    "other declaration missing",
                    "build-1",
                )
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, _ = make_workspace(root, metadata=self._metadata(), log=initial_log)
            makefile = repo / "Makefile"
            makefile.write_text("CFLAGS += -msse4.2\n", encoding="utf-8")
            outcome = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=provider,
            ).run()
            self.assertIn(
                "ARCHIMEDES_TARGET_TRIPLE", makefile.read_text(encoding="utf-8")
            )
            self.assertEqual(outcome.result.modified_paths, ["Makefile"])
            self.assertEqual(outcome.metrics.rollback_count, 0)
            self.assertEqual(
                outcome.episode.attempts[0].build_result, "partial_progress"
            )

    def test_identical_failure_is_ineffective_and_rolls_back(self) -> None:
        """Check that an unchanged error signature marks the attempt ineffective and undoes it."""
        initial_log = "cc: error: unrecognized command-line option '-msse4.2'\n"
        initial_frontier = analyze_build_log(initial_log).frontier
        provider = SequenceBuildFeedback(
            [BuildFeedbackResult(False, initial_log, initial_frontier, "build-1")]
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, _ = make_workspace(root, metadata=self._metadata(), log=initial_log)
            makefile = repo / "Makefile"
            makefile.write_text("CFLAGS += -msse4.2\n", encoding="utf-8")
            outcome = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=provider,
            ).run()
            self.assertEqual(
                makefile.read_text(encoding="utf-8"), "CFLAGS += -msse4.2\n"
            )
            self.assertEqual(outcome.episode.attempts[0].build_result, "ineffective")
            self.assertEqual(outcome.metrics.rollback_count, 1)

    def test_frontier_ablation_does_not_claim_stage_regression(self) -> None:
        """Check that with failure_frontier disabled a stage change is not treated as a regression.

        Frontier positions are reported as -1 and the attempt is kept as partial progress.
        """
        initial_log = "cc: error: unrecognized command-line option '-msse4.2'\n"
        provider = SequenceBuildFeedback(
            [
                feedback(
                    "configure: error: compiler unavailable\n",
                    FrontierStage.CONFIGURE,
                    "compiler unavailable",
                    "build-1",
                )
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, _ = make_workspace(root, metadata=self._metadata(), log=initial_log)
            makefile = repo / "Makefile"
            makefile.write_text("CFLAGS += -msse4.2\n", encoding="utf-8")
            outcome = RepairController(
                config=runtime_config(
                    root, features=FeatureFlags(failure_frontier=False)
                ),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=provider,
            ).run()
            self.assertEqual(outcome.metrics.initial_frontier, -1)
            self.assertEqual(outcome.metrics.best_frontier, -1)
            self.assertEqual(
                outcome.episode.attempts[0].build_result, "partial_progress"
            )
            self.assertEqual(outcome.metrics.rollback_count, 0)


if __name__ == "__main__":
    unittest.main()
