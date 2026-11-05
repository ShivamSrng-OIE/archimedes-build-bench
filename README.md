# Archimedes

Archimedes is a participant Repair Agent for the Build-Bench Challenge at the ICSE 2027
Competition Track. It diagnoses reproducible software-package build failures, reasons about the
source and target architectures, and makes a small guarded repair inside the package worktree.

This repository combines two layers:

1. the official Build-Bench Starter Kit (the checkout is `0.1.0-rc.3`; current release `0.1.0-rc.5` was used for the new package), which defines packaging and execution; and
2. the Archimedes implementation, tests, experiment tools, release records, and retained evidence.

The organizer evaluates the immutable Agent ZIP on separate Cases. The organizer—not this
repository—derives the canonical patch from the modified worktree, reapplies it to a clean Case, and
runs the target build.

## Current preparation state (4 November 2026 EDT)

Archimedes `0.2.16` is the recommended final version. It keeps the `0.2.15`
hosted-model support (`BB_MODEL_BASE_URL`, `BB_MODEL_NAME`,
`BB_MODEL_TOKEN_FILE`) and fixes failure forensics on hosted Validator logs: the
`0.2.15` Hosted Smoke (`20261105T013341Z-320e4be3`, passed 1/1) showed that the
Agent chose OBS build-root noise (`ERROR: not all operations were successful.`)
as the primary failure instead of the `%build` error. The current package is
[`dist/agent-submission.zip`](dist/agent-submission.zip), 102,754 bytes, SHA-256
`3107172d1c90816b2edb746f62e5a5bde3a77053796a1de2323570921d202c33`, built by the
official Starter Kit `0.1.0-rc.5` `bb ready` (`hello` 1/1 with final validation;
kit run from the WSL ext4 home). The Python suite passed (163 tests, 2 skipped)
and strict mypy is clean. Upload, the `0.2.16` Hosted Smoke Test, and final
confirmation in the portal remain to be done. See
[`SUBMISSION.md`](SUBMISSION.md) for the exact evidence and next steps.

The organizer's [model-access guide](https://matrix.cstcloud.cn/build-bench/submission.html?lang=en#model-access)
requires a completed official model call plus independent repair validation in
the new Hosted Smoke Test. A previous final selection must be revalidated and
explicitly reconfirmed by the end of 13 November 2026, Beijing time. Final
confirmation cannot be changed.

## Historical v0.2.14 portal state

As of **4 October 2026 EDT**, the organizer portal shows this exact release as qualified and selected
as the final version:

| Field | Value |
|---|---|
| Agent | `archimedes` |
| Version | `0.2.14` |
| Submission ID | `20261003T193341Z-1c80de94` |
| Portal state | Qualified; Hosted Smoke passed 1/1; Final version selected |
| Selected | 4 October 2026 at 10:57 AM EDT |
| Release source commit | `093b7f2` |
| Historical named ZIP | `dist/agent-submission-v0.2.14-ready.zip` |
| ZIP size | 100,092 bytes |
| SHA-256 | `714bec9725b99493af37b2ab4e9ea38c9d721ee1fda692aa30eb9c2a7bf9d7ed` |

This is the previous portal selection, recorded before the new model-access
requirement. The Agent source has since advanced to v0.2.15. The portal state
must be checked again before treating any version as finally confirmed.

There is currently **no completed organizer Full Evaluation, official score, rank, or leaderboard
result** for this submission. Hosted Smoke and Development Validation are valuable qualification
evidence, but neither is the hidden evaluation.

## Read this first

If you only need one route through the project:

1. Read this README for the complete project and workflow overview.
2. Read [`SUBMISSION.md`](SUBMISSION.md) for the exact selected release and portal procedure.
3. Read [`agents/archimedes/README.md`](agents/archimedes/README.md) for the frozen v0.2.14 research
   and runtime design.
4. Read [`COMPETITION_AUDIT.md`](COMPETITION_AUDIT.md) for the website, rules, corpus, and evidence
   audit.
