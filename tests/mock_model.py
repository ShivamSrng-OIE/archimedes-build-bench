"""Deterministic no-network model fixtures with routing telemetry."""

from __future__ import annotations

import json
from collections.abc import Mapping

from src.errors import ModelError
from src.schemas import JSONValue
from src.models.base import ModelCallRecord, ModelResponse, ModelTask, ModelTier
from src.models.json_protocol import parse_model_json


class MockModelClient:
    """Return configured task fixtures and record every requested routing decision."""

    def __init__(self, responses: Mapping[ModelTask, dict[str, JSONValue] | list[dict[str, JSONValue]]]):
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
