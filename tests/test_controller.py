"""End-to-end tests for ``RepairController`` on small offline workspaces.

Each test builds a tiny package, runs the full controller with the mock model and no
build feedback, and checks which files were changed and which output files were written.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.build.unavailable import UnavailableBuildFeedbackProvider
from src.config import FeatureFlags
from src.reasoning.controller import RepairController
from src.schemas import RepairFamily
from src.workspace.paths import WorkspacePaths

from .support import make_workspace, runtime_config


class ControllerTests(unittest.TestCase):
    """Tests for the whole repair flow, from Case loading to output files."""

    def test_official_task_contract_and_generic_abort_repair(self) -> None:
        """Check that a Case given in the runner's ``task.json`` format, with an RPM spec that
        echoes an error and exits on purpose, gets the abort removed and writes metrics and a trace
        that starts at ``case_ingestion`` and ends at ``finalize``.
        """
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            task = json.dumps({
                "schema_version": "0.1",
                "case_id": "hello",
                "worktree": "/workspace/work/repo",
                "initial_build_log": "/workspace/input/initial-build.log",
            })
            repo, output = make_workspace(root, metadata="{}", log="")
            (root / "input" / "case.json").unlink()
            (root / "input" / "task.json").write_text(task, encoding="utf-8")
            (root / "input" / "initial-build.log").write_text(
                "intentional Build-Bench demo failure\n"
                "error: Bad exit status from /var/tmp/rpm-tmp.demo (%build)\n",
                encoding="utf-8",
            )
            package_input = repo / "input"
            package_input.mkdir()
            spec = package_input / "buildbench-hello.spec"
            broken_block = (
                '# BUILD-BENCH-DEMO-BROKEN\n'
                'echo "intentional Build-Bench demo failure" >&2\n'
                'exit 1\n'
            )
            spec.write_text(
                "Name: buildbench-hello\n%build\n" + broken_block + "%install\n",
                encoding="utf-8",
            )
            outcome = RepairController(
                config=runtime_config(root), workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
            ).run()
            self.assertEqual(outcome.result.modified_paths, ["input/buildbench-hello.spec"])
            repaired = spec.read_text(encoding="utf-8")
            self.assertNotIn('echo "intentional Build-Bench demo failure" >&2', repaired)
            self.assertNotIn("exit 1", repaired)
            self.assertIn("unconditional build abort", repaired)
            self.assertTrue((output / "metrics.json").is_file())
            trace_path = output / "execution-trace.jsonl"
            trace_events = [
                json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(trace_events[0]["stage"], "case_ingestion")
            self.assertEqual(trace_events[-1]["stage"], "finalize")

    def test_unconditional_abort_rule_is_generic_and_requires_log_evidence(self) -> None:
        """Check that the abort rule only edits the script whose echoed message appears in the
        failure log and leaves a similar script with a different message alone. The rule must follow
        log evidence, not package or file names.
        """
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            metadata = json.dumps({
                "case_id": "arbitrary-package-failure",
                "source_architecture": "aarch64",
                "target_architecture": "riscv64",
            })
            repo, _ = make_workspace(
                root,
                metadata=metadata,
                log="configure stopped because portable backend selection failed\n",
            )
            script = repo / "packaging" / "build.sh"
            script.parent.mkdir()
            original = (
                '#!/bin/sh\n'
                'echo "configure stopped because portable backend selection failed" >&2\n'
                'exit 9\n'
            )
            script.write_text(original, encoding="utf-8")
            other = repo / "packaging" / "unrelated.sh"
            other.write_text('echo "a different failure" >&2\nexit 2\n', encoding="utf-8")
            outcome = RepairController(
                config=runtime_config(root),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
            ).run()
            self.assertEqual(outcome.result.modified_paths, ["packaging/build.sh"])
            repaired = script.read_text(encoding="utf-8")
            self.assertNotIn("exit 9", repaired)
            self.assertIn("exit 2", other.read_text(encoding="utf-8"))

    def test_end_to_end_conditional_flag_repair(self) -> None:
        """Check that ``-msse4.2`` failing on aarch64 is fixed by wrapping the flag in an ``ifneq``
        test on ``$(CC) -dumpmachine`` instead of deleting it, and that one semantic anchor and all
        output reports are recorded.
        """
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            metadata = json.dumps({
                "case_id": "sse-flag",
                "source_architecture": "x86_64",
                "target_architecture": "aarch64",
            })
            log = "cc: error: unrecognized command-line option '-msse4.2'\nmake: *** error\n"
            repo, output = make_workspace(root, metadata=metadata, log=log)
            makefile = repo / "Makefile"
            makefile.write_text("CC ?= cc\nCFLAGS += -msse4.2\nall:\n\t$(CC) $(CFLAGS) demo.c\n", encoding="utf-8")
            (repo / "demo.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
            paths = WorkspacePaths.resolve(root)
            outcome = RepairController(
                config=runtime_config(root), workspace=paths,
                build_feedback=UnavailableBuildFeedbackProvider(),
            ).run()
            self.assertEqual(outcome.result.modified_paths, ["Makefile"])
            repaired = makefile.read_text(encoding="utf-8")
            self.assertIn("$(CC) -dumpmachine", repaired)
            self.assertIn("ifneq", repaired)
            self.assertIn("CFLAGS += -msse4.2", repaired)
            self.assertEqual(outcome.metrics.selected_repair_family, RepairFamily.CONDITIONAL_COMPILER_FLAG.value)
            self.assertEqual(outcome.metrics.semantic_anchor_count, 1)
            episode = json.loads((output / "repair-episode.json").read_text(encoding="utf-8"))
            self.assertEqual(episode["semantic_anchors"][0]["symbol"], "-msse4.2")
            for required in ("evidence-ledger.json", "architecture-constitution.json", "repair-episode.json", "metrics.json"):
                self.assertTrue((output / required).is_file())

    def test_semantic_anchor_ablation_changes_diagnostics_not_safe_rule(self) -> None:
        """Check that turning off semantic anchors records zero anchors in the metrics and the
        repair episode. The flag should change diagnostics only, not which rules may run.
        """
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            metadata = json.dumps({
                "case_id": "sse-ablation",
                "source_architecture": "x86_64",
                "target_architecture": "aarch64",
            })
            repo, output = make_workspace(
                root, metadata=metadata,
                log="cc: error: unrecognized command-line option '-msse4.2'\n",
            )
            (repo / "Makefile").write_text("CFLAGS += -msse4.2\n", encoding="utf-8")
            features = FeatureFlags(semantic_anchors=False)
            outcome = RepairController(
                config=runtime_config(root, features=features),
                workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
            ).run()
            self.assertEqual(outcome.metrics.semantic_anchor_count, 0)
            episode = json.loads((output / "repair-episode.json").read_text(encoding="utf-8"))
            self.assertEqual(episode["semantic_anchors"], [])

    def test_ambiguous_atomic_failure_gathers_hypotheses_without_blind_patch(self) -> None:
        """Check that an undefined ``__atomic_compare_exchange_16`` with no clear cause produces
        three hypotheses and one probe but no edit. The agent should not guess a patch when the
        evidence fits several fixes.
        """
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            metadata = json.dumps({
                "case_id": "atomic",
                "source_architecture": "x86_64",
                "target_architecture": "riscv64",
            })
            log = "ld: main.o: undefined reference to `__atomic_compare_exchange_16'\n"
            repo, _ = make_workspace(root, metadata=metadata, log=log)
            makefile = repo / "Makefile"
            makefile.write_text("demo: main.o\n\t$(CC) -o $@ $<\n", encoding="utf-8")
            before = makefile.read_text(encoding="utf-8")
            outcome = RepairController(
                config=runtime_config(root), workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
            ).run()
            self.assertEqual(outcome.result.modified_paths, [])
            self.assertEqual(makefile.read_text(encoding="utf-8"), before)
            self.assertEqual(outcome.metrics.hypothesis_count, 3)
            self.assertEqual(outcome.metrics.probe_count, 1)

    def test_low_familiarity_unknown_target_does_not_invent_patch(self) -> None:
        """Check that an unknown target architecture gets a familiarity score under 0.60, no edit,
        and no confirmed claim that rests only on model inference.
        """
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            metadata = json.dumps({
                "case_id": "ood",
                "source_architecture": "x86_64",
                "target_architecture": "mystery128be",
            })
            repo, _ = make_workspace(root, metadata=metadata, log="cc: error: unknown architecture\n")
            (repo / "code.c").write_text("#ifdef __x86_64__\nint x;\n#endif\n", encoding="utf-8")
            outcome = RepairController(
                config=runtime_config(root), workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
            ).run()
            self.assertEqual(outcome.result.modified_paths, [])
            self.assertLess(outcome.metrics.familiarity_score, 0.60)
            claims = list(outcome.ledger.claims.values())
            self.assertFalse(any(claim.status.value == "confirmed" and claim.category == "llm_inference" for claim in claims))


if __name__ == "__main__":
    unittest.main()
