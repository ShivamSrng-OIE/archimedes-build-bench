# Archimedes

Archimedes is an evidence-first autonomous repair agent for cross-architecture package failures.
Its research objective is to test whether claim-level uncertainty, source/target causal modeling,
semantic abstraction, cheap discriminative probes, and failure-frontier feedback can improve
portable software repair. No benchmark-performance claim is made by this implementation.

## Competition execution model

The agent follows Build-Bench Starter Kit `0.1.0-rc.2`. It expects `BB_WORKSPACE` to name a
workspace with read-only-style `input/`, the only
editable package tree at `work/repo/`, and writable `output/`. If the variable is absent, the
official protocol default is `/workspace`. `python -m src.main` runs exactly one case and writes
`output/agent-result.json`; the repair itself is the changed `work/repo` tree.

The checked-in manifest and result writer use the kit's schema/protocol `0.1`. The manifest contains
no undocumented keys. The agent bundle uses only Python 3.11 standard-library modules.

### Public Development Cases v0.1

The organizer regenerated the 200 public Ubuntu Launchpad cases for the 7 September 2026 public
phase: 100
`x86_64-to-aarch64` and 100 `aarch64-to-x86_64`. Archimedes accepts the released
`buildbench.case.v0.1` metadata shape, including architectures nested under `platform` and failure
logs selected through `task.initial_failure_evidence` or `paths.target_failure_log`. It retains
support for the flat protocol `0.1` task shape used by the Starter Kit and Hosted Smoke Test.
When a runner-generated task omits architecture fields, the loader can recover only the documented
identity fields from a schema-validated worktree-root `case.json`; input metadata remains
authoritative and is never replaced.

The public development archive is development evidence, not part of the submitted Agent. It must
not be bundled into `agent-submission.zip`, and its source-package licensing and attribution terms
continue to apply. The current archive was generated at `2026-09-07T01:22:06Z` and has SHA-256
`94372a28dbd81ece8f5007b83ec4b4ff1b0590de43a528be4461ae7f2bbd675d`. It replaced an earlier
archive under the same `v0.1` label, so the digest—not the label alone—is the release identity.
All 1,510 checksummed members were verified directly from the ZIP. Five Debian epoch filenames use
colons and therefore require Linux extraction rather than a Windows filesystem.

A 7 September audit of all 200 current failure logs found a non-empty diagnostic and deterministic
failure-family classification for every log. Ten logs exceed the two-megabyte context budget, so
the loader now keeps bounded head and tail evidence instead of discarding the terminal failure.
Forensics suppresses zero-error summaries, echoed commands, environment inventories, warning-only
probes, and wrapper cascades; it recognizes released Debian/linker/test diagnostics and extracts a
causal repository path when present. The current audit identified such a path in 106 of 200 logs,
up from 92 before this parser pass, with none of the selected primaries matching the audited
false-positive prefixes. Model context includes the causal log window and prioritized Debian
packaging files. This is parser/context coverage, not verified repair or build success.

The evidence-bounded Debian multiarch planner covers missing installed paths and CMake package
directories when the released log itself contains the hard-coded source triplet. For `.install`
manifests it additionally requires declared debhelper compat 13 or later and edits only tokens
repeated verbatim in the failure log. Replays on the actual `node-re2`, `rocm-llvm`, and
`buteo-sync-plugins` sources made only those guarded edits; none is counted as a repair success
without organizer-equivalent target validation.

## System architecture

The runtime is separated into small typed packages:

- `workspace` resolves all paths, loads bounded case input, discovers tools without running them,
  and rejects traversal, absolute paths, protected metadata, evaluator-like targets, and external
  symlink destinations.
- `forensics` normalizes noisy logs, chooses the earliest high-causality diagnostic, classifies
  failure families, produces stable signatures, and places failures on an ordered frontier.
- `architecture` owns alias normalization, hazard scanning, the Architecture Constitution,
  familiarity and OOD scoring, evidence-first discovery, semantic anchors, and safe probe execution.
