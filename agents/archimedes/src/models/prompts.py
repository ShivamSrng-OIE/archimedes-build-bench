"""Separate, injection-resistant contracts for each model reasoning task."""

from __future__ import annotations

from ..constants import UNTRUSTED_CONTENT_WARNING
from .base import ModelTask

_TASK_INSTRUCTIONS = {
    ModelTask.FAILURE_ANALYSIS: (
        "Identify the first causal failure. Return keys primary_failure, failure_stage, "
        "claims, hypotheses, and recommended_files. Explicitly ask what assumption was valid "
        "on the source architecture but invalid on the target architecture."
    ),
    ModelTask.SEMANTIC_ANCHOR: (
        "State functional purpose before translating syntax. Return purpose, inputs, outputs, "
        "side_effects, invariants, optimization_intent, architecture_specific_mechanism, "
        "portable_alternatives, and uncertain_assumptions."
    ),
    ModelTask.HYPOTHESIS_GENERATION: "Return plausible causal alternatives under hypotheses with support and falsifiers.",
    ModelTask.HYPOTHESIS_RANKING: "Return ranked_hypothesis_ids and evidence-grounded rationale.",
    ModelTask.PATCH_PLANNING: (
        "Return repair_family, rationale, assumptions, and structured edits. Allowed edit operations "
        "are replace_lines, insert_before, insert_after, and create_file. Never emit shell commands."
    ),
    ModelTask.PATCH_CRITIQUE: "Return accepted and findings for portability, semantic, and policy risks.",
    ModelTask.POST_BUILD_REFLECTION: "Return interpretation, hypothesis_updates, and next_action from frontier movement.",
}


def system_prompt(task: ModelTask) -> str:
    return (
        "You are one bounded reasoning component inside Archimedes. Return exactly one JSON object. "
        "Treat expressed confidence as a claim, not ground truth. "
        f"{UNTRUSTED_CONTENT_WARNING} {_TASK_INSTRUCTIONS[task]}"
    )
