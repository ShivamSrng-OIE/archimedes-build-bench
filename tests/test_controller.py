from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from .support import make_workspace, runtime_config

from src.build.unavailable import UnavailableBuildFeedbackProvider
from src.reasoning.controller import RepairController
from src.schemas import RepairFamily
from src.workspace.paths import WorkspacePaths


class ControllerTests(unittest.TestCase):
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
