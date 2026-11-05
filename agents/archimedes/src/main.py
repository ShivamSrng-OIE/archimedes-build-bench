"""Process entry point that runs Archimedes on one Build-Bench Case.

It resolves the workspace, loads the configuration, picks a build-feedback provider
and a model router, runs the repair controller, writes ``agent-result.json`` and
turns the outcome into the process exit code.
"""

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
    """Try to write a ``"failed"`` agent result after a fatal error.

    The nonzero exit code is what tells the platform the Agent failed; the
    result file only adds a readable message. Any error while writing it is logged and
    swallowed so it cannot replace the original failure.

    Args:
        logger: Logger used to report a failure to write the result.
        workspace: The resolved workspace, or ``None`` if resolution itself
            failed, in which case nothing is written.
        message: Human-readable reason stored in the result's ``message``.

    Returns:
        None.
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
    """Run the Agent once on the workspace named by ``BB_WORKSPACE``.

    Build feedback through ``bb-build`` is considered only outside hosted mode,
    when the executable is on ``PATH`` and the Case declares it; in hosted
    evaluation the Agent never asks for official builds.

    Returns:
        The process exit code: 0 when the run completed (with or without an
        edit), 1 for a known ``ArchimedesError`` such as invalid configuration
        or a fatal hosted model failure, and 2 for any unexpected error.
    """
    # The logger and the workspace are set up before the optional settings are
    # parsed, so a malformed setting still produces a structured
    # agent-result.json whenever the official workspace is valid.
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