5. Read [`dev/README.md`](dev/README.md) before replaying public Cases or running experiments.

Do not edit `runner/`, `templates/`, `example-cases/`, or the top-level `bb` script to make the
Agent pass. Formal evaluation uses organizer-controlled copies of those components, and they are not
included in the submission ZIP.

## What the Agent does

For each Case, the organizer provides:

```text
/workspace/
|-- input/          read-only Case metadata and initial failure evidence
|-- work/repo/      writable package worktree; the Agent may modify only this tree
`-- output/         writable structured Agent output and diagnostics
```

Archimedes then follows this bounded pipeline:

1. **Resolve and validate the workspace.** Reject unsafe paths, traversal, protected metadata,
   evaluator-like targets, and external symlinks.
2. **Load bounded evidence.** Read Case metadata and the initial build failure without copying
   arbitrary input into the output or a model prompt.
3. **Find the causal frontier.** Normalize noisy logs, choose the earliest high-causality diagnostic,
   classify the failure family, and create a stable error signature.
4. **Model the architecture delta.** Normalize architecture aliases, scan the repository for hazards,
   build source/target constraints, measure familiarity, and enter conservative OOD behavior when
   necessary.
5. **Build and test hypotheses.** Search the repository, attach supporting and contradicting
   evidence, run cheap bounded probes, and rank candidates by consequence-sensitive confidence.
6. **Plan a repair.** Prefer a uniquely evidenced deterministic portability rule. If the evidence is
   insufficient and a real model is configured, request a small structured plan through the bounded
   model interface.
7. **Critique before editing.** Apply causal, portability, competition-policy, path, file-type, and
   ambiguity gates. One evidence-bearing replan is allowed after a policy rejection.
8. **Apply atomically.** Permit only structured text edits, verify expected content and hashes, write
   through same-directory replacement, and retain a reversible best state without relying on Git.
9. **Use build feedback only when officially exposed.** The `bb-build` adapter remains dormant unless
   both the runtime capability and Case metadata declare the exact protocol-0.1 interface.
10. **Write protocol output.** Emit `agent-result.json` and bounded diagnostics. The organizer derives
    the real patch from the worktree; Archimedes never submits a pre-generated patch.

If no repair is sufficiently supported, the safe outcome is `no_safe_fix` rather than an invented
architecture-specific transformation.

## Architecture

The entrypoint is deliberately thin: `agents/archimedes/src/main.py` loads configuration, resolves
the workspace, discovers capabilities, constructs the controller, writes the result, and maps fatal
errors to nonzero process exits.

| Package | Responsibility |
|---|---|
| `workspace/` | Workspace paths, bounded Case loading, repository inventory, capabilities, and path policy |
| `forensics/` | Log parsing, causal diagnostic selection, failure classification, signatures, and frontier tracking |
| `architecture/` | Architecture aliases, hazard discovery, target constitution, familiarity/OOD logic, semantic anchors, and probes |
| `evidence/` | Provenance-bearing evidence, contradictions, and heuristic claim confidence |
| `graph/` | Source/target Architecture Delta Graph construction and queries |
| `reasoning/` | Hypotheses, probe planning, candidate ranking, and the controller state machine |
| `models/` | Mock and OpenAI-compatible clients, strict JSON contracts, prompts, budgets, retry, and FAST/STRONG routing |
| `repair/` | Generic repair families, planning, policy/portability critics, atomic execution, and rollback |
| `build/` | Optional, fail-closed official build-feedback adapter |
| `memory/` | Bounded within-Case episode and rule retrieval; no hidden cross-Case answer cache |
| `context/` | Repository search, snippet selection, and token budgeting |
| `knowledge/` | Generic architecture aliases, failure patterns, and portability rules |
| `metrics.py`, `observability.py` | Bounded metrics and local execution trace |
| `schemas.py`, `result_writer.py` | Typed protocol data and safe JSON output |

The deterministic planners cover evidence-gated patterns such as architecture compiler flags,
portable source selection, intrinsic or feature guards, atomic linkage, RPM architecture
conditions, artifact paths, generated-configuration markers, byte serialization, pointer width,
synchronized feature dispatch, and observed hard-coded Debian multiarch paths. They do not branch on
Case ID or package name.

## Evidence and safety model

Repository files and logs are untrusted input. They may be inspected and quoted in a bounded model
request, but they cannot instruct the Agent to run arbitrary commands or escape the workspace.

Important invariants are enforced in code:

- only `/workspace/work/repo` may be modified;
- no Docker or Validator access;
- no shell-command execution from model output;
- no file deletion by a repair plan;
- no changes to VCS metadata, evaluator-like files, output paths, or external symlinks;
- no Case-ID, package-name, or benchmark-marker answer lookup;
- no secret logging or raw prompt/model-response trace;
- only `replace_lines`, `insert_before`, `insert_after`, and `create_file` structured edits;
- exact expected text or content hash before changing an existing file;
- atomic same-directory writes and bounded rollback;
- fail closed on malformed configuration, protocol declarations, or ambiguous repair matches.

Confidence values are heuristic decision scores, not calibrated probabilities. Claims retain
supporting and contradicting evidence separately. Pure model support and pure static-pattern support
are capped, and high-consequence assumptions must cross stricter thresholds. A candidate is governed
by its weakest required dangerous assumption, not by an average of easy facts.

## Runtime output

The portable result is:

```json
{
  "schema_version": "0.1",
  "status": "completed"
}
```

Depending on the run, bounded development diagnostics may also appear under `/workspace/output`:

| File | Purpose |
|---|---|
| `agent-result.json` | Protocol result and concise terminal message |
| `evidence-ledger.json` | Evidence and confidence decisions |
| `architecture-constitution.json` | Source/target architecture constraints |
| `repair-episode.json` | Repair attempts and retained state |
| `metrics.json` | Resource and action-value measurements |
| `execution-trace.jsonl` | Bounded state-machine events without raw source, prompts, responses, or credentials |

A nonzero entrypoint exit remains authoritative for an Agent failure even if a best-effort result
document could be written.

## Configuration

Configuration comes only from environment variables. Archimedes never reads or bundles a `.env`
file.

### Model and budget settings

| Variable | Default | Meaning |
|---|---:|---|
| `ARCHIMEDES_MODEL_BACKEND` | `mock` | `mock`, `local_openai_compatible`, or `remote_openai_compatible` |
| `ARCHIMEDES_MODEL_BASE_URL` | `http://127.0.0.1:11434/v1` | OpenAI-compatible base URL; local mode requires loopback |
| `ARCHIMEDES_MODEL_API_KEY` | unset | Remote credential supplied at runtime; never store it in this repository |
| `ARCHIMEDES_FAST_MODEL` | `mock-fast` | FAST-tier model identifier |
| `ARCHIMEDES_STRONG_MODEL` | `mock-strong` | STRONG-tier model identifier |
| `ARCHIMEDES_MODEL_TIER` | `auto` | `auto`, `fast`, or `strong` |
| `ARCHIMEDES_MODEL_TIMEOUT_SECONDS` | `30` | Timeout for one model request |
| `ARCHIMEDES_MAX_TOTAL_MODEL_CALLS` | `12` | Total call ceiling |
| `ARCHIMEDES_MAX_STRONG_CALLS` | `3` | STRONG-tier call ceiling |
| `ARCHIMEDES_MAX_TOTAL_TOKENS` | `50000` | Total estimated-token ceiling |
| `ARCHIMEDES_MAX_REPAIR_ATTEMPTS` | `3` | Repair-attempt ceiling; minimum 1 |
| `ARCHIMEDES_INPUT_COST_PER_MILLION` | unset | Development-only input rate |
| `ARCHIMEDES_OUTPUT_COST_PER_MILLION` | unset | Development-only output rate |
| `ARCHIMEDES_MAX_ESTIMATED_COST_USD` | unset | Requires both cost rates; stops before estimated overrun |

