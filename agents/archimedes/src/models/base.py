"""Provider-neutral types shared by the model client, the router and the controller.

They describe what a model call is for, which tier serves it, what comes back,
and the per-call record kept for metrics.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from ..schemas import JSONValue


class ModelTask(str, Enum):
    """The reasoning job a model call performs.

    Each task has its own system prompt in ``prompts`` and its own required
    JSON keys in ``json_protocol``. The controller currently uses
    ``FAILURE_ANALYSIS``, ``PATCH_PLANNING`` and ``PATCH_CRITIQUE``; the other
    members keep their contracts defined but are not called by the current
    pipeline.
    """

    FAILURE_ANALYSIS = "failure_analysis"
    SEMANTIC_ANCHOR = "semantic_anchor"
    HYPOTHESIS_GENERATION = "hypothesis_generation"
    HYPOTHESIS_RANKING = "hypothesis_ranking"
    PATCH_PLANNING = "patch_planning"
    PATCH_CRITIQUE = "patch_critique"
    POST_BUILD_REFLECTION = "post_build_reflection"


class ModelTier(str, Enum):
    """Which configured model serves a call.

    ``FAST`` is the default; ``STRONG`` is used only when escalation signals
    ask for it or the tier is pinned in configuration. In hosted mode both tiers
    map to the same organizer model alias.
    """

    FAST = "fast"
    STRONG = "strong"


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """The validated result of one model call, including any repair retry.

    Attributes:
        data: The parsed JSON object, already checked against the task's
            contract.
        raw_text: The message content the parsed object came from.
        model: Model identifier the request was sent with.
        input_tokens: Prompt tokens reported by the provider, or an estimate of
            about four characters per token when the count is missing or
            malformed. Summed over all requests made for the call.
        output_tokens: Completion tokens, counted the same way.
        latency_seconds: Wall-clock time for all requests made for the call.
        parsed: Whether ``data`` came from a successfully parsed response.
        provider_requests: HTTP requests made: 1, or 2 when a repair retry was
            needed.
    """

    data: dict[str, JSONValue]
    raw_text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_seconds: float
    parsed: bool = True
    provider_requests: int = 1


@dataclass(slots=True)
class ModelCallRecord:
    """Metrics for one routed model call, kept in ``ModelRouter.records``.

    Prompt and response text are never stored here, only their sizes.

    Attributes:
        task: The task the call performed.
        tier: The tier that served it.
        input_size: Characters in the system and user prompts combined.
        response_size: Characters in the returned message content.
        latency_seconds: Wall-clock time of the call.
        parsed: Whether the response passed its JSON contract.
        provider_requests: HTTP requests the call used.
        changed_hypothesis: Whether a failure-analysis response added a claim
            to the evidence ledger.
        caused_probe: Whether the response led to a probe; not set by the
            current pipeline.
        caused_patch: Whether a patch-planning response produced the patch
            that was applied.
        improved_frontier: Whether a patch from this call moved the failure
            frontier forward, or ``None`` if that was never measured.
    """

    task: ModelTask
    tier: ModelTier
    input_size: int
    response_size: int
    latency_seconds: float
    parsed: bool
    provider_requests: int = 1
    changed_hypothesis: bool = False
    caused_probe: bool = False
    caused_patch: bool = False
    improved_frontier: bool | None = None


class ModelClient(Protocol):
    """Interface every model client must offer to the router."""

    def complete_json(
        self,
        *,
        task: ModelTask,
        tier: ModelTier,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: float,
        allow_repair_retry: bool = True,
    ) -> ModelResponse:
        """Send one task to the model and return its validated JSON answer.

        Args:
            task: The reasoning job, which selects the JSON contract.
            tier: Which configured model to use.
            system_prompt: Task instructions, including the untrusted-content
                warning.
            user_prompt: The Case-specific context for the task.
            timeout_seconds: Time limit for each HTTP request.
            allow_repair_retry: Whether one follow-up request may be sent when
                the first answer breaks the JSON contract.

        Returns:
            The parsed and validated response.

        Raises:
            ModelError: If the provider cannot be reached or fails.
            ModelProtocolError: If the answer breaks the response contract.
        """
        ...
