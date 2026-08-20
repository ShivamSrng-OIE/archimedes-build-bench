"""Environment-only runtime configuration with centralized feature switches."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .constants import DEFAULT_WORKSPACE
from .errors import ConfigurationError


class ModelBackend(str, Enum):
    MOCK = "mock"
    LOCAL_OPENAI_COMPATIBLE = "local_openai_compatible"
    REMOTE_OPENAI_COMPATIBLE = "remote_openai_compatible"


class ContextMode(str, Enum):
    RAW_CONTEXT = "raw_context"
    ARCHIMEDES_SELECTED_CONTEXT = "archimedes_selected_context"


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean value")


def _int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}")
    return value


def _float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be numeric") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}")
    return value


@dataclass(frozen=True, slots=True)
class FeatureFlags:
    deterministic_forensics: bool = True
    claim_confidence: bool = True
    architecture_discovery: bool = True
    delta_graph: bool = True
    semantic_anchors: bool = True
    active_probes: bool = True
    repair_rules: bool = True
    failure_frontier: bool = True


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    workspace_root: Path
    model_backend: ModelBackend
    model_base_url: str
    model_api_key: str | None
    fast_model: str
    strong_model: str
    model_timeout_seconds: float
    max_total_model_calls: int
    max_strong_calls: int
    max_total_tokens: int
    max_estimated_cost_usd: float | None
    input_cost_per_million: float | None
    output_cost_per_million: float | None
    max_repair_attempts: int
    enable_external_knowledge: bool
    log_level: str
    debug: bool
    trace_enabled: bool
    trace_max_events: int
    context_mode: ContextMode
    random_seed: int
    features: FeatureFlags = field(default_factory=FeatureFlags)

    @classmethod
    def from_environment(cls) -> "RuntimeConfig":
        """Load configuration without reading or copying a secret-bearing .env file."""
        backend_raw = os.getenv("ARCHIMEDES_MODEL_BACKEND", "mock").strip().lower()
        try:
            backend = ModelBackend(backend_raw)
        except ValueError as exc:
            raise ConfigurationError(f"unsupported model backend: {backend_raw}") from exc
        context_raw = os.getenv(
            "ARCHIMEDES_CONTEXT_MODE", "archimedes_selected_context"
        ).strip().lower()
        try:
            context_mode = ContextMode(context_raw)
        except ValueError as exc:
            raise ConfigurationError(f"unsupported context mode: {context_raw}") from exc

        base_url = os.getenv("ARCHIMEDES_MODEL_BASE_URL", "http://127.0.0.1:11434/v1")
        if backend is ModelBackend.LOCAL_OPENAI_COMPATIBLE:
            host = base_url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
            if host not in {"127.0.0.1", "localhost", "::1"}:
                raise ConfigurationError("local model backend requires a loopback base URL")

        max_cost_raw = os.getenv("ARCHIMEDES_MAX_ESTIMATED_COST_USD")
        input_rate_raw = os.getenv("ARCHIMEDES_INPUT_COST_PER_MILLION")
        output_rate_raw = os.getenv("ARCHIMEDES_OUTPUT_COST_PER_MILLION")
        features = FeatureFlags(
            deterministic_forensics=_bool("ENABLE_DETERMINISTIC_FORENSICS", True),
            claim_confidence=_bool("ENABLE_CLAIM_CONFIDENCE", True),
            architecture_discovery=_bool("ENABLE_ARCHITECTURE_DISCOVERY", True),
            delta_graph=_bool("ENABLE_DELTA_GRAPH", True),
            semantic_anchors=_bool("ENABLE_SEMANTIC_ANCHORS", True),
            active_probes=_bool("ENABLE_ACTIVE_PROBES", True),
            repair_rules=_bool("ENABLE_REPAIR_RULES", True),
            failure_frontier=_bool("ENABLE_FAILURE_FRONTIER", True),
        )
        return cls(
            workspace_root=Path(os.getenv("BB_WORKSPACE", DEFAULT_WORKSPACE)),
            model_backend=backend,
            model_base_url=base_url.rstrip("/"),
            model_api_key=os.getenv("ARCHIMEDES_MODEL_API_KEY"),
            fast_model=os.getenv("ARCHIMEDES_FAST_MODEL", "mock-fast"),
            strong_model=os.getenv("ARCHIMEDES_STRONG_MODEL", "mock-strong"),
            model_timeout_seconds=_float("ARCHIMEDES_MODEL_TIMEOUT_SECONDS", 30.0, 0.1),
            max_total_model_calls=_int("ARCHIMEDES_MAX_TOTAL_MODEL_CALLS", 12),
            max_strong_calls=_int("ARCHIMEDES_MAX_STRONG_CALLS", 3),
            max_total_tokens=_int("ARCHIMEDES_MAX_TOTAL_TOKENS", 50_000),
            max_estimated_cost_usd=float(max_cost_raw) if max_cost_raw else None,
            input_cost_per_million=float(input_rate_raw) if input_rate_raw else None,
            output_cost_per_million=float(output_rate_raw) if output_rate_raw else None,
            max_repair_attempts=_int("ARCHIMEDES_MAX_REPAIR_ATTEMPTS", 3, 1),
            enable_external_knowledge=_bool("ARCHIMEDES_ENABLE_EXTERNAL_KNOWLEDGE", False),
            log_level=os.getenv("ARCHIMEDES_LOG_LEVEL", "INFO").upper(),
            debug=_bool("ARCHIMEDES_DEBUG", False),
            trace_enabled=_bool("ARCHIMEDES_ENABLE_LOCAL_TRACE", True),
            trace_max_events=_int("ARCHIMEDES_TRACE_MAX_EVENTS", 500, 1),
            context_mode=context_mode,
            random_seed=_int("ARCHIMEDES_RANDOM_SEED", 0),
            features=features,
        )