`mock` is correct for deterministic tests and orchestration development, but it is not a competition
foundation model. The selected Agent supports a real provider; the organizer's final endpoint,
credential injection, network policy, and model IDs were still unpublished at the last audit. Never
place a credential in an Agent ZIP or Worktree Bundle.

### Behavior and diagnostics

| Variable | Default | Meaning |
|---|---:|---|
| `BB_WORKSPACE` | `/workspace` | Official workspace root |
| `ARCHIMEDES_CONTEXT_MODE` | `archimedes_selected_context` | Selected context or bounded `raw_context` experiment |
| `ARCHIMEDES_ENABLE_EXTERNAL_KNOWLEDGE` | `false` | External knowledge switch; disabled by default |
| `ARCHIMEDES_LOG_LEVEL` | `INFO` | `CRITICAL`, `ERROR`, `WARNING`, `INFO`, or `DEBUG` |
| `ARCHIMEDES_DEBUG` | `false` | Additional development diagnostics |
| `ARCHIMEDES_ENABLE_LOCAL_TRACE` | `true` | Write the bounded local JSONL trace |
| `ARCHIMEDES_TRACE_MAX_EVENTS` | `500` | Maximum trace events |
| `ARCHIMEDES_RANDOM_SEED` | `0` | Deterministic seed |

