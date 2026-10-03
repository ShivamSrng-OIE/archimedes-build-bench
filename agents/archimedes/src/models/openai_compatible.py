"""Minimal standard-library client for local or remote OpenAI-compatible servers."""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from typing import IO

from ..errors import ModelError, ModelProtocolError
from ..json_safety import reject_excessive_json_nesting
from ..schemas import JSONValue
from .base import ModelResponse, ModelTask, ModelTier
from .json_protocol import parse_model_json


_MAX_RESPONSE_BYTES = 5_000_000
_MAX_MODEL_CONTENT_CHARACTERS = 100_000


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects so bearer credentials never cross an endpoint boundary."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler())


def _reject_nonfinite_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _usage_tokens(value: JSONValue, fallback_characters: int) -> int:
    """Accept only exact non-negative token counts; otherwise use a safe estimate."""
    fallback = max(1, fallback_characters // 4)
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value if value >= 0 else fallback
    if isinstance(value, float):
        return int(value) if value >= 0 and value.is_integer() else fallback
    if isinstance(value, str):
        stripped = value.strip()
        return (
            int(stripped)
            if len(stripped) <= 20 and stripped.isascii() and stripped.isdecimal()
            else fallback
        )
    return fallback


class OpenAICompatibleModelClient:
    def __init__(
        self, *, base_url: str, api_key: str | None, fast_model: str, strong_model: str
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._models = {ModelTier.FAST: fast_model, ModelTier.STRONG: strong_model}

    def _request(
        self, payload: dict[str, JSONValue], timeout: float
    ) -> dict[str, JSONValue]:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with _NO_REDIRECT_OPENER.open(request, timeout=timeout) as response:
                body = response.read(_MAX_RESPONSE_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ModelError(
                f"OpenAI-compatible request failed: {type(exc).__name__}"
            ) from exc
        if len(body) > _MAX_RESPONSE_BYTES:
            raise ModelProtocolError(
                "provider response exceeds the maximum allowed size"
            )
        try:
            text = body.decode("utf-8")
            reject_excessive_json_nesting(text)
            result = json.loads(text, parse_constant=_reject_nonfinite_constant)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
            RecursionError,
        ) as exc:
            raise ModelProtocolError(
                "provider response is not valid UTF-8 JSON"
            ) from exc
        if not isinstance(result, dict):
            raise ModelProtocolError("provider response root must be an object")
        return result

    @staticmethod
    def _content(response: dict[str, JSONValue]) -> str:
        choices = response.get("choices")
        if (
            not isinstance(choices, list)
            or not choices
            or not isinstance(choices[0], dict)
        ):
            raise ModelProtocolError("provider response has no choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ModelProtocolError("provider response has no message content")
        content = message.get("content")
        if not isinstance(content, str):
            raise ModelProtocolError("provider response has no message content")
        if len(content) > _MAX_MODEL_CONTENT_CHARACTERS:
            raise ModelProtocolError(
                "provider message content exceeds the maximum allowed size"
            )
        return content

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
        model = self._models[tier]
        messages: list[JSONValue] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        payload: dict[str, JSONValue] = {
            "model": model,
            "temperature": 0,
            "max_tokens": 1000,
            "response_format": {"type": "json_object"},
            "messages": messages,
        }
        started = time.monotonic()
        response = self._request(payload, timeout_seconds)
        raw = self._content(response)
        responses = [(response, len(system_prompt) + len(user_prompt), len(raw))]
        try:
            parsed = parse_model_json(raw, task)
        except ModelProtocolError as error:
            if not allow_repair_retry:
                raise
            repair_payload = dict(payload)
            repair_payload["messages"] = [
                *messages,
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        f"The previous object was rejected because: {error}. "
                        "Return only one corrected JSON object satisfying every required field "
                        "and type in the system contract. Do not rename or omit required fields."
                    ),
                },
            ]
            response = self._request(repair_payload, timeout_seconds)
            raw = self._content(response)
            responses.append((response, len(json.dumps(repair_payload)), len(raw)))
            parsed = parse_model_json(raw, task)
        latency = time.monotonic() - started
        input_tokens = 0
        output_tokens = 0
        for item, input_characters, output_characters in responses:
            usage = item.get("usage", {})
            input_value = (
                usage.get("prompt_tokens") if isinstance(usage, dict) else None
            )
            output_value = (
                usage.get("completion_tokens") if isinstance(usage, dict) else None
            )
            input_tokens += _usage_tokens(input_value, input_characters)
            output_tokens += _usage_tokens(output_value, output_characters)
        return ModelResponse(
            parsed,
            raw,
            model,
            input_tokens,
            output_tokens,
            latency,
            provider_requests=len(responses),
        )
