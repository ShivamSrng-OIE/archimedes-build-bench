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

    def test_secret_redaction(self) -> None:
        value = redact_secrets("Authorization: Bearer secret API_KEY=abc? token=def")
        self.assertNotIn("secret", value)
        self.assertNotIn("API_KEY=abc", value)

    def test_local_trace_is_bounded_and_redacts_sensitive_attributes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "execution-trace.jsonl"
            trace = TraceRecorder(path, "case-1", max_events=1)
            trace.record("ingestion", "started", api_key="plain-secret", detail="token=hidden")
            trace.record("finalize", "ignored")
            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(events), 1)
        attributes = events[0]["attributes"]
        self.assertEqual(attributes["api_key"], "[REDACTED]")
        self.assertNotIn("hidden", attributes["detail"])


if __name__ == "__main__":
    unittest.main()