These ablation switches default to true:

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

## Repository map

```text
.
|-- AGENTS.md                  authoritative coding-agent and submission rules
|-- README.md                  current repository guide (this file)
|-- SUBMISSION.md              immutable release, portal, and evidence record
|-- COMPETITION_AUDIT.md       participant-site, corpus, and rules audit
|-- VERSION                    Starter Kit version
|-- bb                         official Starter Kit command entrypoint
|-- agents/
|   |-- archimedes/            frozen selected v0.2.14 Agent source and manifest
|   `-- example-agent/         read-only official reference Agent
|-- dev/                       development corpus, replay, evaluation, and audit tools
|-- docs/                      historical design specification and documentation index
|-- example-cases/             released official local examples
|-- runner/                    official Starter Kit runtime; never customize for submission
|-- templates/                 official managed-Python template
|-- tests/                     repository tests, including Agent and tooling regression tests
|-- dist/                      ignored generated release artifacts; canonical three retained
`-- runs/                      ignored local evidence; canonical compact set retained
```

### Tracked versus generated content

Tracked source, tests, and documentation are portable through Git. `runs/` and `dist/` are
intentionally ignored because they contain generated artifacts, worktrees, or platform evidence.
Their important identifiers and hashes are recorded in tracked Markdown so a clean checkout does
not depend on ignored files.

The historical [`docs/implementation.md`](docs/implementation.md) describes the original research
specification. It is now tracked for provenance, but the current official kit, `AGENTS.md`, source,
and release records override any old assumption in that document. See
[`docs/README.md`](docs/README.md) for the documentation hierarchy.

## Local development

### Requirements

- Python 3.11 for parity with the managed Agent runtime;
- Git;
- Bash and a working Linux Docker daemon for official `bb` commands;
- Ruff, mypy, and pytest in the development environment for the full local checks.

The submitted Agent itself has no third-party Python dependency. Its `requirements.lock` therefore
contains only an explanatory comment; no unpinned package is present.

### Fast deterministic checks

From the repository root:

```powershell
python -m pytest -q
python -m ruff check agents/archimedes/src tests dev
python -m mypy --strict agents/archimedes/src
python -m compileall -q agents/archimedes/src
python dev/evaluate.py --configuration A --case unconditional_x86_sse_flag
```

The v0.2.14 release gate passed 149 tests, with two platform-specific symlink tests skipped and 56
subtests, plus Ruff, strict mypy over 75 source files, bytecode compilation, archive/source
comparison, secret scanning, and official readiness.

