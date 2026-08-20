from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.architecture.hazard_scanner import ArchitectureHazard
from src.context.repository_search import find_portable_fallbacks, inventory_repository
from src.graph.builder import build_delta_graph
from src.graph.query import neighbors
from src.schemas import (
    Architecture,
    ArchitectureIdentity,
    CaseData,
    FailureAnalysis,
    FailureFamily,
    FailureFrontier,
    FrontierStage,
)


class GraphContextTests(unittest.TestCase):
    def test_inventory_and_portable_fallback_search(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            (repo / "src").mkdir()
            (repo / "src" / "generic.c").write_text("int portable_fallback(void) { return 0; }", encoding="utf-8")
            (repo / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
            inventory = inventory_repository(repo)
            self.assertIn("Makefile", inventory.build_files)
            self.assertEqual(find_portable_fallbacks(repo)[0].path, "src/generic.c")

    def test_delta_graph_traces_matching_hazard(self) -> None:
        identity = ArchitectureIdentity(Architecture.AARCH64, "aarch64", ["aarch64"])
        source = ArchitectureIdentity(Architecture.X86_64, "x86_64", ["x86_64"])
        case = CaseData("case", source, identity, "")
        failure = FailureAnalysis(
            "", "cc: error: unsupported option -msse4.2", FailureFamily.COMPILATION, [],
            FailureFrontier(FrontierStage.COMPILE, "flag"),
        )
        hazard = ArchitectureHazard("Makefile", 2, "compiler_flag", "-msse4.2", "CFLAGS += -msse4.2", 0.9)
        graph = build_delta_graph(case, failure, [hazard])
        self.assertIn("hazard-001", neighbors(graph, "failure-001"))


if __name__ == "__main__":
    unittest.main()
