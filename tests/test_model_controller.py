from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.build.feedback import BuildFeedbackResult
from src.build.unavailable import UnavailableBuildFeedbackProvider
from src.context.token_budget import UsageBudget
from src.config import FeatureFlags
from src.errors import ModelError
from src.forensics.log_parser import analyze_build_log
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


def router_for(plan: dict[str, object] | list[dict[str, object]]) -> ModelRouter:
    client = MockModelClient(
        {
            ModelTask.FAILURE_ANALYSIS: analysis_response(),
            ModelTask.PATCH_PLANNING: plan,
        }
    )
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


class FailingModelClient(MockModelClient):
    def __init__(self) -> None:
        super().__init__({})

    def complete_json(self, **kwargs):
        del kwargs
        raise ModelError("simulated provider outage")


class ModelControllerTests(unittest.TestCase):
    def _workspace(self, root: Path) -> Path:
        metadata = json.dumps(
            {
                "case_id": "model-plan",
                "source_architecture": "x86_64",
                "target_architecture": "aarch64",
            }
        )
        repo, _ = make_workspace(
            root,
            metadata=metadata,
            log="src/value.c:1: error: invalid target-independent return value\n",
        )
        source = repo / "src" / "value.c"
        source.parent.mkdir()
        source.write_text("int value(void) { return -1; }\n", encoding="utf-8")
        return source

    def test_model_provider_failure_finishes_without_false_model_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._workspace(root)
            router = ModelRouter(FailingModelClient(), UsageBudget(5, 2, 20_000), 2.0)

            outcome = RepairController(
                config=runtime_config(root, features=FeatureFlags(repair_rules=False)),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
                model_router=router,
            ).run()

            self.assertEqual(outcome.result.status, "completed")
            self.assertEqual(outcome.result.modified_paths, [])
            self.assertEqual(router.records, [])
            self.assertNotIn("model evidence", outcome.result.message)

    def test_mock_model_plan_passes_guarded_executor(self) -> None:
        plan = {
            "repair_family": "source_port",
            "rationale": "replace the diagnostic-linked sentinel with the portable value",
            "assumptions": ["the sentinel is not part of the public ABI"],
            "edits": [
                {
                    "operation": "replace_lines",
                    "path": "src/value.c",
                    "expected_text": "int value(void) { return -1; }",
                    "replacement": "int value(void) { return 0; }",
                    "rationale": "use the target-independent value",
                }
            ],
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
            self.assertEqual(
                source.read_text(encoding="utf-8"), "int value(void) { return 0; }\n"
            )
            self.assertEqual(router.budget.calls, 2)
            self.assertTrue(router.records[-1].caused_patch)
            self.assertTrue(router.records[-1].improved_frontier)
            diagnostic = json.loads(
                (root / "output" / "repair-episode.json").read_text(encoding="utf-8")
            )
            self.assertTrue(diagnostic["model_call_records"][0]["changed_hypothesis"])
            self.assertTrue(diagnostic["model_call_records"][1]["caused_patch"])
            self.assertTrue(diagnostic["model_call_records"][1]["improved_frontier"])

    def test_model_context_centers_the_causal_source_line(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, _ = make_workspace(root)
            source = repo / "src" / "deep.c"
            source.parent.mkdir()
            source.write_text(
                "\n".join(f"line {number}" for number in range(1, 301)) + "\n",
                encoding="utf-8",
            )
            controller = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
            )
            failure = analyze_build_log("src/deep.c:240:7: error: unsupported opcode\n")
            context = controller._model_file_context(failure, [])
        self.assertIn("--- src/deep.c:161", context)
        self.assertIn("line 240", context)
        self.assertNotIn("line 1\n", context)

    def test_model_plan_with_traversal_is_rejected_without_edit(self) -> None:
        plan = {
            "repair_family": "source_port",
            "rationale": "unsafe path",
            "assumptions": ["the path is writable"],
            "edits": [
                {
                    "operation": "replace_lines",
                    "path": "../outside.c",
                    "expected_text": "x",
                    "replacement": "y",
                    "rationale": "unsafe path",
                }
            ],
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
            "repair_family": "architecture_guard",
            "rationale": "guard the existing x86-only option using a different repair family",
            "assumptions": ["the option is an optional x86 optimization"],
            "edits": [
                {
                    "operation": "replace_lines",
                    "path": "Makefile",
                    "expected_text": "CFLAGS += -msse4.2",
                    "replacement": "ifeq ($(findstring x86_64,$(shell $(CC) -dumpmachine)),x86_64)\nCFLAGS += -msse4.2\nendif",
                    "rationale": "retain the option only for the source architecture",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = json.dumps(
                {
                    "case_id": "rule-then-model",
                    "source_architecture": "x86_64",
                    "target_architecture": "aarch64",
                }
            )
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
            repaired = makefile.read_text(encoding="utf-8")
            self.assertEqual(repaired.count("CFLAGS += -msse4.2"), 1)
            self.assertIn("ifeq", repaired)
            self.assertEqual(
                source.read_text(encoding="utf-8"), "int value(void) { return -1; }\n"
            )
            self.assertEqual(outcome.result.modified_paths, ["Makefile"])

    def test_model_replans_when_first_plan_leaves_diagnosed_flag_active(self) -> None:
        invalid_insert = {
            "repair_family": "architecture_guard",
            "rationale": "add an architecture guard",
            "assumptions": ["the option is x86-specific"],
            "edits": [
                {
                    "operation": "insert_after",
                    "path": "Makefile",
                    "expected_text": "CC ?= cc",
                    "replacement": "ifeq ($(ARCH),x86_64)\nCFLAGS += -msse4.2\nendif",
                    "rationale": "guard a new declaration",
                }
            ],
        }
        valid_replace = {
            "repair_family": "conditional_compiler_flag",
            "rationale": "guard the existing x86-only option",
            "assumptions": ["the option is an optional x86 optimization"],
            "edits": [
                {
                    "operation": "replace_lines",
                    "path": "Makefile",
                    "expected_text": "CFLAGS += -msse4.2",
                    "replacement": "ifneq (,$(filter x86_64%,$(shell $(CC) -dumpmachine)))\nCFLAGS += -msse4.2\nendif",
                    "rationale": "retain the option only for x86 targets",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = json.dumps(
                {
                    "case_id": "model-replan",
                    "source_architecture": "x86_64",
                    "target_architecture": "aarch64",
                }
            )
            repo, _ = make_workspace(
                root,
                metadata=metadata,
                log="cc: error: unrecognized command-line option '-msse4.2'\n",
            )
            makefile = repo / "Makefile"
            makefile.write_text(
                "CC ?= cc\nCFLAGS += -msse4.2\nall:\n\t$(CC) $(CFLAGS) demo.c\n",
                encoding="utf-8",
            )
            (repo / "demo.c").write_text(
                "int main(void){return 0;}\n", encoding="utf-8"
            )
            features = FeatureFlags(repair_rules=False)
            router = router_for([invalid_insert, valid_replace])
            outcome = RepairController(
                config=runtime_config(root, features=features),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
                model_router=router,
            ).run()
            repaired = makefile.read_text(encoding="utf-8")
            self.assertEqual(router.budget.calls, 3)
            self.assertEqual(repaired.count("CFLAGS += -msse4.2"), 1)
            self.assertIn("ifneq", repaired)
            self.assertEqual(outcome.result.modified_paths, ["Makefile"])
            self.assertFalse(router.records[1].caused_patch)
            self.assertTrue(router.records[2].caused_patch)

    def test_critic_rejected_model_plan_is_not_credited_with_a_patch(self) -> None:
        rejected = {
            "repair_family": "conditional_compiler_flag",
            "rationale": "probe compiler support without a complete conditional",
            "assumptions": ["the option is optional"],
            "edits": [
                {
                    "operation": "replace_lines",
                    "path": "Makefile",
                    "expected_text": "CFLAGS += -msse4.2",
                    "replacement": (
                        'check_c_compiler_flag("-msse4.2" COMPILER_SUPPORTS_SSE)\n'
                        "CFLAGS += -msse4.2"
                    ),
                    "rationale": "detect support",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = json.dumps(
                {
                    "case_id": "critic-rejection",
                    "source_architecture": "x86_64",
                    "target_architecture": "aarch64",
                }
            )
            repo, _ = make_workspace(
                root,
                metadata=metadata,
                log="cc: error: unrecognized command-line option '-msse4.2'\n",
            )
            makefile = repo / "Makefile"
            makefile.write_text("CFLAGS += -msse4.2\n", encoding="utf-8")
            router = router_for(rejected)
            outcome = RepairController(
                config=runtime_config(root, features=FeatureFlags(repair_rules=False)),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
                model_router=router,
            ).run()
            self.assertEqual(outcome.result.modified_paths, [])
            self.assertEqual(
                makefile.read_text(encoding="utf-8"), "CFLAGS += -msse4.2\n"
            )
            self.assertFalse(router.records[-1].caused_patch)

    def test_model_replans_when_target_triple_match_is_exact(self) -> None:
        exact_match = {
            "repair_family": "architecture_guard",
            "rationale": "guard the existing x86 option",
            "assumptions": ["the option is x86-specific"],
            "edits": [
                {
                    "operation": "replace_lines",
                    "path": "Makefile",
                    "expected_text": "CFLAGS += -msse4.2",
                    "replacement": (
                        "ifeq ($(filter x86_64,$(shell $(CC) -dumpmachine)),x86_64)\n"
                        "CFLAGS += -msse4.2\nendif"
                    ),
                    "rationale": "match x86",
                }
            ],
        }
        wildcard_match = {
            "repair_family": "architecture_guard",
            "rationale": "guard the existing x86 option using a target-triple prefix",
            "assumptions": ["the option is x86-specific"],
            "edits": [
                {
                    "operation": "replace_lines",
                    "path": "Makefile",
                    "expected_text": "CFLAGS += -msse4.2",
                    "replacement": (
                        "ifneq (,$(filter x86_64%,$(shell $(CC) -dumpmachine)))\n"
                        "CFLAGS += -msse4.2\nendif"
                    ),
                    "rationale": "match the x86 target-triple prefix",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = json.dumps(
                {
                    "case_id": "triple-prefix",
                    "source_architecture": "x86_64",
                    "target_architecture": "aarch64",
                }
            )
            repo, _ = make_workspace(
                root,
                metadata=metadata,
                log="cc: error: unrecognized command-line option '-msse4.2'\n",
            )
            makefile = repo / "Makefile"
            makefile.write_text("CFLAGS += -msse4.2\n", encoding="utf-8")
            router = router_for([exact_match, wildcard_match])
            outcome = RepairController(
                config=runtime_config(root, features=FeatureFlags(repair_rules=False)),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
                model_router=router,
            ).run()
            self.assertEqual(outcome.result.modified_paths, ["Makefile"])
            self.assertIn("x86_64%", makefile.read_text(encoding="utf-8"))
            self.assertFalse(router.records[1].caused_patch)
            self.assertTrue(router.records[2].caused_patch)

    def test_model_replans_after_policy_critic_rejection(self) -> None:
        policy_sensitive = {
            "repair_family": "source_port",
            "rationale": "download a portable replacement",
            "assumptions": ["the remote content is stable"],
            "edits": [
                {
                    "operation": "replace_lines",
                    "path": "src/value.c",
                    "expected_text": "int value(void) { return -1; }",
                    "replacement": "/* curl replacement */\nint value(void) { return 0; }",
                    "rationale": "use curl to obtain a replacement",
                }
            ],
        }
        safe = {
            "repair_family": "source_port",
            "rationale": "replace the diagnosed sentinel with the local portable value",
            "assumptions": ["the sentinel is not part of the public ABI"],
            "edits": [
                {
                    "operation": "replace_lines",
                    "path": "src/value.c",
                    "expected_text": "int value(void) { return -1; }",
                    "replacement": "int value(void) { return 0; }",
                    "rationale": "use the target-independent local value",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._workspace(root)
            router = router_for([policy_sensitive, safe])
            outcome = RepairController(
                config=runtime_config(root, features=FeatureFlags(repair_rules=False)),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
                model_router=router,
            ).run()
            self.assertEqual(outcome.result.modified_paths, ["src/value.c"])
            self.assertEqual(
                source.read_text(encoding="utf-8"), "int value(void) { return 0; }\n"
            )
            self.assertEqual(router.budget.calls, 3)
            self.assertFalse(router.records[1].caused_patch)
            self.assertTrue(router.records[2].caused_patch)


if __name__ == "__main__":
    unittest.main()
