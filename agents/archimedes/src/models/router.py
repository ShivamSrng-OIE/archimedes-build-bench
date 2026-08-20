"""Budgeted FAST/STRONG routing with explicit bounded escalation conditions."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import ModelBackend, ModelTierMode, RuntimeConfig
from ..context.token_budget import UsageBudget, approximate_tokens
from .base import ModelCallRecord, ModelClient, ModelResponse, ModelTask, ModelTier
from .openai_compatible import OpenAICompatibleModelClient


@dataclass(slots=True)
class EscalationSignals:
    critical_confidence_low: bool = False
    high_hypothesis_entropy: bool = False
    sample_disagreement: bool = False
    semantic_anchor_unknown: bool = False
    novel_multi_file_repair: bool = False
    unexplained_previous_failure: bool = False
    ood_without_evidence: bool = False

    def requires_strong(self) -> bool:
        return any((
            self.critical_confidence_low, self.high_hypothesis_entropy,
            self.sample_disagreement, self.semantic_anchor_unknown,
            self.novel_multi_file_repair, self.unexplained_previous_failure,
            self.ood_without_evidence,
        ))


@dataclass(slots=True)
class ModelRouter:
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
    """Construct real local/remote adapters; mock fixtures are injected by tests explicitly."""
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
