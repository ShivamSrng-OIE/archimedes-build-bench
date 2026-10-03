from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.build.feedback import BuildFeedbackResult
from src.config import FeatureFlags
from src.errors import BuildFeedbackError
from src.forensics.log_parser import analyze_build_log
from src.reasoning.controller import RepairController
from src.schemas import FailureFrontier, FrontierStage
from src.workspace.paths import WorkspacePaths

from .support import make_workspace, runtime_config


class SequenceBuildFeedback:
    def __init__(self, results: list[BuildFeedbackResult]):
        self.results = list(results)
        self.request_limit = len(results)
        self.reason_unavailable = None

    @property
    def available(self) -> bool:
        return True

    def request(self, *, expected_information_value: float) -> BuildFeedbackResult:
        if expected_information_value <= 0:
            raise AssertionError("build experiments require positive information value")
        return self.results.pop(0)


class FailingBuildFeedback:
    available = True
    request_limit = 1
    reason_unavailable = None

    def request(self, *, expected_information_value: float) -> BuildFeedbackResult:
        if expected_information_value <= 0:
            raise AssertionError("build experiments require positive information value")
        raise BuildFeedbackError("simulated gateway failure")


def feedback(log: str, stage: FrontierStage, signature: str, request_id: str) -> BuildFeedbackResult:
    return BuildFeedbackResult(
        success=stage is FrontierStage.SUCCESS,
        raw_log=log,
        frontier=FailureFrontier(stage, signature, confidence=0.99),
        request_id=request_id,
    )


class RecoveryTests(unittest.TestCase):
    def _metadata(self) -> str:
        return json.dumps({
            "case_id": "iterative",
            "source_architecture": "x86_64",
            "target_architecture": "riscv64",
        })

    def test_progress_is_retained_and_second_repair_reaches_success(self) -> None:
        initial_log = "CMake Error: compiler does not support requested option -mavx2\n"
        atomic_log = "ld: main.o: undefined reference to `__atomic_compare_exchange_16'\n"
        provider = SequenceBuildFeedback([
            feedback(atomic_log, FrontierStage.LINK, "undefined __atomic_compare_exchange_16", "build-1"),
            feedback("build succeeded\n", FrontierStage.SUCCESS, "success", "build-2"),
        ])
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

    def test_explicit_success_overrides_an_uninformative_frontier(self) -> None:
        initial_log = "cc: error: unrecognized command-line option '-msse4.2'\n"
        initial_frontier = analyze_build_log(initial_log).frontier
        provider = SequenceBuildFeedback(
            [BuildFeedbackResult(True, "build succeeded\n", initial_frontier, "build-1")]
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

    def test_regression_rolls_back_the_current_attempt(self) -> None:
        initial_log = "cc: error: unrecognized command-line option '-msse4.2'\n"
        provider = SequenceBuildFeedback([
            feedback("configure: error: compiler unavailable\n", FrontierStage.CONFIGURE, "compiler unavailable", "build-1")
        ])
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
        initial_log = "cc: error: unrecognized command-line option '-msse4.2'\n"
        next_log = "src/other.c:1: error: unrelated portable declaration missing\n"
        provider = SequenceBuildFeedback([
            feedback(next_log, FrontierStage.COMPILE, "other declaration missing", "build-1")
        ])
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
            self.assertIn("ARCHIMEDES_TARGET_TRIPLE", makefile.read_text(encoding="utf-8"))
            self.assertEqual(outcome.result.modified_paths, ["Makefile"])
            self.assertEqual(outcome.metrics.rollback_count, 0)
            self.assertEqual(
                outcome.episode.attempts[0].build_result, "partial_progress"
            )

    def test_identical_failure_is_ineffective_and_rolls_back(self) -> None:
        initial_log = "cc: error: unrecognized command-line option '-msse4.2'\n"
        initial_frontier = analyze_build_log(initial_log).frontier
        provider = SequenceBuildFeedback([
            BuildFeedbackResult(False, initial_log, initial_frontier, "build-1")
        ])
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
            self.assertEqual(makefile.read_text(encoding="utf-8"), "CFLAGS += -msse4.2\n")
            self.assertEqual(outcome.episode.attempts[0].build_result, "ineffective")
            self.assertEqual(outcome.metrics.rollback_count, 1)

    def test_frontier_ablation_does_not_claim_stage_regression(self) -> None:
        initial_log = "cc: error: unrecognized command-line option '-msse4.2'\n"
        provider = SequenceBuildFeedback([
            feedback(
                "configure: error: compiler unavailable\n",
                FrontierStage.CONFIGURE,
                "compiler unavailable",
                "build-1",
            )
        ])
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