- `evidence`, `graph`, and `reasoning` maintain the evidence ledger, claim confidence,
  source/target Architecture Delta Graph, hypothesis portfolio, probe value, candidate ranking,
  and the explicit repair controller state machine.
- `models` provides deterministic mock and local/remote OpenAI-compatible clients behind one
  structured interface, strict JSON contracts, bounded retry, and cost-aware FAST/STRONG routing.
- `repair` loads generic portability rules, generates structured edits, runs portability and policy
  critics, applies atomic content/hash-guarded changes, and supports rollback without Git.
- `build` isolates the unreleased official build-feedback contract. The current provider is
  unavailable and fail-closed; no request format has been invented.
- `memory`, `context`, and `metrics` retain bounded per-case state, select small provenance-bearing
  context, and measure action/resource value without assuming cross-case persistence.

The thin `src/main.py` only loads configuration, resolves the workspace, constructs dependencies,
runs `RepairController`, writes the result, and translates expected fatal errors into diagnostics.

## Reasoning loop

The controller ingests immutable case evidence, inventories the repository, extracts the first
causal failure, scans architecture hazards, builds the target Constitution and familiarity score,
and creates a source/target delta graph. It maintains multiple real hypotheses when evidence is
ambiguous. Cheap bounded repository probes precede model or build spending. A repair is considered
only after causal assumptions pass consequence-dependent confidence gates, portability review, and
deterministic competition policy review.

The source architecture is treated as a control condition: the central question is which assumption
was valid in the source world but invalid in the target world. Architecture-specific syntax is not
translated until the functional purpose and invariants are represented as a Semantic Anchor. An
existing generic/scalar implementation is preferred to novel target-specific source.

When official build feedback becomes available, each request must state expected information value.
The new frontier is compared with the old one: deeper stages are retained, earlier stages are rolled
back, unchanged signatures are ineffective, and a same-stage new causal error is partial progress.
The edit transaction preserves the best reversible state independently of repository Git metadata.

## Evidence and confidence semantics

Confidence values are heuristic epistemic decision scores, not empirically calibrated probabilities.
Evidence retains kind, source, excerpt, optional path/range, reliability, strength, and stage. Claims
retain separate supporting and contradicting evidence. Fusion occurs in one module using independent
support/contradiction products. Pure LLM support is capped at `0.65`; pure static-pattern support is
capped at `0.75`; confirmation requires `>= 0.90`, non-LLM evidence, and no strong direct conflict.
High-consequence claims need `0.80`; critical claims need `0.90`. A candidate is controlled by its
weakest required high-consequence assumption rather than an average of easy facts.

This design makes model self-confidence subordinate to observed evidence. Low-familiarity/OOD mode
increases static discovery, portable-fallback search, toolchain evidence, and cross-checking while
preventing unsupported architecture improvisation from becoming confirmed.

## Repair rules and security

`src/knowledge/portability_rules.json` contains generic semantic-shape rules for conditional
architecture flags, scalar fallbacks, intrinsic guards, toolchain feature detection, atomic linkage
when proven, and ABI width assumptions. It contains no package- or case-specific hidden fixes.

Repository contents and logs are untrusted input. They are inspected statically and inserted into
model prompts with an explicit prompt-injection warning. Model output cannot execute shell commands;
only `replace_lines`, `insert_before`, `insert_after`, and `create_file` operations are accepted.
File deletion is disabled. Edits cannot target workspace input/output, tests via deletion, hidden/evaluator
paths, VCS metadata, dummy artifacts, validation bypasses, sandbox escapes, or arbitrary command
execution. Text edits reject probable binaries and non-UTF-8 data, verify expected text/hash, write
through same-directory atomic replacement, and record before/after hashes.

There is no Case-ID, package-name, or benchmark-marker repair branch. A generic packaging rule can
remove one unconditional build-script `echo ... >&2` plus nonzero `exit` only when that exact emitted
message appears in the causal failure log. The same evidence rule applies to any supported build
script and refuses ambiguous matches.

## Model providers and local development

