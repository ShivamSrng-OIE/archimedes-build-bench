"""Environment-only runtime configuration with centralized feature switches."""

from __future__ import annotations

import os
import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit

from .constants import DEFAULT_WORKSPACE
from .errors import ConfigurationError


class ModelBackend(str, Enum):
    MOCK = "mock"
    LOCAL_OPENAI_COMPATIBLE = "local_openai_compatible"
    REMOTE_OPENAI_COMPATIBLE = "remote_openai_compatible"


class ContextMode(str, Enum):
    RAW_CONTEXT = "raw_context"
    ARCHIMEDES_SELECTED_CONTEXT = "archimedes_selected_context"


class ModelTierMode(str, Enum):
    AUTO = "auto"
    FAST = "fast"
    STRONG = "strong"


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
    if not math.isfinite(value):
        raise ConfigurationError(f"{name} must be finite")
    if value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}")
    return value


def _optional_float(name: str, minimum: float = 0.0) -> float | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be numeric") from exc
    if not math.isfinite(value):
        raise ConfigurationError(f"{name} must be finite")
    if value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}")
    return value


def _validated_base_url(raw: str, backend: ModelBackend) -> str:
    value = raw.strip().rstrip("/")
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in value
    ):
        raise ConfigurationError(
            "ARCHIMEDES_MODEL_BASE_URL must not contain whitespace or controls"
        )
    parsed = urlsplit(value)
    try:
        parsed.port
    except ValueError as exc:
        raise ConfigurationError(
            "ARCHIMEDES_MODEL_BASE_URL contains an invalid port"
        ) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError(
            "ARCHIMEDES_MODEL_BASE_URL must be an HTTP(S) URL without credentials, query, or fragment"
        )
    if backend is ModelBackend.LOCAL_OPENAI_COMPATIBLE and parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ConfigurationError("local model backend requires a loopback base URL")
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
    model_tier_mode: ModelTierMode
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
            raise ConfigurationError(
                f"unsupported model backend: {backend_raw}"
            ) from exc
        context_raw = (
            os.getenv("ARCHIMEDES_CONTEXT_MODE", "archimedes_selected_context")
            .strip()
            .lower()
        )
        try:
            context_mode = ContextMode(context_raw)
        except ValueError as exc:
            raise ConfigurationError(
                f"unsupported context mode: {context_raw}"
            ) from exc
        tier_raw = os.getenv("ARCHIMEDES_MODEL_TIER", "auto").strip().lower()
        try:
            tier_mode = ModelTierMode(tier_raw)
        except ValueError as exc:
            raise ConfigurationError(
                f"unsupported model tier mode: {tier_raw}"
            ) from exc

        base_url = _validated_base_url(
            os.getenv("ARCHIMEDES_MODEL_BASE_URL", "http://127.0.0.1:11434/v1"),
            backend,
        )
        fast_model = os.getenv("ARCHIMEDES_FAST_MODEL", "mock-fast").strip()
        strong_model = os.getenv("ARCHIMEDES_STRONG_MODEL", "mock-strong").strip()
        if backend is not ModelBackend.MOCK and (not fast_model or not strong_model):
            raise ConfigurationError(
                "real model backends require non-empty model identifiers"
            )
        max_cost = _optional_float("ARCHIMEDES_MAX_ESTIMATED_COST_USD")
        input_rate = _optional_float("ARCHIMEDES_INPUT_COST_PER_MILLION")
        output_rate = _optional_float("ARCHIMEDES_OUTPUT_COST_PER_MILLION")
        if (input_rate is None) != (output_rate is None):
            raise ConfigurationError(
                "input and output model cost rates must be configured together"
            )
        if max_cost is not None and input_rate is None:
            raise ConfigurationError(
                "a model cost limit requires both input and output cost rates"
            )
        log_level = os.getenv("ARCHIMEDES_LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ConfigurationError(
                "ARCHIMEDES_LOG_LEVEL must be a standard logging level"
            )
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
            model_base_url=base_url,
            model_api_key=os.getenv("ARCHIMEDES_MODEL_API_KEY"),
            fast_model=fast_model,
            strong_model=strong_model,
            model_tier_mode=tier_mode,
            model_timeout_seconds=_float("ARCHIMEDES_MODEL_TIMEOUT_SECONDS", 30.0, 0.1),
            max_total_model_calls=_int("ARCHIMEDES_MAX_TOTAL_MODEL_CALLS", 12),
            max_strong_calls=_int("ARCHIMEDES_MAX_STRONG_CALLS", 3),
            max_total_tokens=_int("ARCHIMEDES_MAX_TOTAL_TOKENS", 50_000),
            max_estimated_cost_usd=max_cost,
            input_cost_per_million=input_rate,
            output_cost_per_million=output_rate,
            max_repair_attempts=_int("ARCHIMEDES_MAX_REPAIR_ATTEMPTS", 3, 1),
            enable_external_knowledge=_bool(
                "ARCHIMEDES_ENABLE_EXTERNAL_KNOWLEDGE", False
            ),
            log_level=log_level,
            debug=_bool("ARCHIMEDES_DEBUG", False),
            trace_enabled=_bool("ARCHIMEDES_ENABLE_LOCAL_TRACE", True),
            trace_max_events=_int("ARCHIMEDES_TRACE_MAX_EVENTS", 500, 1),
            context_mode=context_mode,
            random_seed=_int("ARCHIMEDES_RANDOM_SEED", 0),
            features=features,
        )
