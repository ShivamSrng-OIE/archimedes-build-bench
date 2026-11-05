"""Tests for how the repair controller uses model-generated patch plans.

A mock model client returns scripted analyses and plans, so these tests can check
that unsafe or incomplete plans are rejected, that the model is asked again after a
rejection, and that model calls are credited only for patches they really caused.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.build.feedback import BuildFeedbackResult
from src.build.unavailable import UnavailableBuildFeedbackProvider
from src.config import FeatureFlags
from src.context.token_budget import UsageBudget
from src.errors import ModelError
from src.forensics.log_parser import analyze_build_log
from src.models.base import ModelTask
from src.models.router import ModelRouter
from src.reasoning.controller import RepairController
from src.schemas import FailureFrontier, FrontierStage
from src.workspace.paths import WorkspacePaths

from .mock_model import MockModelClient
from .support import make_workspace, runtime_config


def analysis_response() -> dict[str, object]:
    """Return a canned failure analysis that points at src/value.c.

    Returns:
        A failure-analysis reply with one claim and src/value.c as the
        recommended file.
    """
    return {
        "primary_failure": "invalid target-independent return value",
        "failure_stage": "compile",
        "claims": [{"statement": "the diagnostic identifies src/value.c"}],
        "hypotheses": [],
        "recommended_files": ["src/value.c"],
    }


def router_for(plan: dict[str, object] | list[dict[str, object]]) -> ModelRouter:
    """Build a model router whose mock client answers with the given patch plans.

    Args:
        plan: One patch plan, or a list of plans returned in order on
            successive planning calls.

    Returns:
        A ModelRouter allowing 5 calls, 2 of them strong-tier, 20,000 tokens
        and a 2-second timeout per request.
    """
    client = MockModelClient(
        {
            ModelTask.FAILURE_ANALYSIS: analysis_response(),
            ModelTask.PATCH_PLANNING: plan,
        }
    )
    return ModelRouter(client, UsageBudget(5, 2, 20_000), 2.0)


class SuccessfulBuildFeedback:
    """Build gateway stand-in whose single allowed build always succeeds.

    Attributes:
        available: Always True.
        reason_unavailable: Always None.
        request_limit: One build request.
    """

    available = True
    reason_unavailable = None
    request_limit = 1

    def request(self, *, expected_information_value: float) -> BuildFeedbackResult:
        """Return a successful build result.

        Args:
            expected_information_value: The controller's estimate of what the
                build would teach it; must be above zero.

        Returns:
            A successful result with request id "model-build-1".

        Raises:
            AssertionError: If the expected information value is zero or less.
        """
        if expected_information_value <= 0:
            raise AssertionError("build experiment needs information value")
        return BuildFeedbackResult(
            True,
            "build succeeded\n",
            FailureFrontier(FrontierStage.SUCCESS, "success", confidence=0.99),
            "model-build-1",
        )


class RegressionThenSuccessBuildFeedback:
    """Build gateway stand-in that regresses to configure first, then succeeds.

    Attributes:
        available: Always True.
        reason_unavailable: Always None.
        request_limit: Two build requests.
        calls: How many builds have been requested so far.
    """

    available = True
    reason_unavailable = None
    request_limit = 2

    def __init__(self) -> None:
        """Start with no builds requested.

        Returns:
            None.
        """
        self.calls = 0

    def request(self, *, expected_information_value: float) -> BuildFeedbackResult:
        """Return a configure-stage failure on the first call and success afterwards.

        Args:
            expected_information_value: The controller's estimate of what the
                build would teach it; must be above zero.

        Returns:
            The "build-regression" result first, then "build-success".

        Raises:
            AssertionError: If the expected information value is zero or less.
        """
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
    """Mock model client that fails every request like an unreachable provider."""

    def __init__(self) -> None:
        """Set up the mock with no canned replies.

        Returns:
            None.
        """
        super().__init__({})

    def complete_json(self, **kwargs):
        """Fail the request.

        Args:
            **kwargs: The usual complete_json() keyword arguments; ignored.

        Returns:
            Never returns.

        Raises:
            ModelError: Always, to simulate a provider outage.
        """
        del kwargs
        raise ModelError("simulated provider outage")


class ModelControllerTests(unittest.TestCase):
    """Check plan validation, replanning and credit for model-driven repairs."""

    def _workspace(self, root: Path) -> Path:
        """Create a workspace whose log blames src/value.c, and write that file.

        Args:
            root: The temporary directory to build the workspace in.

        Returns:
            The path of src/value.c, which holds "return -1" for the plans
            to replace.
        """
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
        """Check that a provider outage ends the run cleanly with no edits and no model records.

        The result message must not claim model evidence that was never received.
        """
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
        """Check that a valid model patch plan is applied, built and credited in the episode record.

        The analysis call must be marked as changing a hypothesis, and the planning call as
        causing the patch and improving the frontier.
        """
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
        """Check that the model sees a window of src/deep.c around line 240, starting at line 161.

        Sending the top of a long file would hide the line the compiler complained about.
        """
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
        """Check that a model plan editing ../outside.c is refused and nothing is written."""
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
        """Check that a rule fix that regresses the build is undone before the model plan is tried.

        The final Makefile must hold only the model's guard, with the flag line appearing once.
        """
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
        """Check that the model is asked again when its first plan leaves -msse4.2 unguarded.

        Inserting a guarded copy keeps the original flag line active, so that plan must be
        rejected and only the second plan credited with the patch.
        """
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
        """Check that a plan the critic rejects changes nothing and gets no credit for a patch."""
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
        """Check that a guard comparing the whole target triple to "x86_64" is rejected.

        gcc -dumpmachine prints a full triple such as x86_64-linux-gnu, so only a prefix
        match like x86_64% actually keeps the flag on x86 builds.
        """
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
        """Check that a plan mentioning a curl download is refused and the safe second plan used.

        Repairs must not fetch code from the network, so the policy critic blocks them.
        """
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
