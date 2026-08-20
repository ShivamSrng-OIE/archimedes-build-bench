"""Separate, injection-resistant contracts for each model reasoning task."""

from __future__ import annotations

from ..constants import UNTRUSTED_CONTENT_WARNING
from .base import ModelTask

_TASK_INSTRUCTIONS = {
    ModelTask.FAILURE_ANALYSIS: (
        "Identify the first causal failure and the source-world assumption that fails on the "
        "target. The object must contain exactly these required fields and compatible types: "
        'primary_failure string, failure_stage string, claims array of objects with a "statement" '
        "string, hypotheses array, and recommended_files array of repository-relative strings."
    ),
    ModelTask.SEMANTIC_ANCHOR: (
        "State functional purpose before translating syntax. Return purpose, inputs, outputs, "
        "side_effects, invariants, optimization_intent, architecture_specific_mechanism, "
        "portable_alternatives, and uncertain_assumptions."
    ),
    ModelTask.HYPOTHESIS_GENERATION: "Return plausible causal alternatives under hypotheses with support and falsifiers.",
    ModelTask.HYPOTHESIS_RANKING: "Return ranked_hypothesis_ids and evidence-grounded rationale.",
    ModelTask.PATCH_PLANNING: (
        "Return all four required top-level fields: repair_family string, rationale string, "
        "assumptions non-empty string array, and edits array containing one to five objects. "
        "repair_family must be one of conditional_compiler_flag, portable_fallback, "
        "architecture_guard, dependency_declaration, architecture_dependency_condition, "
        "toolchain_feature_detection, abi_type_fix, atomic_linkage, packaging_rule, "
        "artifact_path, test_portability, config_generation, multi_file_synchronization, or "
        "source_port. Every edit requires operation, path, replacement, and rationale strings. "
        "Allowed operations are replace_lines, insert_before, insert_after, and create_file. "
        "Every non-create edit also requires expected_text copied exactly from the supplied file. "
        "When the diagnostic names an unsupported compiler option, replace the existing active "
        "declaration; never insert a guarded duplicate while leaving the original active. In a "
        "Makefile, derive the compilation target from $(CC) -dumpmachine or a proven capability "
        "check; never use host uname or undeclared case-metadata variables as a target guard. "
        "Compiler targets are triples, so use a prefix wildcard such as "
        "$(filter x86_64%,$(shell $(CC) -dumpmachine)). "
        "Do not return patch, changes, commands, markdown, or shell commands in place of edits."
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