Development does not require paid inference. `MOCK`, `LOCAL_OPENAI_COMPATIBLE`, and
`REMOTE_OPENAI_COMPATIBLE` are logical modes behind `ModelClient`. Unit tests inject deterministic
`MockModelClient` fixtures keyed by task and verify task/tier/input-size telemetry. Runtime backend
selection uses:

```text
ARCHIMEDES_MODEL_BACKEND=mock|local_openai_compatible|remote_openai_compatible
ARCHIMEDES_MODEL_BASE_URL=http://127.0.0.1:11434/v1
ARCHIMEDES_MODEL_API_KEY=...                 # remote only; never logged
ARCHIMEDES_FAST_MODEL=...
ARCHIMEDES_STRONG_MODEL=...
ARCHIMEDES_MODEL_TIER=auto|fast|strong
ARCHIMEDES_MODEL_TIMEOUT_SECONDS=30
ARCHIMEDES_MAX_TOTAL_MODEL_CALLS=12
ARCHIMEDES_MAX_STRONG_CALLS=3
ARCHIMEDES_MAX_TOTAL_TOKENS=50000
```

The competition rulebook requires a participating Agent to use an LLM as its foundation model.
`mock` is therefore for deterministic development and orchestration tests only; it is not an
acceptable final competition configuration. The final immutable version must use a real local or
remote OpenAI-compatible model through the organizer-approved network and credential mechanism.
That mechanism must be validated after the protocol freeze; credentials are never bundled.

Local mode rejects non-loopback base URLs. The reasoning layer contains no Ollama, llama.cpp, vLLM,
or hosted-provider assumption. If a configured real provider is unusable, deterministic analysis
runs first and the runtime returns a clear structured model error; it does not pretend heuristics are
a foundation model.

`auto` is the production tier policy: start with FAST and escalate to STRONG only on explicit
uncertainty signals. `fast` and `strong` force a tier for controlled development comparisons; they
do not change the provider or the configured model identifiers.

When deterministic rules cannot justify a repair, a configured model may perform bounded failure
analysis followed by structured patch planning. Only validated causal/model-selected files enter the
prompt. Plans are limited to five structured edits, must include exact expected text for existing
files, receive current-content hashes, and pass claim-confidence, portability, path/policy, and atomic
execution gates. Raw prompts and model responses are not written to the local execution trace.

Optional development-only cost control uses `ARCHIMEDES_INPUT_COST_PER_MILLION`,
`ARCHIMEDES_OUTPUT_COST_PER_MILLION`, and `ARCHIMEDES_MAX_ESTIMATED_COST_USD`. Pricing is never
hard-coded. If rates are absent, estimated cost is unknown. STRONG escalation is bounded (default
three calls) and requires an explicit signal such as low critical confidence, high hypothesis
entropy, sample disagreement, unknown semantics, novel multi-file reasoning, unexplained repair
failure, or OOD mode without adequate deterministic evidence.

## Feature switches and experiments

The following environment switches default to true and support controlled ablation:

```text
ENABLE_DETERMINISTIC_FORENSICS
ENABLE_CLAIM_CONFIDENCE
ENABLE_ARCHITECTURE_DISCOVERY
ENABLE_DELTA_GRAPH
ENABLE_SEMANTIC_ANCHORS
ENABLE_ACTIVE_PROBES
ENABLE_REPAIR_RULES
ENABLE_FAILURE_FRONTIER
```

`ARCHIMEDES_CONTEXT_MODE=raw_context|archimedes_selected_context` compares bounded raw log context
with selected causal context. `dev/evaluate.py` (outside this competition bundle) runs a fixed,
sorted synthetic case set under A–K configurations and emits JSONL or CSV. It records seed, commit,
configuration, model, token/cost data, frontiers, causal-file agreement, and repair-family agreement.
The harness never labels these surrogates as verified build success. Paid-model experiments should
expand from 5–10 representative cases to 20–30 only after traces and cost are inspected.

## Diagnostics, reproducibility, and limitations

