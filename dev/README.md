# Development tools

Scripts for testing Archimedes during development. Nothing in `dev/` is part of the Agent
submission ZIP. Run every command from the repository root.

| Script | What it does |
|---|---|
| `evaluate.py` | Runs the synthetic cases in `synthetic_cases/` under one configuration and writes a result table. |
| `trace_viewer.py` | Turns an Agent execution trace into a single HTML page. |
| `replay_development_cases.py` | Runs the Agent over the 200 public Development Cases and keeps each final worktree. |
| `package_worktree_bundle.py` | Packs a replay's worktrees into a ZIP for the organizer's Development Validation service. |
| `submission_preflight.py` | Checks a packaged submission ZIP against the current Agent source before upload. |

None of these scripts builds packages. Only the organizer's Validator (or Development Validation
service) can show that a repair builds on the target architecture.

## Synthetic evaluation

`dev/synthetic_cases/` holds 16 small cases, each a different failure shape (compiler flags, inline
assembly, intrinsics, atomic linkage, pointer width, endianness, packaging conditions, generated
configuration, an unfamiliar target architecture, and so on). `evaluate.py` copies each into a
temporary workspace, runs the Agent, and records whether it edited the expected file and chose the
expected repair family. `verified_build_success` is always null.

```text
python dev/evaluate.py --configuration A --case unconditional_x86_sse_flag --output dev/pilot.jsonl
python dev/evaluate.py --configuration K --output dev/full-synthetic.csv
python dev/evaluate.py --configuration K --case unconditional_x86_sse_flag --trace-dir dev/traces
python dev/evaluate.py --configuration C --case unconditional_x86_sse_flag --artifact-dir <artifact-dir>
python dev/trace_viewer.py dev/traces/unconditional_x86_sse_flag-K.jsonl --output dev/trace.html
```

The configurations live in `configurations.json`:

| Config | Setup |
|---|---|
| A | Deterministic repair rules only, no model. |
| B, C | A local (B) or remote STRONG-tier (C) model with minimal context and no rules. |
| D, E | Full Archimedes with a local (D) or remote STRONG-tier (E) model. |
| F to J | Full Archimedes with one feature turned off: claim confidence, Semantic Anchors, Architecture Discovery, active probes, Failure Frontier. |
| K | Full Archimedes with whatever model backend the environment selects (default). |

A and K run without a model provider. B and D need a local OpenAI-compatible endpoint on a
loopback address. C and E need an explicitly configured remote endpoint and model; set the
per-million token rates and `ARCHIMEDES_MAX_ESTIMATED_COST_USD`, and keep them to small pilots.
Set `ARCHIMEDES_CONTEXT_MODE=raw_context` with K to compare raw context against selected context.

When comparing configurations, keep the case list, seed, model, budgets and time limits fixed and
change one thing at a time. `--case-timeout-seconds 240` helps slow local models; each model
request is still limited separately by `ARCHIMEDES_MODEL_TIMEOUT_SECONDS`. `--artifact-dir` keeps
each workspace for inspection.

Never put credentials in the cases, result tables, the Agent folder or Git.

## Execution traces

Every Agent run writes `output/execution-trace.jsonl`. It records controller stages, evidence
counts, hypothesis and probe decisions, candidate review, build requests, model usage metadata and
the final outcome. It leaves out source code, raw logs, prompts, model responses and credentials.
Set `ARCHIMEDES_ENABLE_LOCAL_TRACE=false` to turn it off or `ARCHIMEDES_TRACE_MAX_EVENTS` to lower
its event limit. Generated `.jsonl`, `.csv` and `.html` files under `dev/` are ignored by Git.

## Replaying the Development Cases

`replay_development_cases.py` builds each Case's workspace, unpacks the Debian source with
`dpkg-source -x`, runs the Agent and writes `replay-summary.json`. On Windows it calls
`dpkg-source` through the `Ubuntu` WSL distribution, using `dpkg-dev` and `libdpkg-perl` (and, for
`.bz2` sources, `bzip2` and `libbz2-1.0`) extracted without installation into a local folder
that you pass as `--dpkg-source-root`.

```text
python dev/replay_development_cases.py \
  --dataset-root <dataset-dir>/buildbench-development-cases-v0.1 \
  --output-root <replay-output-dir> \
  --dpkg-source-root <extracted-dpkg-dev-root> \
  --python C:\path\to\python3.11.exe \
  --jobs 6 \
  --case CASE_ID
```

Omit `--case` to replay all 200 Cases. The output folder must not exist yet. The exit code is 0
only if every Case reports `completed`. A replay shows what the Agent changed, not whether the
package builds. To run natively on Linux instead, see section 5.4 of `docs/CODEBASE_GUIDE.md`.

## Worktree Bundles

```text
python dev/package_worktree_bundle.py \
  --replay-root <replay-output-dir> \
  --output <bundle-name>.zip
```

The bundle has a root `manifest.json` (schema `0.1`, suite `development-recommended-v1`) and each
Case's full source tree under `cases/<case-id>/worktree/input/`. Its bytes are reproducible. The
packager stops on incomplete Cases, duplicate Case IDs, symlinks and non-regular files, and leaves
out version-control and quilt folders (`.git*`, `.hg`, `.svn`, `.pc` and similar), which the
service rejects. A Worktree Bundle is not the Agent submission ZIP; never mix them up.

## Submission preflight

```text
python dev/submission_preflight.py --hosted-smoke-sha256 SHA --final-confirmed-sha256 SHA
```

This rebuilds the ZIP from `agents/archimedes` with the Starter Kit's `runner/` code and compares
it with the packaged ZIP given by `--archive`. It also checks Git state, Docker images, the latest
local `bb ready` result and, when `ARCHIMEDES_MODEL_BACKEND` is set, the model settings. Hosted steps
cannot be checked locally, so you pass the SHA-256 of the ZIP that passed the Hosted Smoke Test and
was confirmed final, plus `--team-registered` and `--submission-access-confirmed`. Add `--json` for
the full report. The current confirmed release is documented in `SUBMISSION.md`.
