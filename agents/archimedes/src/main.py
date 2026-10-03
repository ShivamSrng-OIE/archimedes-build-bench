"""Thin official runtime entry point for one Archimedes case."""

from __future__ import annotations

import logging
import sys

from .build.bb_build import BbBuildFeedbackProvider
from .build.feedback import BuildFeedbackProvider
from .build.unavailable import UnavailableBuildFeedbackProvider
from .config import RuntimeConfig
from .constants import PROTOCOL_VERSION
from .errors import ArchimedesError
from .logging_utils import configure_logging, log_event
from .models.router import create_runtime_router
from .reasoning.controller import RepairController
from .result_writer import write_agent_result
from .schemas import AgentResult
from .workspace.capabilities import CapabilityRegistry
from .workspace.paths import WorkspacePaths


def run() -> int:
    config = RuntimeConfig.from_environment()
    configure_logging(config.log_level)
    logger = logging.getLogger(__name__)
    workspace: WorkspacePaths | None = None
    try:
        workspace = WorkspacePaths.resolve(config.workspace_root)
        capabilities = CapabilityRegistry.discover()
        build_feedback: BuildFeedbackProvider
        bb_build_path = capabilities.path("bb-build")
        if bb_build_path is not None:
            build_feedback = BbBuildFeedbackProvider.discover(
                executable=bb_build_path,
                input_dir=workspace.input,
                repo_root=workspace.repo,
            )
        else:
            build_feedback = UnavailableBuildFeedbackProvider()
        log_event(logger, "workspace", "capabilities discovered", count=len(capabilities.executables))
        controller = RepairController(
            config=config,
            workspace=workspace,
            build_feedback=build_feedback,
            model_router=create_runtime_router(config),
        )
        outcome = controller.run()
        result_path = write_agent_result(workspace.output, outcome.result)
        log_event(logger, "finalize", "agent result written", path=result_path)
        return 0
    except ArchimedesError as exc:
        log_event(logger, "fatal", str(exc), error_type=type(exc).__name__)
        if workspace is not None:
            result = AgentResult(PROTOCOL_VERSION, "failed", str(exc), [])
            write_agent_result(workspace.output, result)
        return 1
    except Exception:
        logger.exception("[fatal] unexpected internal error")
        if workspace is not None:
            result = AgentResult(PROTOCOL_VERSION, "failed", "Unexpected internal error.", [])
            write_agent_result(workspace.output, result)
        return 2


if __name__ == "__main__":
    sys.exit(run())
