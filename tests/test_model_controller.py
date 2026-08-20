from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.build.feedback import BuildFeedbackResult
from src.build.unavailable import UnavailableBuildFeedbackProvider
from src.context.token_budget import UsageBudget
from src.models.base import ModelTask
from src.models.mock import MockModelClient
from src.models.router import ModelRouter
from src.reasoning.controller import RepairController
from src.schemas import FailureFrontier, FrontierStage
from src.workspace.paths import WorkspacePaths

from .support import make_workspace, runtime_config


def analysis_response() -> dict[str, object]:
    return {
        "primary_failure": "invalid target-independent return value",
        "failure_stage": "compile",
        "claims": [{"statement": "the diagnostic identifies src/value.c"}],
        "hypotheses": [],
        "recommended_files": ["src/value.c"],
    }


def router_for(plan: dict[str, object]) -> ModelRouter:
    client = MockModelClient({
        ModelTask.FAILURE_ANALYSIS: analysis_response(),
        ModelTask.PATCH_PLANNING: plan,
    })
    return ModelRouter(client, UsageBudget(5, 2, 20_000), 2.0)


class SuccessfulBuildFeedback:
    available = True
    reason_unavailable = None
    request_limit = 1

    def request(self, *, expected_information_value: float) -> BuildFeedbackResult:
        if expected_information_value <= 0:
            raise AssertionError("build experiment needs information value")
        return BuildFeedbackResult(
            True,
            "build succeeded\n",
            FailureFrontier(FrontierStage.SUCCESS, "success", confidence=0.99),
            "model-build-1",
        )


class RegressionThenSuccessBuildFeedback:
    available = True
    reason_unavailable = None
    request_limit = 2

    def __init__(self) -> None:
        self.calls = 0

    def request(self, *, expected_information_value: float) -> BuildFeedbackResult:
        if expected_information_value <= 0:
            raise AssertionError("build experiment needs information value")
        self.calls += 1
        if self.calls == 1:
            return BuildFeedbackResult(
                False,
                "configure: error: compiler unavailable\n",
                FailureFrontier(
                    FrontierStage.CONFIGURE, "compiler unavailable", confidence=0.99
                ),
                "build-regression",
            )
        return BuildFeedbackResult(
            True,
            "build succeeded\n",
            FailureFrontier(FrontierStage.SUCCESS, "success", confidence=0.99),
            "build-success",
        )


class ModelControllerTests(unittest.TestCase):
    def _workspace(self, root: Path) -> Path:
        metadata = json.dumps({
            "case_id": "model-plan",
            "source_architecture": "x86_64",
            "target_architecture": "aarch64",
        })
        repo, _ = make_workspace(
            root,
            metadata=metadata,
            log="src/value.c:1: error: invalid target-independent return value\n",
        )
        source = repo / "src" / "value.c"
        source.parent.mkdir()
        source.write_text("int value(void) { return -1; }\n", encoding="utf-8")
        return source

    def test_mock_model_plan_passes_guarded_executor(self) -> None:
        plan = {
            "repair_family": "source_port",
            "rationale": "replace the diagnostic-linked sentinel with the portable value",
            "assumptions": ["the sentinel is not part of the public ABI"],
            "edits": [{
                "operation": "replace_lines",
                "path": "src/value.c",
                "expected_text": "int value(void) { return -1; }",
                "replacement": "int value(void) { return 0; }",
                "rationale": "use the target-independent value",
            }],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._workspace(root)
            router = router_for(plan)
            outcome = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=SuccessfulBuildFeedback(),
                model_router=router,
            ).run()
            self.assertEqual(outcome.result.modified_paths, ["src/value.c"])
            self.assertEqual(source.read_text(encoding="utf-8"), "int value(void) { return 0; }\n")
            self.assertEqual(router.budget.calls, 2)
            self.assertTrue(router.records[-1].caused_patch)
            self.assertTrue(router.records[-1].improved_frontier)
            diagnostic = json.loads(
                (root / "output" / "repair-episode.json").read_text(encoding="utf-8")
            )
            self.assertTrue(diagnostic["model_call_records"][0]["changed_hypothesis"])
            self.assertTrue(diagnostic["model_call_records"][1]["caused_patch"])
            self.assertTrue(diagnostic["model_call_records"][1]["improved_frontier"])

    def test_model_plan_with_traversal_is_rejected_without_edit(self) -> None:
        plan = {
            "repair_family": "source_port",
            "rationale": "unsafe path",
            "assumptions": ["the path is writable"],
            "edits": [{
                "operation": "replace_lines",
                "path": "../outside.c",
                "expected_text": "x",
                "replacement": "y",
                "rationale": "unsafe path",
            }],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._workspace(root)
            before = source.read_bytes()
            outcome = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
                model_router=router_for(plan),
            ).run()
            self.assertEqual(outcome.result.modified_paths, [])
            self.assertEqual(source.read_bytes(), before)

    def test_regressed_rule_is_rolled_back_before_model_alternative(self) -> None:
        plan = {
            "repair_family": "source_port",
            "rationale": "replace the diagnostic-linked sentinel with the portable value",
            "assumptions": ["the sentinel is not part of the public ABI"],
            "edits": [{
                "operation": "replace_lines",
                "path": "src/value.c",
                "expected_text": "int value(void) { return -1; }",
                "replacement": "int value(void) { return 0; }",
                "rationale": "use the target-independent value",
            }],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = json.dumps({
                "case_id": "rule-then-model",
                "source_architecture": "x86_64",
                "target_architecture": "aarch64",
            })
            repo, _ = make_workspace(
                root,
                metadata=metadata,
                log="cc: error: unrecognized command-line option '-msse4.2'\n",
            )
            makefile = repo / "Makefile"
            makefile.write_text("CFLAGS += -msse4.2\n", encoding="utf-8")
            source = repo / "src" / "value.c"
            source.parent.mkdir()
            source.write_text("int value(void) { return -1; }\n", encoding="utf-8")
            provider = RegressionThenSuccessBuildFeedback()
            outcome = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=provider,
                model_router=router_for(plan),
            ).run()
            self.assertTrue(outcome.metrics.success)
            self.assertEqual(outcome.metrics.attempts, 2)
            self.assertEqual(outcome.metrics.rollback_count, 1)
            self.assertEqual(makefile.read_text(encoding="utf-8"), "CFLAGS += -msse4.2\n")
            self.assertEqual(source.read_text(encoding="utf-8"), "int value(void) { return 0; }\n")
            self.assertEqual(outcome.result.modified_paths, ["src/value.c"])


if __name__ == "__main__":
    unittest.main()
