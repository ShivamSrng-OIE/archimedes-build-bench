from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from .support import make_workspace, runtime_config

from src.build.unavailable import UnavailableBuildFeedbackProvider
from src.repair.planner import PUBLIC_DEMO_BROKEN_BLOCK, PUBLIC_DEMO_REPAIRED_BLOCK
from src.reasoning.controller import RepairController
from src.schemas import RepairFamily
from src.workspace.paths import WorkspacePaths


class ControllerTests(unittest.TestCase):
    def test_official_task_contract_and_released_example_repair(self) -> None:
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
            spec.write_text(
                "Name: buildbench-hello\n%build\n" + PUBLIC_DEMO_BROKEN_BLOCK + "%install\n",
                encoding="utf-8",
            )
            outcome = RepairController(
                config=runtime_config(root), workspace=WorkspacePaths.resolve(root),
                build_feedback=UnavailableBuildFeedbackProvider(),
            ).run()
            self.assertEqual(outcome.result.modified_paths, ["input/buildbench-hello.spec"])
            repaired = spec.read_text(encoding="utf-8")
            self.assertNotIn(PUBLIC_DEMO_BROKEN_BLOCK, repaired)
            self.assertIn(PUBLIC_DEMO_REPAIRED_BLOCK, repaired)
            self.assertTrue((output / "metrics.json").is_file())
            trace_path = output / "execution-trace.jsonl"
            trace_events = [
                json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(trace_events[0]["stage"], "case_ingestion")
            self.assertEqual(trace_events[-1]["stage"], "finalize")

    def test_end_to_end_conditional_flag_repair(self) -> None:
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
            for required in ("evidence-ledger.json", "architecture-constitution.json", "repair-episode.json", "metrics.json"):
                self.assertTrue((output / required).is_file())

    def test_ambiguous_atomic_failure_gathers_hypotheses_without_blind_patch(self) -> None:
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
