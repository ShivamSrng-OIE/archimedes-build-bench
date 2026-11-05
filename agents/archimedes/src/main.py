"""Thin official runtime entry point for one Archimedes case."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from .build.bb_build import BbBuildFeedbackProvider
from .build.feedback import BuildFeedbackProvider
from .build.unavailable import UnavailableBuildFeedbackProvider
from .config import ModelBackend, RuntimeConfig
from .constants import DEFAULT_WORKSPACE, PROTOCOL_VERSION
from .errors import ArchimedesError
from .logging_utils import configure_logging, log_event
from .models.router import create_runtime_router
from .reasoning.controller import RepairController
from .result_writer import write_agent_result
from .schemas import AgentResult
from .workspace.capabilities import CapabilityRegistry
from .workspace.paths import WorkspacePaths


def _write_terminal_diagnostic(
    logger: logging.Logger,
    workspace: WorkspacePaths | None,
    *,
    message: str,
) -> None:
    """Best-effort protocol diagnostic without masking the fatal process exit.

    The nonzero process exit remains the authoritative Agent-error signal.
    """
    if workspace is None:
        return
    try:
        write_agent_result(
            workspace.output,
            AgentResult(PROTOCOL_VERSION, "failed", message, []),
        )
    except Exception:
        logger.exception("[fatal] unable to write agent failure result")


def run() -> int:
    # Establish a safe logger and the protocol output location before parsing
    # optional runtime settings. A malformed setting must still yield a
    # structured agent-result.json whenever the official workspace is valid.
    configure_logging("INFO")
    logger = logging.getLogger(__name__)
    workspace: WorkspacePaths | None = None
    try:
        workspace = WorkspacePaths.resolve(
            Path(os.getenv("BB_WORKSPACE", DEFAULT_WORKSPACE))
        )
        config = RuntimeConfig.from_environment()
        configure_logging(config.log_level)
        capabilities = CapabilityRegistry.discover()
        build_feedback: BuildFeedbackProvider
        bb_build_path = capabilities.path("bb-build")
        if (
            config.model_backend is not ModelBackend.HOSTED_OPENAI_COMPATIBLE
            and bb_build_path is not None
        ):
            build_feedback = BbBuildFeedbackProvider.discover(
                executable=bb_build_path,
                input_dir=workspace.input,
                repo_root=workspace.repo,
            )
        else:
            build_feedback = UnavailableBuildFeedbackProvider()
        log_event(
            logger,
            "workspace",
            "capabilities discovered",
            count=len(capabilities.executables),
        )
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
        _write_terminal_diagnostic(logger, workspace, message=str(exc))
        return 1
    except Exception:
        logger.exception("[fatal] unexpected internal error")
        _write_terminal_diagnostic(
            logger, workspace, message="Unexpected internal error."
        )
        return 2


if __name__ == "__main__":
    sys.exit(run())
