"""Language-model access for the Agent: client, router, prompts and response checks.

It holds the provider-neutral types in ``base``, the standard-library
OpenAI-compatible HTTP client, the budgeted FAST/STRONG router, the per-task
system prompts and the strict JSON response contracts. Test doubles live in
``tests/``, not here.
"""

from .base import ModelClient, ModelResponse, ModelTask, ModelTier

__all__ = ["ModelClient", "ModelResponse", "ModelTask", "ModelTier"]
