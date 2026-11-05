"""Small ``urllib`` client for OpenAI-compatible chat-completion endpoints.

It serves the hosted organizer endpoint and the local and remote development
backends with the same strict request contract, and uses no provider SDK.
"""

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
_MAX_REQUEST_BYTES = 16 * 1024 * 1024


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that refuses every redirect.

    Following a redirect would resend the ``Authorization`` header to whatever
    host the server names, so the bearer token could leak. Refusing makes the
    3xx response surface as an HTTP error instead.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> None:
        """Refuse the redirect so urllib reports the 3xx status as an error.

        Args:
            req: The original request.
            fp: The response body stream.
            code: The HTTP redirect status code.
            msg: The HTTP reason phrase.
            headers: The response headers.
            newurl: The redirect target, which is never contacted.

        Returns:
            None, which tells urllib not to follow the redirect.
        """
        del req, fp, code, msg, headers, newurl


_NO_REDIRECT_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), _NoRedirectHandler()
)


def _reject_nonfinite_constant(value: str) -> None:
    """Refuse ``NaN``, ``Infinity`` and ``-Infinity`` while decoding JSON.

    Args:
        value: The constant name the decoder found.

    Returns:
        Never returns.

    Raises:
        ValueError: Always.
    """
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _usage_tokens(value: JSONValue, fallback_characters: int) -> int:
    """Read a provider token count, falling back to an estimate when it is unusable.

    Only exact non-negative integers count, whether sent as a number or as a
    short decimal string. Booleans, negatives, fractions and anything else fall
    back to about four characters per token, so a broken usage field cannot
    hide spending from the budget.

    Args:
        value: The ``prompt_tokens`` or ``completion_tokens`` field, or
            ``None`` when it is missing.
        fallback_characters: Characters of text the count is meant to cover,
            used for the estimate.

    Returns:
        The reported count, or ``max(1, fallback_characters // 4)``.
    """
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
    """Client that sends one task at a time to ``POST {base}/chat/completions``.

    Every request body holds exactly four fields: ``model``, ``messages``,
    ``max_completion_tokens`` set to 8192, and ``store`` set to false. The
    transport refuses redirects, ignores proxy settings from the environment,
    makes no automatic retries, and caps request and response sizes. An answer
    is accepted only when there is exactly one choice whose ``finish_reason``
    is ``"stop"``, with no refusal and non-empty content. The bearer token is
    sent only in the ``Authorization`` header and never appears in logs, error
    messages or traces.
    """

    def __init__(
        self, *, base_url: str, api_key: str | None, fast_model: str, strong_model: str
    ):
        """Store the endpoint, credential and per-tier model names.

        Args:
            base_url: Endpoint root such as ``https://host/v1``; a trailing
                ``/`` is removed.
            api_key: Bearer token, or ``None`` to send no ``Authorization``
                header.
            fast_model: Model name sent for FAST-tier calls.
            strong_model: Model name sent for STRONG-tier calls.

        Returns:
            None.
        """
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._models = {ModelTier.FAST: fast_model, ModelTier.STRONG: strong_model}

    def _request(
        self, payload: dict[str, JSONValue], timeout: float
    ) -> dict[str, JSONValue]:
        """Send one chat-completion request and decode the JSON reply.

        Errors carry only the exception type name and, when there is one, the
        HTTP status. The original exception is dropped (``from None``) so no
        header or URL detail can reach a log.

        Args:
            payload: The request body to send as JSON.
            timeout: Socket timeout in seconds.

        Returns:
            The decoded top-level JSON object.

        Raises:
            ModelProtocolError: If the request is larger than 16 MiB, the
                response is larger than 5,000,000 bytes, or the response is not
                a UTF-8 JSON object within the nesting limit and free of
                non-finite numbers.
            ModelError: If the connection fails, times out, is redirected or
                returns an HTTP error status.
        """
        data = json.dumps(payload).encode("utf-8")
        if len(data) > _MAX_REQUEST_BYTES:
            raise ModelProtocolError("model request exceeds the maximum allowed size")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
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
            status = getattr(exc, "code", None)
            raise ModelError(
                f"OpenAI-compatible request failed: {type(exc).__name__}"
                + (f" HTTP {status}" if type(status) is int else "")
            ) from None
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
        """Pull the message text out of a chat-completion reply.

        Args:
            response: The decoded reply object.

        Returns:
            The content string of the single choice.

        Raises:
            ModelProtocolError: If there is not exactly one choice, the choice
                has no message, ``finish_reason`` is not ``"stop"``, the model
                refused, the content is empty or not a string, or it is longer
                than 100,000 characters.
        """
        choices = response.get("choices")
        if (
            not isinstance(choices, list)
            or len(choices) != 1
            or not isinstance(choices[0], dict)
        ):
            raise ModelProtocolError("provider response has no choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ModelProtocolError("provider response has no message content")
        if choices[0].get("finish_reason") != "stop":
            raise ModelProtocolError("provider response did not finish normally")
        if message.get("refusal"):
            raise ModelProtocolError("provider response was refused")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
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
        """Ask the model for one task and return its validated JSON answer.

        If the first answer breaks the task's JSON contract and a retry is
        allowed, one follow-up request is sent that includes the rejected
        answer and the reason, asking for a corrected object. Token usage is
        summed over both requests.

        Args:
            task: The reasoning job, which selects the JSON contract.
            tier: Which configured model name to send.
            system_prompt: Task instructions for the ``system`` message.
            user_prompt: Case-specific context for the ``user`` message.
            timeout_seconds: Socket timeout for each HTTP request.
            allow_repair_retry: Whether the one follow-up request is allowed.

        Returns:
            The parsed answer with its raw text, token counts, latency and the
            number of HTTP requests used.

        Raises:
            ModelError: If a request fails at the transport or HTTP level.
            ModelProtocolError: If a reply breaks the response contract, or the
                answer still breaks the JSON contract after the allowed retry.
        """
        model = self._models[tier]
        messages: list[JSONValue] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        payload: dict[str, JSONValue] = {
            "model": model,
            "max_completion_tokens": 8192,
            "store": False,
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