The A–K experiment definitions live in `dev/configurations.json`. Configurations A and K can run
without a provider. Provider-backed configurations must be treated as explicit bounded pilots; a
surrogate result is never target-build evidence.

## Official Starter Kit workflow

The high-level commands are the source of truth for a deliberate future release:

```bash
./bb bootstrap my-agent --json
# Implement a new Agent only under agents/my-agent/src/.
./bb ready --agent ./agents/my-agent --json
```

For Archimedes diagnostics:

```bash
./bb version
./bb doctor
./bb check --agent ./agents/archimedes
./bb test --agent ./agents/archimedes
./bb package --agent ./agents/archimedes
```

`bb ready` snapshots, checks, tests, and packages one exact Agent version. Completion requires all of
the following:

- process exit code 0;
- JSON result `"status": "succeeded"`;
- an existing output ZIP;
- fresh SHA-256 agreement with the JSON artifact metadata.

The repository-level preflight adds competition and provider gates:

```powershell
python dev/submission_preflight.py --json
```

At the current checkpoint it confirms `source_and_archive_ready: true` and
`local_submission_ready: true` for the exact package. It reports the failed
local `bb ready` separately. Overall status remains `not_ready` until the
current Hosted Smoke and final portal confirmation are attested for this ZIP
digest. The local process receives no official model credentials; do not
invent values or store credentials to make an advisory check green.

Do not casually regenerate the currently selected v0.2.14 artifact. For any future version, use a
new semantic Agent version, preserve the previous digest and portal ID, run the full gate, and upload
only the newly frozen ZIP after explicit release review.

## Submission archive contract

The Agent ZIP has exactly these root entries:

```text
agent.yaml
requirements.lock
README.md
src/
```

For v0.2.14, `agent.yaml` declares managed Python 3.11, protocol `0.1`, and entrypoint
`python -m src.main`.

Never include:

- `.env`, API keys, passwords, tokens, private keys, or private endpoints;
- hidden Case information, reference repairs, or Case-specific answer tables;
- pre-generated `repair.diff` files;
- `runs/`, test output, caches, virtual environments, model caches, or weights;
- the Starter Kit runner, templates, or example Cases;
- symlinks, traversal paths, or files outside the documented ZIP root;
- unpinned or URL-based Python requirements.

The exact selected ZIP has 81 unique regular-file members, deterministic timestamps, 280,433
uncompressed bytes, no duplicate/traversal/encrypted/symlink entries, no prohibited output, no
detected credential pattern, and a passing CRC test.

## Public Development Validation

Development Validation accepts a source-package layout or complete unpacked repaired source trees.
It is separate from Agent submission and does not affect the leaderboard.

The retained worktree bundle is:

| Field | Value |
|---|---|
| Path | `dist/buildbench-development-worktrees-v0.2.14-four.zip` |
| Size | 2,251,539 bytes |
| SHA-256 | `d6955489d19d1a2cb45c1a010c900182948b2b33ee0f7368058b60d3dd358b40` |
| Layout | root `manifest.json`; repaired source under `cases/<case-id>/worktree/input/` |

This bundle is byte-for-byte identical to the earlier v0.2.11 four-case bundle whose repair bytes
received four successful public ARM64 target-build observations across two organizer runs:

| Run ID | Result |
|---|---|
| `DV-20260921T040125Z-5afed455` | `node-re2`, `buteo-sync-plugins`, and `qmidiarp` passed; pre-fix `rocm-llvm` exposed the remaining COMGR failure |
| `DV-20260921T154515Z-2ce55011` | repaired `rocm-llvm` passed on attempt 1 |

All 200 released public source Cases completed a v0.2.14 local replay; four Cases modified nine files.
Those nine file digests match the validated v0.2.11 repairs. “Replay completed” means the Agent ran
and produced a worktree; it does **not** mean 200 packages passed target builds.

To recreate a replay or Worktree Bundle, follow `dev/README.md`. Do not place credentials, Git
metadata, quilt state, unrelated files, or the Agent ZIP in a Worktree Bundle.

