# Archimedes development evaluation

The synthetic corpus and evaluation runner are deliberately outside `agents/archimedes`; they are
not part of the competition bundle. The corpus spans compiler flags, optimized source, guards,
inline assembly, fallback selection, atomic linkage, ABI width, endianness, packaging/dependencies,
generated configuration, multi-file consistency, artifact paths, intrinsics, feature detection, and
an unfamiliar OOD target.

`replay_development_cases.py` prepares complete final worktrees from the released public Debian
source packages. On Windows it uses an unprivileged, workspace-local extraction of `dpkg-dev` and
`libdpkg-perl` through the `Ubuntu` WSL distribution; `.bz2` sources additionally need the local
`bzip2` and `libbz2-1.0` packages. These tools live under ignored `runs/tools/` and are never part of
the Agent submission or a Worktree Bundle. After extracting those packages into
`runs/tools/dpkg-dev/root`, run:

```text
python dev/replay_development_cases.py \
  --dataset-root runs/d1/buildbench-development-cases-v0.1 \
  --output-root runs/development-replay \
  --python C:\path\to\python3.11.exe \
  --case CASE_ID
```

Omit `--case` to replay all 200 Cases. A replay means the Agent completed and a final worktree was
preserved; it does not mean the package builds. Only the organizer's Development Validation service
provides target-architecture build evidence.

Create a deterministic Development Validation Worktree Bundle from one frozen replay with:

```text
python dev/package_worktree_bundle.py \
  --replay-root runs/development-validation-candidates-v0210-a847c1d \
  --output dist/buildbench-development-worktrees-v0.2.10-a847c1d.zip
```

The packager fails closed on incomplete Cases, duplicate Case IDs, symlinks, and non-regular files.
It omits version-control and quilt state such as `.git*`, `.hg`, `.svn`, and `.pc`, which are
repository metadata rather than package source and are rejected by the validation service.
It follows the live portal contract exactly: a root `manifest.json` using schema `0.1` and suite
`development-recommended-v1`, plus each complete repaired package source tree under
`cases/<case-id>/worktree/input/`. It does not add Case-level manifests or platform metadata. This
bundle is for Development Validation; it is distinct from `dist/agent-submission.zip`.

Use a fixed case order, seed, model, generation settings, model/build budget, and wall-time budget
when comparing configurations. Change only one component for an ablation. The default runner does
not execute package builds, so `verified_build_success` stays null; causal-location and repair-family
agreement are development surrogates only.

Examples:

```text
python dev/evaluate.py --configuration A --case unconditional_x86_sse_flag --output dev/pilot.jsonl
python dev/evaluate.py --configuration K --output dev/full-synthetic.csv
python dev/evaluate.py --configuration K --case unconditional_x86_sse_flag --trace-dir dev/traces
python dev/evaluate.py --configuration C --case unconditional_x86_sse_flag --artifact-dir runs/model-artifacts
python dev/trace_viewer.py dev/traces/unconditional-x86-sse-flag-K.jsonl --output dev/trace.html
```

Configurations B and D require a running local OpenAI-compatible endpoint. Configurations C and E
require an explicitly configured remote endpoint/model and force the STRONG tier; run them only as
cost-controlled pilots. A and K remain usable without a provider, and K accepts caller-selected
provider settings. Use `ARCHIMEDES_CONTEXT_MODE=raw_context` with K for the capped raw-vs-selected
context experiment.

Use `--artifact-dir` for small model pilots when the actual guarded worktree and diagnostics must be
inspected. Artifacts are stored under ignored `runs/` paths by convention and are never bundled.
Slow local models can use `--case-timeout-seconds 240`; the agent's individual request timeout
remains independently bounded by `ARCHIMEDES_MODEL_TIMEOUT_SECONDS`.

Every agent run writes a bounded `output/execution-trace.jsonl` by default. The trace records the
controller stages, evidence counts, hypothesis/probe decisions, candidate review, build requests,
model usage metadata, and final outcome. It deliberately excludes raw source, raw logs, prompts,
model responses, and credentials. Set `ARCHIMEDES_ENABLE_LOCAL_TRACE=false` to disable it or
`ARCHIMEDES_TRACE_MAX_EVENTS` to lower its event cap. `trace_viewer.py` is a standard-library,
development-only renderer; its HTML and copied traces are ignored by Git.

For local-model levels, set the loopback base URL and model identifiers in ordinary environment
variables. For remote pilot experiments, configure rates and a maximum estimated cost explicitly.
Do not put credentials into the case corpus, output table, submitted Agent directory, or Git.
