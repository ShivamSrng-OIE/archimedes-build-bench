from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.context.token_budget import UsageBudget
from src.errors import BudgetExhaustedError, ModelProtocolError
from src.logging_utils import redact_secrets
from src.models.base import ModelTask, ModelTier
from src.models.json_protocol import parse_model_json
from src.models.mock import MockModelClient
from src.models.openai_compatible import OpenAICompatibleModelClient
from src.models.prompts import system_prompt
from src.models.router import EscalationSignals, ModelRouter
from src.observability import TraceRecorder


FAILURE_RESPONSE = {
    "primary_failure": "bad flag",
    "failure_stage": "compile",
    "claims": [],
    "hypotheses": [],
    "recommended_files": [],
}


class ModelsLoggingTests(unittest.TestCase):
    def test_json_protocol_extracts_fenced_object(self) -> None:
        import json

        value = parse_model_json(f"```json\n{json.dumps(FAILURE_RESPONSE)}\n```", ModelTask.FAILURE_ANALYSIS)
        self.assertEqual(value["primary_failure"], "bad flag")

    def test_json_protocol_rejects_dangerous_patch_operation(self) -> None:
        raw = '{"repair_family":"x","rationale":"x","assumptions":[],"edits":[{"operation":"shell"}]}'
        with self.assertRaises(ModelProtocolError):
            parse_model_json(raw, ModelTask.PATCH_PLANNING)

    def test_json_protocol_requires_complete_bounded_patch_edits(self) -> None:
        raw = (
            '{"repair_family":"source_port","rationale":"x","assumptions":["a"],'
            '"edits":[{"operation":"replace_lines","path":"a.c","replacement":"x",'
            '"rationale":"x"}]}'
        )
        with self.assertRaises(ModelProtocolError):
            parse_model_json(raw, ModelTask.PATCH_PLANNING)

    def test_patch_prompt_states_exact_machine_contract(self) -> None:
        prompt = system_prompt(ModelTask.PATCH_PLANNING)
        for required in ("repair_family", "rationale", "assumptions", "edits", "expected_text"):
            self.assertIn(required, prompt)
        self.assertIn("one to five", prompt)

    def test_openai_adapter_repair_prompt_names_protocol_error(self) -> None:
        class StubClient(OpenAICompatibleModelClient):
            def __init__(self) -> None:
                super().__init__(
                    base_url="http://127.0.0.1:1/v1",
                    api_key=None,
                    fast_model="fast",
                    strong_model="strong",
                )
                self.payloads: list[dict[str, object]] = []

            def _request(self, payload: dict[str, object], timeout: float) -> dict[str, object]:
                del timeout
                self.payloads.append(payload)
                content = (
                    '{"repair_family":"source_port","rationale":"x","assumptions":["a"]}'
                    if len(self.payloads) == 1
                    else '{"repair_family":"source_port","rationale":"x",'
                    '"assumptions":["a"],"edits":[{"operation":"replace_lines",'
                    '"path":"a.c","expected_text":"old","replacement":"new",'
                    '"rationale":"x"}]}'
                )
                return {"choices": [{"message": {"content": content}}]}

        client = StubClient()
        response = client.complete_json(
            task=ModelTask.PATCH_PLANNING,
            tier=ModelTier.FAST,
            system_prompt=system_prompt(ModelTask.PATCH_PLANNING),
            user_prompt="bounded context",
            timeout_seconds=1,
        )
        self.assertEqual(response.data["repair_family"], "source_port")
        self.assertEqual(response.provider_requests, 2)
        self.assertEqual(len(client.payloads), 2)
        messages = client.payloads[-1]["messages"]
        assert isinstance(messages, list)
        correction = messages[-1]
        assert isinstance(correction, dict)
        self.assertIn("missing required keys", str(correction["content"]))

    def test_mock_model_is_deterministic_and_records_routing(self) -> None:
        client = MockModelClient({ModelTask.FAILURE_ANALYSIS: FAILURE_RESPONSE})
        response = client.complete_json(
            task=ModelTask.FAILURE_ANALYSIS, tier=ModelTier.FAST,
            system_prompt="system", user_prompt="input", timeout_seconds=1,
        )
        self.assertEqual(response.data["primary_failure"], "bad flag")
        self.assertEqual(client.calls[0].task, ModelTask.FAILURE_ANALYSIS)
        self.assertEqual(client.calls[0].tier, ModelTier.FAST)

    def test_router_escalates_only_on_explicit_signal(self) -> None:
        client = MockModelClient({
            ModelTask.FAILURE_ANALYSIS: [FAILURE_RESPONSE, FAILURE_RESPONSE]
        })
        router = ModelRouter(client, UsageBudget(5, 3, 10_000), 1.0)
        router.complete(
            task=ModelTask.FAILURE_ANALYSIS, system_prompt="s", user_prompt="u"
        )
        router.complete(
            task=ModelTask.FAILURE_ANALYSIS, system_prompt="s", user_prompt="u",
            signals=EscalationSignals(critical_confidence_low=True),
        )
        self.assertEqual([call.tier for call in client.calls], [ModelTier.FAST, ModelTier.STRONG])

    def test_router_can_force_strong_tier_for_controlled_comparison(self) -> None:
        client = MockModelClient({ModelTask.FAILURE_ANALYSIS: FAILURE_RESPONSE})
        router = ModelRouter(
            client,
            UsageBudget(5, 3, 10_000),
            1.0,
            forced_tier=ModelTier.STRONG,
        )
        router.complete(
            task=ModelTask.FAILURE_ANALYSIS,
            system_prompt="system",
            user_prompt="case",
        )
        self.assertEqual(router.records[-1].tier, ModelTier.STRONG)

    def test_cost_budget_is_optional_and_bounded_when_configured(self) -> None:
        budget = UsageBudget(5, 3, 10_000, max_cost_usd=0.000001, input_rate_per_million=10, output_rate_per_million=10)
        with self.assertRaises(BudgetExhaustedError):
            budget.authorize(strong=False, estimated_input_tokens=1000)
        unknown = UsageBudget(5, 3, 10_000)
        self.assertIsNone(unknown.estimated_cost)

    def test_budget_reserves_and_records_provider_retries(self) -> None:
        budget = UsageBudget(2, 2, 10_000)
        budget.authorize(strong=True, estimated_input_tokens=10, reserve_calls=2)
        budget.record(strong=True, input_tokens=20, output_tokens=5, calls=2)
        self.assertEqual((budget.calls, budget.strong_calls), (2, 2))
        with self.assertRaises(BudgetExhaustedError):
            budget.authorize(strong=False, estimated_input_tokens=1)

    def test_router_disables_hidden_retry_for_the_last_allowed_request(self) -> None:
        class RetryAwareMock(MockModelClient):
            def __init__(self) -> None:
                super().__init__({ModelTask.FAILURE_ANALYSIS: FAILURE_RESPONSE})
                self.retry_flags: list[bool] = []

            def complete_json(
                self,
                *,
                task: ModelTask,
                tier: ModelTier,
                system_prompt: str,
                user_prompt: str,
                timeout_seconds: float,
                allow_repair_retry: bool = True,
            ):
                self.retry_flags.append(allow_repair_retry)
                return super().complete_json(
                    task=task,
                    tier=tier,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    timeout_seconds=timeout_seconds,
                    allow_repair_retry=allow_repair_retry,
                )

        client = RetryAwareMock()
        router = ModelRouter(client, UsageBudget(2, 2, 10_000), 1.0)
        for _ in range(2):
            router.complete(
                task=ModelTask.FAILURE_ANALYSIS,
                system_prompt="system",
                user_prompt="case",
            )
        self.assertEqual(client.retry_flags, [True, False])

    def test_secret_redaction(self) -> None:
        value = redact_secrets("Authorization: Bearer secret API_KEY=abc? token=def")
        self.assertNotIn("secret", value)
        self.assertNotIn("API_KEY=abc", value)

    def test_local_trace_is_bounded_and_redacts_sensitive_attributes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "execution-trace.jsonl"
            trace = TraceRecorder(path, "case-1", max_events=1)
            trace.record(
                "ingestion",
                "started",
                api_key="plain-secret",
                detail="token=hidden",
                input_tokens=123,
                output_tokens=45,
            )
            trace.record("finalize", "ignored")
            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(events), 1)
        attributes = events[0]["attributes"]
        self.assertEqual(attributes["api_key"], "[REDACTED]")
        self.assertNotIn("hidden", attributes["detail"])
        self.assertEqual(attributes["input_tokens"], 123)
        self.assertEqual(attributes["output_tokens"], 45)


if __name__ == "__main__":
    unittest.main()
