"""Tests for the model client layer, the JSON reply protocol, routing, budgets and logging.

They cover the OpenAI-compatible adapter (redirects, size limits, repair retries,
bad usage numbers), parse_model_json() limits, the router's tier choice, the
UsageBudget call and cost limits, and secret redaction in logs and traces.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from http.client import HTTPMessage
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

from src.context.token_budget import UsageBudget
from src.errors import BudgetExhaustedError, ModelProtocolError
from src.logging_utils import redact_secrets
from src.models.base import ModelTask, ModelTier
from src.models.json_protocol import parse_model_json
from src.models.openai_compatible import OpenAICompatibleModelClient, _NoRedirectHandler
from src.models.prompts import system_prompt
from src.models.router import EscalationSignals, ModelRouter
from src.observability import TraceRecorder

from .mock_model import MockModelClient

FAILURE_RESPONSE = {
    "primary_failure": "bad flag",
    "failure_stage": "compile",
    "claims": [],
    "hypotheses": [],
    "recommended_files": [],
}


class ModelsLoggingTests(unittest.TestCase):
    """Check model adapters, reply parsing, routing, budgets and redaction."""

    def test_openai_adapter_rejects_redirects_before_resending_credentials(
        self,
    ) -> None:
        """Check that the adapter refuses HTTP redirects instead of following them.

        Following a redirect would send the bearer token to whatever host the
        provider points at.
        """
        redirected = _NoRedirectHandler().redirect_request(
            Request("https://provider.example/v1/chat/completions"),
            BytesIO(),
            302,
            "Found",
            HTTPMessage(),
            "https://other.example/collect",
        )
        self.assertIsNone(redirected)

    def test_json_protocol_extracts_fenced_object(self) -> None:
        """Check that a JSON object wrapped in a ```json fence is still parsed."""
        import json

        value = parse_model_json(
            f"```json\n{json.dumps(FAILURE_RESPONSE)}\n```", ModelTask.FAILURE_ANALYSIS
        )
        self.assertEqual(value["primary_failure"], "bad flag")

    def test_json_protocol_rejects_dangerous_patch_operation(self) -> None:
        """Check that a patch plan asking for a "shell" operation is refused.

        Only the known edit operations may reach the executor.
        """
        raw = '{"repair_family":"x","rationale":"x","assumptions":[],"edits":[{"operation":"shell"}]}'
        with self.assertRaises(ModelProtocolError):
            parse_model_json(raw, ModelTask.PATCH_PLANNING)

    def test_json_protocol_rejects_wrong_failure_analysis_types(self) -> None:
        """Check that a failure analysis with a string where a list belongs is refused."""
        malformed = dict(FAILURE_RESPONSE)
        malformed["recommended_files"] = "Makefile"
        with self.assertRaises(ModelProtocolError):
            parse_model_json(json.dumps(malformed), ModelTask.FAILURE_ANALYSIS)

    def test_json_protocol_bounds_analysis_arrays_and_patch_assumptions(self) -> None:
        """Check that 51 recommended files or 21 patch assumptions exceed the reply limits.

        The limits keep a runaway reply from flooding later prompts and logs.
        """
        oversized_analysis = dict(FAILURE_RESPONSE)
        oversized_analysis["recommended_files"] = [
            f"src/{index}.c" for index in range(51)
        ]
        with self.assertRaises(ModelProtocolError):
            parse_model_json(json.dumps(oversized_analysis), ModelTask.FAILURE_ANALYSIS)

        oversized_patch = {
            "repair_family": "source_port",
            "rationale": "bounded repair",
            "assumptions": [f"assumption {index}" for index in range(21)],
            "edits": [
                {
                    "operation": "replace_lines",
                    "path": "src/a.c",
                    "expected_text": "old",
                    "replacement": "new",
                    "rationale": "portable",
                }
            ],
        }
        with self.assertRaises(ModelProtocolError):
            parse_model_json(json.dumps(oversized_patch), ModelTask.PATCH_PLANNING)

    def test_json_protocol_rejects_nonfinite_json_numbers(self) -> None:
        """Check that NaN, which Python's json module accepts by default, is refused."""
        raw = (
            '{"primary_failure":"x","failure_stage":"compile","claims":[],'
            '"hypotheses":[NaN],"recommended_files":[]}'
        )
        with self.assertRaises(ModelProtocolError):
            parse_model_json(raw, ModelTask.FAILURE_ANALYSIS)

    def test_json_protocol_rejects_excessive_nesting(self) -> None:
        """Check that 2,000 levels of nested arrays raise ModelProtocolError, not RecursionError."""
        raw = '{"value":' + "[" * 2_000 + "0" + "]" * 2_000 + "}"
        with self.assertRaises(ModelProtocolError):
            parse_model_json(raw, ModelTask.FAILURE_ANALYSIS)

    def test_json_protocol_requires_complete_bounded_patch_edits(self) -> None:
        """Check that an edit without expected_text is refused.

        Without the expected text the executor could not confirm where the edit goes.
        """
        raw = (
            '{"repair_family":"source_port","rationale":"x","assumptions":["a"],'
            '"edits":[{"operation":"replace_lines","path":"a.c","replacement":"x",'
            '"rationale":"x"}]}'
        )
        with self.assertRaises(ModelProtocolError):
            parse_model_json(raw, ModelTask.PATCH_PLANNING)

    def test_patch_prompt_states_exact_machine_contract(self) -> None:
        """Check that the patch-planning system prompt names every required key and the edit limit.

        The prompt and the parser must agree, or valid-looking replies get rejected.
        """
        prompt = system_prompt(ModelTask.PATCH_PLANNING)
        for required in (
            "repair_family",
            "rationale",
            "assumptions",
            "edits",
            "expected_text",
        ):
            self.assertIn(required, prompt)
        self.assertIn("one to five", prompt)

    def test_openai_adapter_repair_prompt_names_protocol_error(self) -> None:
        """Check the single repair retry after a reply that is missing required keys.

        The retry must quote the protocol error, and the first request must use
        max_completion_tokens 8192, store=False, and no temperature or response_format.
        """
        class StubClient(OpenAICompatibleModelClient):
            """Adapter that answers from memory and records every request payload.

            The first reply lacks the edits key; the second is a complete patch plan.
            """

            def __init__(self) -> None:
                """Set up the adapter with placeholder settings and an empty payload list.

                Returns:
                    None.
                """
                super().__init__(
                    base_url="http://127.0.0.1:1/v1",
                    api_key=None,
                    fast_model="fast",
                    strong_model="strong",
                )
                self.payloads: list[dict[str, object]] = []

            def _request(
                self, payload: dict[str, object], timeout: float
            ) -> dict[str, object]:
                """Record the payload and return a canned chat completion.

                Args:
                    payload: The request body the adapter would have sent.
                    timeout: The request timeout in seconds; ignored.

                Returns:
                    A chat completion whose content is an incomplete patch plan on
                    the first call and a complete one afterwards.
                """
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
                return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}

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
        self.assertEqual(client.payloads[0]["max_completion_tokens"], 8192)
        self.assertIs(client.payloads[0]["store"], False)
        self.assertNotIn("temperature", client.payloads[0])
        self.assertNotIn("response_format", client.payloads[0])
        messages = client.payloads[-1]["messages"]
        assert isinstance(messages, list)
        correction = messages[-1]
        assert isinstance(correction, dict)
        self.assertIn("missing required keys", str(correction["content"]))

    def test_openai_adapter_ignores_invalid_provider_usage_values(self) -> None:
        """Check that a non-numeric or negative usage count falls back to the local token estimate.

        Bad provider numbers must not corrupt the usage budget.
        """
        class StubClient(OpenAICompatibleModelClient):
            """Adapter that returns a valid analysis with nonsense usage numbers."""

            def __init__(self) -> None:
                """Set up the adapter with placeholder endpoint and model names.

                Returns:
                    None.
                """
                super().__init__(
                    base_url="http://127.0.0.1:1/v1",
                    api_key=None,
                    fast_model="fast",
                    strong_model="strong",
                )

            def _request(
                self, payload: dict[str, object], timeout: float
            ) -> dict[str, object]:
                """Return a canned completion whose usage counts are invalid.

                Args:
                    payload: The request body; ignored.
                    timeout: The request timeout in seconds; ignored.

                Returns:
                    A chat completion with FAILURE_RESPONSE as content, a string
                    prompt_tokens and a negative completion_tokens.
                """
                del payload, timeout
                return {
                    "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(FAILURE_RESPONSE)}}],
                    "usage": {"prompt_tokens": "not-a-number", "completion_tokens": -4},
                }

        response = StubClient().complete_json(
            task=ModelTask.FAILURE_ANALYSIS,
            tier=ModelTier.FAST,
            system_prompt="system",
            user_prompt="input",
            timeout_seconds=1,
        )
        self.assertEqual(response.input_tokens, 2)
        self.assertGreater(response.output_tokens, 0)

    def test_openai_adapter_rejects_oversized_provider_response(self) -> None:
        """Check that _request() refuses a response body larger than its read limit.

        A provider that keeps sending data must not exhaust the Agent's memory.
        """
        class OversizedResponse:
            """Stand-in HTTP response that fills every read() to the requested size."""

            def __enter__(self):
                """Enter the context manager.

                Returns:
                    This response object.
                """
                return self

            def __exit__(self, *_: object) -> None:
                """Leave the context manager without suppressing exceptions.

                Args:
                    *_: The exception type, value and traceback; ignored.

                Returns:
                    None.
                """
                return

            def read(self, size: int) -> bytes:
                """Return as many bytes as were asked for, so the body never ends.

                Args:
                    size: The number of bytes requested.

                Returns:
                    Exactly size bytes of b"x".
                """
                return b"x" * size

        client = OpenAICompatibleModelClient(
            base_url="http://127.0.0.1:1/v1",
            api_key=None,
            fast_model="fast",
            strong_model="strong",
        )
        with patch(
            "src.models.openai_compatible._NO_REDIRECT_OPENER.open",
            return_value=OversizedResponse(),
        ), self.assertRaises(ModelProtocolError):
            client._request({"model": "fast"}, 1)

    def test_openai_adapter_rejects_oversized_message_before_repair_retry(self) -> None:
        """Check that a reply over 100,000 characters fails at once without a repair retry.

        Retrying would only spend another request on a reply that can never be accepted.
        """
        class StubClient(OpenAICompatibleModelClient):
            """Adapter that counts requests and always returns an oversized message."""

            def __init__(self) -> None:
                """Set up the adapter with placeholder settings and a zero request count.

                Returns:
                    None.
                """
                super().__init__(
                    base_url="http://127.0.0.1:1/v1",
                    api_key=None,
                    fast_model="fast",
                    strong_model="strong",
                )
                self.requests = 0

            def _request(
                self, payload: dict[str, object], timeout: float
            ) -> dict[str, object]:
                """Count the request and return a completion with 100,001 characters.

                Args:
                    payload: The request body; ignored.
                    timeout: The request timeout in seconds; ignored.

                Returns:
                    A chat completion whose content is too long to accept.
                """
                del payload, timeout
                self.requests += 1
                return {"choices": [{"finish_reason": "stop", "message": {"content": "x" * 100_001}}]}

        client = StubClient()
        with self.assertRaisesRegex(ModelProtocolError, "message content exceeds"):
            client.complete_json(
                task=ModelTask.FAILURE_ANALYSIS,
                tier=ModelTier.FAST,
                system_prompt="system",
                user_prompt="input",
                timeout_seconds=1,
            )
        self.assertEqual(client.requests, 1)

    def test_mock_model_is_deterministic_and_records_routing(self) -> None:
        """Check that the mock client returns its canned reply and records task and tier."""
        client = MockModelClient({ModelTask.FAILURE_ANALYSIS: FAILURE_RESPONSE})
        response = client.complete_json(
            task=ModelTask.FAILURE_ANALYSIS,
            tier=ModelTier.FAST,
            system_prompt="system",
            user_prompt="input",
            timeout_seconds=1,
        )
        self.assertEqual(response.data["primary_failure"], "bad flag")
        self.assertEqual(client.calls[0].task, ModelTask.FAILURE_ANALYSIS)
        self.assertEqual(client.calls[0].tier, ModelTier.FAST)

    def test_router_escalates_only_on_explicit_signal(self) -> None:
        """Check that the router stays on the fast tier until a low-confidence signal.

        Strong-tier calls cost more and are capped, so they must not happen by default.
        """
        client = MockModelClient(
            {ModelTask.FAILURE_ANALYSIS: [FAILURE_RESPONSE, FAILURE_RESPONSE]}
        )
        router = ModelRouter(client, UsageBudget(5, 3, 10_000), 1.0)
        router.complete(
            task=ModelTask.FAILURE_ANALYSIS, system_prompt="s", user_prompt="u"
        )
        router.complete(
            task=ModelTask.FAILURE_ANALYSIS,
            system_prompt="s",
            user_prompt="u",
            signals=EscalationSignals(critical_confidence_low=True),
        )
        self.assertEqual(
            [call.tier for call in client.calls], [ModelTier.FAST, ModelTier.STRONG]
        )

    def test_router_can_force_strong_tier_for_controlled_comparison(self) -> None:
        """Check that forced_tier=STRONG sends even a first call to the strong tier.

        Experiments use this to compare tiers on the same Cases.
        """
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
        """Check that a tiny cost limit blocks a call, and that no rates means no estimate."""
        budget = UsageBudget(
            5,
            3,
            10_000,
            max_cost_usd=0.000001,
            input_rate_per_million=10,
            output_rate_per_million=10,
        )
        with self.assertRaises(BudgetExhaustedError):
            budget.authorize(strong=False, estimated_input_tokens=1000)
        unknown = UsageBudget(5, 3, 10_000)
        self.assertIsNone(unknown.estimated_cost)

    def test_budget_reserves_and_records_provider_retries(self) -> None:
        """Check that a call reserving a retry counts as two calls against the budget.

        Otherwise a hidden provider retry could push usage past the call limit.
        """
        budget = UsageBudget(2, 2, 10_000)
        budget.authorize(strong=True, estimated_input_tokens=10, reserve_calls=2)
        budget.record(strong=True, input_tokens=20, output_tokens=5, calls=2)
        self.assertEqual((budget.calls, budget.strong_calls), (2, 2))
        with self.assertRaises(BudgetExhaustedError):
            budget.authorize(strong=False, estimated_input_tokens=1)

    def test_router_disables_hidden_retry_for_the_last_allowed_request(self) -> None:
        """Check that the router turns off the repair retry when only one call is left.

        With a two-call budget the first request may retry and the second may not.
        """
        class RetryAwareMock(MockModelClient):
            """Mock client that records the allow_repair_retry flag of each call."""

            def __init__(self) -> None:
                """Set up the mock with a canned failure analysis and no recorded flags.

                Returns:
                    None.
                """
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
                """Record the retry flag, then answer like the normal mock client.

                Args:
                    task: The kind of model request.
                    tier: The model tier the router chose.
                    system_prompt: The system prompt text.
                    user_prompt: The user prompt text.
                    timeout_seconds: The request timeout in seconds.
                    allow_repair_retry: Whether the client may send one repair retry.

                Returns:
                    The mock client's response for the task.
                """
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
        """Check that bearer tokens and KEY=value secrets are removed from log text."""
        value = redact_secrets("Authorization: Bearer secret API_KEY=abc? token=def")
        self.assertNotIn("secret", value)
        self.assertNotIn("API_KEY=abc", value)

    def test_local_trace_is_bounded_and_redacts_sensitive_attributes(self) -> None:
        """Check that the trace stops at max_events and hides api_key and token= values.

        Token counts must stay readable even though their names contain "token".
        """
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
            events = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
        self.assertEqual(len(events), 1)
        attributes = events[0]["attributes"]
        self.assertEqual(attributes["api_key"], "[REDACTED]")
        self.assertNotIn("hidden", attributes["detail"])
        self.assertEqual(attributes["input_tokens"], 123)
        self.assertEqual(attributes["output_tokens"], 45)

    def test_local_trace_redacts_nested_secrets_and_serializes_nonfinite_values(
        self,
    ) -> None:
        """Check that secrets inside nested dicts are hidden and NaN is written as "nan".

        Each trace line must stay valid JSON, which has no NaN literal.
        """
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "execution-trace.jsonl"
            trace = TraceRecorder(path, "case-1")
            trace.record(
                "model",
                "completed",
                nested={"credential": "plain-secret", "score": float("nan")},
            )
            event = json.loads(path.read_text(encoding="utf-8"))
        nested = event["attributes"]["nested"]
        self.assertEqual(nested["credential"], "[REDACTED]")
        self.assertEqual(nested["score"], "nan")


if __name__ == "__main__":
    unittest.main()
