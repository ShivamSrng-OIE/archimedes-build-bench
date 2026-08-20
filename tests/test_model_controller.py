from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.build.unavailable import UnavailableBuildFeedbackProvider
from src.context.token_budget import UsageBudget
from src.models.base import ModelTask
from src.models.mock import MockModelClient
from src.models.router import ModelRouter
from src.reasoning.controller import RepairController
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
                build_feedback=UnavailableBuildFeedbackProvider(),
                model_router=router,
            ).run()
            self.assertEqual(outcome.result.modified_paths, ["src/value.c"])
            self.assertEqual(source.read_text(encoding="utf-8"), "int value(void) { return 0; }\n")
            self.assertEqual(router.budget.calls, 2)
            self.assertTrue(router.records[-1].caused_patch)

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


if __name__ == "__main__":
    unittest.main()
