"""Scripted, offline stand-in for a model client, used by the tests.

``MockModelClient`` answers each model task with a fixed JSON fixture instead of calling
a real endpoint, and records every call so tests can check which tasks and tiers the
router asked for.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from src.errors import ModelError
from src.models.base import ModelCallRecord, ModelResponse, ModelTask, ModelTier
from src.models.json_protocol import parse_model_json
from src.schemas import JSONValue


class MockModelClient:
    """Fake model client that replays configured JSON answers for each task.

    Each task maps either to one fixture, which is returned on every call, or to a list of
    fixtures, which are returned in order, one per call. Answers go through the real
    ``parse_model_json`` check, so a fixture that lacks the keys a task requires fails the
    same way a bad model reply would. The class offers the same ``complete_json`` method as
    the real clients, so it can be handed to ``ModelRouter``.

    Attributes:
        calls: One ``ModelCallRecord`` per successful call, in call order.
    """

    def __init__(self, responses: Mapping[ModelTask, dict[str, JSONValue] | list[dict[str, JSONValue]]]):
        """Store the fixtures to replay.

        Args:
            responses: Maps each ``ModelTask`` to the JSON object to return for it, or to a
                list of objects to return one per call. Requesting a task that is not in the
                mapping raises ``ModelError``.

        Returns:
            None.
        """
        self._responses = dict(responses)
        self._indexes: dict[ModelTask, int] = {}
        self.calls: list[ModelCallRecord] = []

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
        """Return the next scripted answer for a task, as a real client would.

        The fixture is serialized with sorted keys and parsed back with ``parse_model_json``.
        Token counts are rough estimates of four characters per token (at least one), and the
        reported latency is always zero.

        Args:
            task: The model task being asked for. It picks the fixture.
            tier: The requested model tier. It is recorded and used to name the model
                ``mock-<tier>``.
            system_prompt: The system prompt. Only its length is used.
            user_prompt: The user prompt. Only its length is used.
            timeout_seconds: Accepted to match the real clients and otherwise ignored.
            allow_repair_retry: Accepted to match the real clients and otherwise ignored.

        Returns:
            A ``ModelResponse`` holding the parsed object, the raw JSON text, the mock model
            name and the estimated token counts.

        Raises:
            ModelError: If no fixture is configured for ``task``, or its fixture list has
                already been used up.
            ModelProtocolError: If the fixture lacks keys that ``parse_model_json`` requires
                for ``task``.
        """
        del allow_repair_retry
        if task not in self._responses:
            raise ModelError(f"no mock response configured for task {task.value}")
        fixture = self._responses[task]
        if isinstance(fixture, list):
            index = self._indexes.get(task, 0)
            if index >= len(fixture):
                raise ModelError(f"mock responses exhausted for task {task.value}")
            data = fixture[index]
            self._indexes[task] = index + 1
        else:
            data = fixture
        raw = json.dumps(data, sort_keys=True)
        parsed = parse_model_json(raw, task)
        self.calls.append(ModelCallRecord(task, tier, len(system_prompt) + len(user_prompt), len(raw), 0.0, True))
        return ModelResponse(parsed, raw, f"mock-{tier.value}", max(1, (len(system_prompt) + len(user_prompt)) // 4), max(1, len(raw) // 4), 0.0)
