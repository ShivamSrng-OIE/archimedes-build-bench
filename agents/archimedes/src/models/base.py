"""Provider-neutral structured model protocol."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from ..schemas import JSONValue


class ModelTask(str, Enum):
    FAILURE_ANALYSIS = "failure_analysis"
    SEMANTIC_ANCHOR = "semantic_anchor"
    HYPOTHESIS_GENERATION = "hypothesis_generation"
    HYPOTHESIS_RANKING = "hypothesis_ranking"
    PATCH_PLANNING = "patch_planning"
    PATCH_CRITIQUE = "patch_critique"
    POST_BUILD_REFLECTION = "post_build_reflection"


class ModelTier(str, Enum):
    FAST = "fast"
    STRONG = "strong"


@dataclass(frozen=True, slots=True)
class ModelResponse:
    data: dict[str, JSONValue]
    raw_text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_seconds: float
    parsed: bool = True


@dataclass(slots=True)
class ModelCallRecord:
    task: ModelTask
    tier: ModelTier
    input_size: int
    response_size: int
    latency_seconds: float
    parsed: bool
    changed_hypothesis: bool = False
    caused_probe: bool = False
    caused_patch: bool = False
    improved_frontier: bool | None = None


class ModelClient(Protocol):
    def complete_json(
        self,
        *,
        task: ModelTask,
        tier: ModelTier,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: float,
    ) -> ModelResponse: ...
