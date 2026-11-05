"""Tests for the repository inventory and the delta graph that links failures to hazards."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.architecture.hazard_scanner import ArchitectureHazard
from src.context.repository_search import inventory_repository
from src.graph.builder import build_delta_graph
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
    """Tests for finding build files and tracing failures to hazards."""

    def test_inventory_finds_build_files(self) -> None:
        """Check that the inventory lists a top-level ``Makefile`` as a build file."""
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            (repo / "src").mkdir()
            (repo / "src" / "generic.c").write_text("int portable_fallback(void) { return 0; }", encoding="utf-8")
            (repo / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
            inventory = inventory_repository(repo)
            self.assertIn("Makefile", inventory.build_files)

    def test_debian_rules_are_build_and_packaging_context(self) -> None:
        """Check that ``debian/rules`` is listed both as a build file and as a packaging file."""
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            (repo / "debian").mkdir()
            (repo / "debian" / "rules").write_text("#!/usr/bin/make -f\n", encoding="utf-8")
            inventory = inventory_repository(repo)
            self.assertIn("debian/rules", inventory.build_files)
            self.assertIn("debian/rules", inventory.packaging_files)

    def test_inventory_file_limit_is_enforced_during_traversal(self) -> None:
        """Check that ``max_files`` stops the walk once the limit is reached, keeping the first
        files in sorted order.
        """
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name)
            for index in range(5):
                (repo / f"file-{index}.txt").write_text("x", encoding="utf-8")
            inventory = inventory_repository(repo, max_files=2)
            self.assertEqual(inventory.files, ["file-0.txt", "file-1.txt"])

    def test_delta_graph_traces_matching_hazard(self) -> None:
        """Check that the delta graph links a failure about ``-msse4.2`` to the hazard for that flag
        in the Makefile.
        """
        identity = ArchitectureIdentity(Architecture.AARCH64, "aarch64", ["aarch64"])
        source = ArchitectureIdentity(Architecture.X86_64, "x86_64", ["x86_64"])
        case = CaseData("case", source, identity, "")
        failure = FailureAnalysis(
            "", "cc: error: unsupported option -msse4.2", FailureFamily.COMPILATION, [],
            FailureFrontier(FrontierStage.COMPILE, "flag"),
        )
        hazard = ArchitectureHazard("Makefile", 2, "compiler_flag", "-msse4.2", "CFLAGS += -msse4.2", 0.9)
        graph = build_delta_graph(case, failure, [hazard])
        targets = [edge.target for edge in graph.adjacency.get("failure-001", [])]
        self.assertIn("hazard-001", targets)


if __name__ == "__main__":
    unittest.main()