## Evidence ladder

Keep these claims separate:

| Evidence | What it proves | What it does not prove |
|---|---|---|
| Unit/static checks | Local code invariants and tooling quality | Organizer runtime or a target build |
| `bb ready` | Official local package shape plus Example Case execution/build | Hidden performance |
| Hosted Smoke 1/1 | Organizer accepted and executed the ZIP on one smoke Case | Score or rank |
| Development Validation pass | A selected public repair built on the target architecture | Hidden evaluation result |
| Completed Full Evaluation | Official hidden result for that organizer evaluation | Rank until published compatibly |
| Leaderboard publication | Official comparable score and rank | Future result stability |

At the current checkpoint, Archimedes has evidence through Hosted Smoke plus four public target-build
observations. The account had zero organizer Full Evaluations, so no score is available yet.

## Retained local artifacts

The ignored directories were deliberately pruned to a compact canonical set. Reproducible extracted
datasets, obsolete replays, old caches, and superseded archives were removed; tracked hashes and
historical submission IDs remain.

### `dist/`

```text
agent-submission.zip
agent-submission-v0.2.14-ready.zip
buildbench-development-worktrees-v0.2.14-four.zip
```

### `runs/`

The retained set contains:

- the v0.2.14 full-replay summary, without its reproducible multi-gigabyte expanded worktrees;
- the compact four-Case v0.2.14 replay needed to reproduce the worktree bundle;
- the three organizer Development Validation records, including the original incident and logs;
- the v0.2.11 and selected v0.2.14 submission evidence;
- the one final v0.2.14 `bb ready` result;
- final v0.2.14 source-risk, site-contract, Bandit, coverage, and synthetic-evaluation reports; the
  supplementary reports are retained as artifacts without promoting them beyond the release claims
  in `SUBMISSION.md`.

Generated content is intentionally not Git history. Before deleting future run output, preserve any
unique portal/run ID, digest, permitted log, and evidence classification in a tracked record.

## Troubleshooting

### `bb doctor` fails

Confirm Docker is running, the Linux daemon is reachable, and the immutable kit images can be
pulled. Do not edit official runner files to conceal a host failure.

### Direct Docker Desktop run fails on mounts or nested builds

This checkout and a clean rc.3 kit both reproduced host bind-mount and `/dev/null` permission issues
under direct Docker Desktop execution. The official privileged Linux `bb ready` path succeeded.
Treat the host symptom as environment evidence before changing the Agent.

### A public validation run fails before building

Separate platform intake/infrastructure errors from source preparation, configuration, compile,
link, test, packaging, and artifact errors. Preserve the Run ID and logs. The organizer previously
fixed complete-worktree compatibility and corrected results inside the original run.

### The Agent makes no change

That can be the correct fail-closed result. Inspect the causal diagnostic, evidence ledger,
confidence gates, policy critic, and configured model availability before weakening a safety gate.

### A real model backend fails

Check the explicit backend, URL, model IDs, runtime network policy, credentials, timeouts, and
budgets. Local mode only accepts loopback URLs. Remote credentials must be supplied outside the
repository and are never written to traces.

### Windows cannot extract a released Debian source tree

Some public archive member names contain colons. Use Linux or WSL for faithful extraction. The replay
tooling can use workspace-local Debian utilities under ignored `runs/tools/`; those tools are
reproducible and never belong in an Agent or Worktree Bundle.

## Documentation authority

When documents disagree, use this order:

1. current organizer rules and live protocol;
2. the checked-in official Starter Kit and `AGENTS.md`;
3. the selected release record in `SUBMISSION.md` and current audit in `COMPETITION_AUDIT.md`;
4. the implemented source and tests;
5. the frozen Agent README for v0.2.14-specific design detail;
6. the historical implementation specification.

This ordering keeps current competition contracts separate from research intent and prevents old
notes from silently overriding the immutable selected artifact.
