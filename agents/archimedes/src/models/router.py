"""Choose the FAST or STRONG model tier for each call and enforce the run's budgets.

Every call is checked against the call, strong-call, token and optional cost
limits before it is sent, and its usage is recorded afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import ModelBackend, ModelTierMode, RuntimeConfig
from ..context.token_budget import UsageBudget, approximate_tokens
from .base import ModelCallRecord, ModelClient, ModelResponse, ModelTask, ModelTier
from .openai_compatible import OpenAICompatibleModelClient


@dataclass(slots=True)
class EscalationSignals:
    """Reasons a call may need the STRONG tier.

    Any single true field is enough to escalate. With no signals the call uses
    the FAST tier. The controller currently sets only ``ood_without_evidence``,
    for patch planning when the target architecture is unknown.

    Attributes:
        critical_confidence_low: A claim the repair depends on has low
            confidence.
        high_hypothesis_entropy: Several hypotheses remain about equally likely.
        sample_disagreement: Earlier model answers disagree with each other.
        semantic_anchor_unknown: The purpose of a hazard construct is unclear.
        novel_multi_file_repair: The repair would touch several files in a way
            no rule covers.
        unexplained_previous_failure: An earlier attempt failed for an unknown
            reason.
        ood_without_evidence: The target architecture is unfamiliar and there
            is little evidence about it.
    """

    critical_confidence_low: bool = False
    high_hypothesis_entropy: bool = False
    sample_disagreement: bool = False
    semantic_anchor_unknown: bool = False
    novel_multi_file_repair: bool = False
    unexplained_previous_failure: bool = False
    ood_without_evidence: bool = False

    def requires_strong(self) -> bool:
        """Tell whether any escalation signal is set.

        Returns:
            True if at least one field is true.
        """
        return any((
            self.critical_confidence_low, self.high_hypothesis_entropy,
            self.sample_disagreement, self.semantic_anchor_unknown,
            self.novel_multi_file_repair, self.unexplained_previous_failure,
            self.ood_without_evidence,
        ))


@dataclass(slots=True)
class ModelRouter:
    """Send model calls through one client while enforcing the usage budget.

    Attributes:
        client: The model client that performs the requests.
        budget: Call, token and cost limits plus the usage so far.
        timeout_seconds: Socket timeout for each request.
        forced_tier: Tier pinned by ``ARCHIMEDES_MODEL_TIER``, or ``None`` to
            choose per call.
        records: One metrics record per completed call, in order.
    """

    client: ModelClient
    budget: UsageBudget
    timeout_seconds: float
    forced_tier: ModelTier | None = None
    records: list[ModelCallRecord] = field(default_factory=list)

    def complete(
        self,
        *,
        task: ModelTask,
        system_prompt: str,
        user_prompt: str,
        signals: EscalationSignals | None = None,
    ) -> ModelResponse:
        """Pick a tier, check the budget, make the call and record its usage.

        When at least two requests remain in the budget, two are reserved so
        the client may send one JSON repair retry; otherwise only one is
        reserved and the retry is turned off.

        Args:
            task: The reasoning job to perform.
            system_prompt: Task instructions for the model.
            user_prompt: Case-specific context for the model.
            signals: Escalation signals for this call, or ``None`` for none.

        Returns:
            The client's validated response.

        Raises:
            BudgetExhaustedError: If the call would exceed the call,
                strong-call, token or cost limit.
            ModelError: If the client's request fails.
            ModelProtocolError: If the answer breaks its JSON contract.
        """
        tier = self.forced_tier or (
            ModelTier.STRONG if signals and signals.requires_strong() else ModelTier.FAST
        )
        estimated = approximate_tokens(system_prompt + user_prompt)
        remaining_requests = self.budget.max_calls - self.budget.calls
        if tier is ModelTier.STRONG:
            remaining_requests = min(
                remaining_requests,
                self.budget.max_strong_calls - self.budget.strong_calls,
            )
        reserved_requests = 2 if remaining_requests >= 2 else 1
        self.budget.authorize(
            strong=tier is ModelTier.STRONG,
            estimated_input_tokens=estimated,
            reserve_calls=reserved_requests,
        )
        response = self.client.complete_json(
            task=task,
            tier=tier,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            timeout_seconds=self.timeout_seconds,
            allow_repair_retry=reserved_requests == 2,
        )
        self.budget.record(
            strong=tier is ModelTier.STRONG,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            calls=response.provider_requests,
        )
        self.records.append(ModelCallRecord(
            task, tier, len(system_prompt) + len(user_prompt), len(response.raw_text),
            response.latency_seconds, response.parsed, response.provider_requests,
        ))
        return response


def create_runtime_router(config: RuntimeConfig) -> ModelRouter | None:
    """Create the model router described by the configuration.

    Tests that need a scripted model build their own ``ModelRouter`` with a
    fake client; nothing here creates one.

    Args:
        config: The runtime configuration.

    Returns:
        A router backed by ``OpenAICompatibleModelClient``, or ``None`` when the
        backend is ``mock`` and the run should use no model at all.
    """
    if config.model_backend is ModelBackend.MOCK:
        return None
    client = OpenAICompatibleModelClient(
        base_url=config.model_base_url,
        api_key=config.model_api_key,
        fast_model=config.fast_model,
        strong_model=config.strong_model,
    )
    budget = UsageBudget(
        max_calls=config.max_total_model_calls,
        max_strong_calls=config.max_strong_calls,
        max_tokens=config.max_total_tokens,
        max_cost_usd=config.max_estimated_cost_usd,
        input_rate_per_million=config.input_cost_per_million,
        output_rate_per_million=config.output_cost_per_million,
    )
    forced_tier = {
        ModelTierMode.AUTO: None,
        ModelTierMode.FAST: ModelTier.FAST,
        ModelTierMode.STRONG: ModelTier.STRONG,
    }[config.model_tier_mode]
    return ModelRouter(
        client=client,
        budget=budget,
        timeout_seconds=config.model_timeout_seconds,
        forced_tier=forced_tier,
    )
