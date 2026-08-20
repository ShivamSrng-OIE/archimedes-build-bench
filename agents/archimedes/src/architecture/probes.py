"""Predefined, side-effect-bounded diagnostic probe execution."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..constants import MAX_TOOL_OUTPUT_CHARACTERS
from ..errors import ProbeError
from ..schemas import Probe
from ..workspace.capabilities import CapabilityRegistry


@dataclass(frozen=True, slots=True)
class ProbeResult:
    probe_id: str
    return_code: int
    stdout: str
    stderr: str
    compiler_provenance: str | None = None


_ALLOWED_PROBE_TYPES = {
    "repository_search", "preprocessor_macro_query", "compiler_feature_test",
    "compile_only", "link_only", "pkg_config_query", "rpmspec_expansion",
    "make_dry_run", "file_inspection", "symbol_lookup",
}


def execute_probe(
    probe: Probe, *, capabilities: CapabilityRegistry, repo_root: Path
) -> ProbeResult:
    """Execute only controller-defined argument arrays with explicit bounds."""
    if not probe.allowed or probe.probe_type not in _ALLOWED_PROBE_TYPES:
        raise ProbeError(f"probe type is not allowed: {probe.probe_type}")
    if not probe.command:
        raise ProbeError("probe command is empty")
    executable = Path(probe.command[0]).name
    if not capabilities.has(executable):
        raise ProbeError(f"probe executable is unavailable: {executable}")
    cwd = (repo_root / probe.working_directory).resolve(strict=True)
    try:
        cwd.relative_to(repo_root.resolve(strict=True))
    except ValueError as exc:
        raise ProbeError("probe working directory escapes repository") from exc
    try:
        completed = subprocess.run(
            probe.command,
            cwd=cwd,
            timeout=probe.timeout_seconds,
            capture_output=True,
            text=True,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError(f"probe execution failed: {exc}") from exc
    provenance = capabilities.path(executable) if probe.requires_target_toolchain else None
    return ProbeResult(
        probe.id,
        completed.returncode,
        completed.stdout[:MAX_TOOL_OUTPUT_CHARACTERS],
        completed.stderr[:MAX_TOOL_OUTPUT_CHARACTERS],
        provenance,
    )