Bounded development diagnostics are written as `evidence-ledger.json`,
`architecture-constitution.json`, `repair-episode.json`, `metrics.json`, and
`execution-trace.jsonl`. The JSONL trace exposes the state-machine flow and bounded decision
metadata without raw source, raw logs, prompts, model responses, or credentials. It is local-only,
requires no service or extra dependency, and can be disabled with
`ARCHIMEDES_ENABLE_LOCAL_TRACE=false`. Filesystem ordering and per-case identifiers are
deterministic; the experiment runner
records commit, UTC timestamp, seed, model identifier, feature configuration, and case metadata hash.

## Method disclosure

- Foundation model: selected at deployment through `ARCHIMEDES_FAST_MODEL` and
  `ARCHIMEDES_STRONG_MODEL`; the exact provider, model/version, and endpoint configuration must be
  recorded with the final immutable submission. Mock fixtures are not a competition foundation
  model.
- Prompting and loop: task-specific structured contracts in `src/models/prompts.py`; evidence-first
  failure analysis, bounded patch planning, portability/policy critique, guarded execution, and up
  to the configured repair-attempt limit with frontier-based retention or rollback.
- Retrieval and tools: bounded repository inventory, regex/source search, static architecture
  hazard scanning, local portability rules, and officially exposed build feedback. External
  knowledge is disabled by default.
- Training and data: no training or fine-tuning is performed. The development-only synthetic corpus
  is outside the submitted Agent and contains no hidden Case material. No private knowledge base or
  pre-generated Case repair is bundled.
- External services: only the configured OpenAI-compatible model endpoint may be contacted, subject
  to the final competition network policy. The Agent does not access Docker, the Validator, or any
  evaluator service directly.
- Reproducibility: inference providers can be nondeterministic even with deterministic request
  settings. Filesystem traversal, identifiers, budgets, candidate gates, and JSON outputs are
  deterministic. The Agent has no cross-Case cache and declares no third-party Python dependency.

Important current limitations:

- The latest published Starter Kit remains `0.1.0-rc.2`. The public Development Cases are `v0.1`,
  while separate protocol-schema and runtime-image releases remain unpublished.
- As of the 7 September 2026 CSTCloud audit, Full Evaluation is disabled and the official
  network/API-key injection policy, final runtime budgets, resource limits, score denominator,
  rerun limits, and credential mechanism remain unpublished.
- `BbBuildFeedbackProvider` intentionally remains unavailable. Host compiler results are never
  treated as official target-build truth because the participant documentation still calls hosted
  build feedback a planned later-protocol feature.
- Deterministic planners cover uniquely evidenced compiler-feature checks, guarded x86 flags,
  portable source selection, atomic linkage, RPM architecture conditions, artifact paths,
  generated-configuration markers, byte serialization, pointer-width assumptions, synchronized
  feature dispatch, and uniquely observed hard-coded Debian multiarch paths.
  Novel semantics or ambiguous source transformations still require model evidence
  or return `no_safe_fix` rather than guessing.
- QEMU is only a discovered capability; no emulator result is treated as definitive hardware,
  kernel, atomic, or memory-model evidence.
- The submission archive is produced only with the root Starter Kit tooling. Never
  include `.env`, credentials, caches, prior logs, model traces, or the development corpus.

These choices preserve the research rationale: claim-level confidence prevents one dangerous weak
assumption from hiding among easy facts; source architecture supplies the control condition;
Semantic Anchors discourage instruction-name translation; builds are active experiments rather than
blind retries; frontier tracking preserves partial progress; and unknown-architecture mode changes
evidence-gathering behavior instead of increasing speculation.

## Development checks

From the repository root:

```text
python -m unittest discover -s tests -t . -v
python -m compileall agents/archimedes/src
ruff check agents/archimedes/src tests dev/evaluate.py dev/trace_viewer.py
mypy agents/archimedes/src
python dev/evaluate.py --configuration A --case unconditional_x86_sse_flag
```

Run official `bb doctor`, `bb test`, `bb check`, and `bb ready` from the repository root. They require
the Docker Linux daemon and the kit's pinned runtime images. Archimedes never uploads automatically.
