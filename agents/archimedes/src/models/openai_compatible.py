"""Minimal standard-library client for local or remote OpenAI-compatible servers."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from ..errors import ModelError, ModelProtocolError
from ..schemas import JSONValue
from .base import ModelResponse, ModelTask, ModelTier
from .json_protocol import parse_model_json


class OpenAICompatibleModelClient:
    def __init__(self, *, base_url: str, api_key: str | None, fast_model: str, strong_model: str):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._models = {ModelTier.FAST: fast_model, ModelTier.STRONG: strong_model}

    def _request(self, payload: dict[str, JSONValue], timeout: float) -> dict[str, JSONValue]:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = urllib.request.Request(
            f"{self._base_url}/chat/completions", data=data, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(5_000_000)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ModelError(f"OpenAI-compatible request failed: {type(exc).__name__}") from exc
        try:
            result = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelProtocolError("provider response is not valid UTF-8 JSON") from exc
        if not isinstance(result, dict):
            raise ModelProtocolError("provider response root must be an object")
        return result

    @staticmethod
    def _content(response: dict[str, JSONValue]) -> str:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ModelProtocolError("provider response has no choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ModelProtocolError("provider response has no message content")
        content = message.get("content")
        if not isinstance(content, str):
            raise ModelProtocolError("provider response has no message content")
        return content

    def complete_json(
        self,
        *,
        task: ModelTask,
        tier: ModelTier,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: float,
    ) -> ModelResponse:
        model = self._models[tier]
        messages: list[JSONValue] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        payload: dict[str, JSONValue] = {
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": messages,
        }
        started = time.monotonic()
        response = self._request(payload, timeout_seconds)
        raw = self._content(response)
        try:
            parsed = parse_model_json(raw, task)
        except ModelProtocolError:
            repair_payload = dict(payload)
            repair_payload["messages"] = [
                *messages,
                {"role": "assistant", "content": raw},
                {"role": "user", "content": "Return only one corrected JSON object satisfying every required key."},
            ]
            response = self._request(repair_payload, timeout_seconds)
            raw = self._content(response)
            parsed = parse_model_json(raw, task)
        latency = time.monotonic() - started
        usage = response.get("usage", {})
        fallback_input = max(1, (len(system_prompt) + len(user_prompt)) // 4)
        fallback_output = max(1, len(raw) // 4)
        input_value = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        output_value = usage.get("completion_tokens") if isinstance(usage, dict) else None
        input_tokens = int(input_value) if isinstance(input_value, (str, int, float)) else fallback_input
        output_tokens = int(output_value) if isinstance(output_value, (str, int, float)) else fallback_output
        return ModelResponse(parsed, raw, model, input_tokens, output_tokens, latency)
