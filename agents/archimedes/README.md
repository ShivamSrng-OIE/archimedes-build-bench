# Archimedes

Archimedes is a repair agent for the Build-Bench competition. Each run gets one software package
that builds on one CPU architecture but fails on another (x86_64 to aarch64, or the reverse),
together with the failing build log. The agent works out which line of the log is the real cause,
and edits the package only when it can back the edit with evidence from the log and the source.
When it can't, it leaves the package untouched and says why.

This README does not claim any benchmark score. Only the organizer's official build of the
canonical patch decides whether a Case is repaired.

Current version: `0.2.20`.

## How it runs

The agent follows Build-Bench protocol `0.1` and is tested with Starter Kit `0.1.0-rc.5`.
`agent.yaml` declares the managed `python-3.11` runtime and the entrypoint `python -m src.main`.
The code uses only the Python 3.11 standard library, so `requirements.lock` lists no packages.

`BB_WORKSPACE` names the workspace root (default `/workspace`), which holds three directories:

```text
input/       Case metadata and the failing build log (read only)
work/repo/   the package source; the only tree the agent edits
output/      agent-result.json and diagnostic files
```

One invocation handles exactly one Case and writes `output/agent-result.json`. The repair itself
is whatever changed under `work/repo/`; the platform turns that into the canonical patch. The
agent never starts Docker or talks to the Validator.

The loader accepts the released `buildbench.case.v0.1` format (architectures under `platform`, log
named by `task.initial_failure_evidence` or `paths.target_failure_log`) and the flat protocol
`0.1` task format used by the Starter Kit and the Hosted Smoke Test. If a runner-generated task
leaves out the architectures, only the documented identity fields may be recovered from a
schema-checked `case.json` at the worktree root; input metadata always wins. Logs over 2,000,000
characters keep their head and tail, since the setup context and the final error matter most.

## How a run works

`src/main.py` is deliberately thin: it loads the configuration, resolves the workspace, builds the
dependencies, runs `RepairController`, writes the result, and turns known errors into diagnostics.
The controller (`src/reasoning/controller.py`) then goes through these steps:

1. **Load the Case** and list the repository files (sorted, symlink-safe, at most 20,000).
2. **Read the log.** Pick the primary diagnostic (the first line that caused the failure, not the
   last line printed), classify the failure family, compute an error signature, and place the
   failure on the failure frontier.
3. **Profile the architectures.** Statically scan for hazards (x86 flags, intrinsic headers,
   inline assembly, architecture macros, ABI assumptions), attach a semantic anchor to each, and
   score how familiar the target is. An unfamiliar target turns on a more cautious discovery mode.
4. **List hypotheses** only where the evidence is ambiguous, build the source/target delta graph,
   and run one cheap repository-search probe for atomic-symbol failures.
5. **Pick a candidate:** deterministic rules first, then the conditional compiler-flag repair,
   then a model-planned repair if a model is configured.
6. **Review and apply.** The portability and policy critics must both accept it; then all edits
   are checked in memory and written atomically, or none are.
7. **Use build feedback if present.** A later frontier stage is kept, an earlier one is rolled
   back, an unchanged signature counts as no effect, and a new error at the same stage counts as
   partial progress. Hosted runs have no build feedback, so at most one repair is applied there.

The source architecture acts as the control case: the question is always which assumption held on
the source but breaks on the target. A semantic anchor records what an architecture-specific
construct is for before anything is changed, and an existing generic implementation is preferred
over writing new target-specific code.

## Evidence and confidence

Every observation goes into the evidence ledger with its source, excerpt, optional path and line
range, reliability, strength, and stage. Claims keep supporting and contradicting evidence apart,
and `src/evidence/confidence.py` fuses them into a score. These scores are decision thresholds,
not calibrated probabilities:

- A claim backed only by model output is capped at 0.65; one backed only by static patterns is
  capped at 0.75.
- A claim is confirmed only at 0.90 or above, with non-model evidence and no strong contradiction.
- High-consequence claims need 0.80 to justify an edit; critical claims need 0.90.
- A candidate is only as strong as its weakest required high-consequence claim, not an average.
  So a model's opinion alone can never justify a risky edit without support from the log and files.

## Deterministic repair rules

Most edits come from the rules in `src/repair/deterministic.py`. Each one needs a specific log
signal and a unique causal site in the repository, and returns nothing if anything is ambiguous.
None of them contains a Case ID, package name, or benchmark marker.

- **Debian multiarch paths.** Replaces a hard-coded source triplet (for example
  `x86_64-linux-gnu`) in `debian/rules` or a CMake `_DIR` setting, but only when the log itself
  shows that triplet. For `.install` files it also needs debhelper compat 13 or later and only
  touches tokens repeated verbatim in the log. When a generated signed-byte digest chain is found
  by its source shape (generator, declaration, definition, and hash consumer), it is switched to
  `unsigned char` in the same transaction; an incomplete or ambiguous chain is left alone.
- **Debian symbols.** When `dpkg-gensymbols` reports that symbols disappeared on the target, the
  rule reads the symbols diff from the log and tags exactly those symbols `arch=!<target>`, or
  drops the target from an existing positive `arch=` list. A symbol declared only for the target
  gets dpkg-gensymbols' own `#MISSING:` marker. It refuses unless every symbol is found exactly
  once, the log architecture matches the Case target, and no more than a quarter of a file's
  symbols (at most 200) vanished. A bigger loss points to a broken library, so it is not masked.
- **Compiler flags.** Removes x86 flags rejected by a non-x86 compiler from the single causal
  `configure.ac` declaration, or wraps them in a CMake compiler feature check.
- **Build-script abort.** Removes one unconditional `echo ... >&2` plus nonzero `exit` only when
  that exact message appears in the failure log.
- **Other families.** Portable source selection when an x86 intrinsic header is missing, atomic
  library linkage, RPM architecture conditions, hard-coded artifact paths, generated configuration
  markers, architecture guards, portable byte serialization and pointer width, and keeping a CMake
  source list in step with its feature dispatch.

On the 200 public Development Cases, version 0.2.20 edits 15: four through the earlier rules and
eleven through the symbols rule.

## Safety

Repository files and logs are untrusted input. They are read, never executed, and go into model
prompts with a warning not to follow instructions found in them.

- The model cannot run commands. Only `replace_lines`, `insert_before`, `insert_after`, and
  `create_file` edits are accepted, at most five per plan. File deletion is disabled.
- The path policy rejects absolute paths, `..` traversal, oversized path components, VCS and cache
  directories, evaluator-like or hidden paths, the workspace input and output, and every symlink.
  When the Case publishes `allowed_paths`, edits must stay inside them; released
  `buildbench.case.v0.1` Cases without that list are limited to the `input/` prefix.
- The policy critic rejects emptied test files and bypass patterns such as skipping tests, dummy
  artifacts, downloads, `sudo`, or `docker.sock`.
- Each edit names the exact text it replaces, which must occur exactly once. Binary and non-UTF-8
  files are refused. Files are written by same-directory atomic replacement, with before and after
  hashes recorded so the change can be rolled back without Git.

## Language model use

### Hosted runs

In the organizer's environment, `BB_MODEL_BASE_URL`, `BB_MODEL_NAME`, and `BB_MODEL_TOKEN_FILE`
are injected. If any of them is set, all three must be, or startup fails. The agent reads the
bearer token from the file, uses the model alias unchanged for both the FAST and STRONG tiers, and
never writes the token anywhere.

Each request is one Chat Completions call with `model`, `messages`, `max_completion_tokens` (8,192),
and `store=false`. Redirects are refused so the token can't reach another host, and request size,
response size, and JSON nesting are all bounded. A reply is accepted only if it finished normally,
was not refused, and holds valid JSON for its task.

- **A rule matched:** the model critiques the edit first (edit text is clipped in the prompt, not
  in the applied edit). On rejection the agent asks for the model's own analysis and plan, and
  keeps the rule's edit if that plan fails any gate. If the critique call fails, the rule's edit
  is kept and the HTTP status is traced.
- **No rule matched:** the model analyses the diagnostic and causal log excerpt, then plans a
  patch from validated file context. A plan rejected by the gates gets one retry that quotes the
  findings. If the model can't be reached, the run exits nonzero with a credential-free
  diagnostic, and never claims a model call that did not complete.

### Local development

Development needs no paid model. `ARCHIMEDES_MODEL_BACKEND` selects `mock` (no model at all, only
deterministic analysis), `local_openai_compatible` (loopback URLs only), or
`remote_openai_compatible`. The unit tests use a scripted fake client from `tests/`. Local
`bb test` and `bb ready` do not provide model access, so only the Hosted Smoke Test shows a real
model call working.

The `auto` tier starts on FAST and moves to STRONG only on an explicit signal, such as low
confidence on a critical claim, sample disagreement, unknown semantics, or an unfamiliar target
without enough deterministic evidence. `fast` and `strong` force one tier for comparisons.

## Configuration

All settings are environment variables, and none are needed locally.

```text
ARCHIMEDES_MODEL_BACKEND=mock|local_openai_compatible|remote_openai_compatible
ARCHIMEDES_MODEL_BASE_URL=http://127.0.0.1:11434/v1
ARCHIMEDES_MODEL_API_KEY=...              # never logged
ARCHIMEDES_FAST_MODEL=... / ARCHIMEDES_STRONG_MODEL=...
ARCHIMEDES_MODEL_TIER=auto|fast|strong
ARCHIMEDES_MODEL_TIMEOUT_SECONDS=30       # 620 in hosted runs
ARCHIMEDES_MAX_TOTAL_MODEL_CALLS=12
ARCHIMEDES_MAX_STRONG_CALLS=3
ARCHIMEDES_MAX_TOTAL_TOKENS=50000
ARCHIMEDES_MAX_REPAIR_ATTEMPTS=3
ARCHIMEDES_CONTEXT_MODE=archimedes_selected_context|raw_context
ARCHIMEDES_LOG_LEVEL=INFO
ARCHIMEDES_ENABLE_LOCAL_TRACE=true
ARCHIMEDES_TRACE_MAX_EVENTS=500
```

Optional cost limits use `ARCHIMEDES_INPUT_COST_PER_MILLION`, `ARCHIMEDES_OUTPUT_COST_PER_MILLION`,
and `ARCHIMEDES_MAX_ESTIMATED_COST_USD`. No prices are built in; without rates the cost is reported
as unknown. `raw_context` adds up to 32,000 characters of normalized log to the failure analysis.

These feature switches all default to true and exist for ablation runs:
`ENABLE_DETERMINISTIC_FORENSICS`, `ENABLE_CLAIM_CONFIDENCE`, `ENABLE_ARCHITECTURE_DISCOVERY`,
`ENABLE_DELTA_GRAPH`, `ENABLE_SEMANTIC_ANCHORS`, `ENABLE_ACTIVE_PROBES`, `ENABLE_REPAIR_RULES`,
`ENABLE_FAILURE_FRONTIER`.

## Outputs

Besides `agent-result.json`, each run writes `evidence-ledger.json`,
`architecture-constitution.json`, `repair-episode.json`, `metrics.json`, and
`execution-trace.jsonl` to `output/`. The trace records stage changes and decision metadata, never
raw source, raw logs, prompts, model responses, or credentials. It is also mirrored to the process
log with a `[trace]` prefix and can be turned off with `ARCHIMEDES_ENABLE_LOCAL_TRACE=false`.

## Public Development Cases

The organizer's 200 public Ubuntu Launchpad Cases (100 `x86_64-to-aarch64`, 100
`aarch64-to-x86_64`) were regenerated for the 7 September 2026 public phase. The archive (built
`2026-09-07T01:22:06Z`, SHA-256 `94372a28dbd81ece8f5007b83ec4b4ff1b0590de43a528be4461ae7f2bbd675d`)
replaced an earlier one with the same `v0.1` label, so the digest is what identifies it. Five
Debian epoch file names contain colons, so extract it on Linux. It is development material only:
never bundled into the submission, and its packages' licence and attribution terms still apply.

`dev/replay_development_cases.py` unpacks each Debian source package, sets up the workspace, runs
the agent, and keeps every final worktree for the organizer's Development Validation service. A
replay shows what the agent changes; it does not build anything, so it can't show build success.

An audit of all 200 logs found a non-empty diagnostic and a failure family for every one, and a
causal repository path in 106 of them. That measures log reading, not repairs.

## Limitations

- Hosted runs have no build feedback, so the single applied repair has to be right the first
  time. The `bb-build` adapter stays off unless both the executable and the Case declare the exact
  protocol `0.1` interface, and it is always off in hosted mode.
- Rules cover a minority of failures. Anything new or ambiguous depends on a model plan that
  passes every gate, or ends as `no_safe_fix`.
- Host compiler or QEMU results are never treated as proof of target behaviour (QEMU is only
  recorded as an available tool), and the agent never infers a Docker socket, token, Validator
  path, or evaluator setting.

## Method disclosure

- **Model:** hosted runs use the organizer's `BB_MODEL_NAME` alias unchanged, through the
  organizer's OpenAI-compatible endpoint. Prompts are task-specific JSON contracts in
  `src/models/prompts.py`: failure analysis, patch planning, and patch critique.
- **Loop:** evidence-first failure analysis, deterministic rules, bounded patch planning,
  portability and policy review, guarded edits, and frontier-based keep-or-rollback when build
  feedback exists.
- **Retrieval and tools:** bounded repository listing, regex search, static hazard scanning, and
  the deterministic rules. No external knowledge sources.
- **Training and data:** no training or fine-tuning. No hidden Case data, private knowledge base,
  or pre-generated repair is bundled. The synthetic test cases live outside the submission.
- **External services:** only the configured model endpoint. No Docker, Validator, or evaluator
  service is contacted.
- **Reproducibility:** model providers may not be deterministic, but file traversal, IDs, budgets,
  gates, and JSON outputs are. There is no cache shared between Cases and no third-party Python
  dependency.

## Development checks

From the repository root:

```text
python -m unittest discover -s tests -t . -v
python -m compileall agents/archimedes/src
ruff check agents/archimedes/src tests dev
mypy --strict agents/archimedes/src
python dev/evaluate.py --configuration A --case unconditional_x86_sse_flag
```

`dev/evaluate.py` runs the synthetic cases under configurations A to K and records the commit,
timestamp, model, feature settings, token and cost data, frontiers, and whether the causal file
and repair family matched. It never reports these as build success.

The official `bb` commands need a Linux Docker daemon and the kit's pinned images. The submission
ZIP is built only by `bb ready` and must not contain `.env` files, credentials, caches, logs,
traces, or development data. Nothing is uploaded automatically.

## Version history

- **0.2.11:** Added the `rocm-llvm` follow-up repair: after the multiarch path fix, the generated
  SHA-256 identifier in COMGR failed on AArch64, so the digest bytes were made unsigned.
- **0.2.12:** Hardened the runtime: bounded, deterministic, symlink-safe file listing; strict
  limits on metadata, context, model replies, settings, and paths; the compiler-reported line is
  kept in the context of deep source files.
- **0.2.13:** Made the unsigned-digest repair generic (found by code shape, no package names) and
  added the opt-in `bb-build` feedback adapter. An explicit build success outranks a vague
  frontier, so a working repair is never rolled back.
- **0.2.14:** Tightened the edit and model boundaries: `input/` default for released Cases, all
  symlink edits refused, rules need repository-wide uniqueness, JSON depth limits, no redirects,
  and one evidence-backed replan for rejected model plans.
- **0.2.15:** Switched to the organizer's injected model settings, with model critique before a
  deterministic repair and a nonzero exit when hosted model access is unusable.
- **0.2.16:** Read Open Build Service logs as the hosted Validator writes them: strip the `[ NNs]`
  prefix, ignore build-root noise, and prefer errors after the `(user abuild)` build marker.
- **0.2.17:** Kept rpmbuild's `error:` lead-in, classified rpm stage and OBS dependency errors,
  and kept the deterministic repair, tracing the HTTP status, when a critique call fails.
- **0.2.18:** Stopped OBS host noise from deciding the failure family and recognized more
  dependency, rpm, meson test, and compiler out-of-memory messages.
- **0.2.19:** Kept Python logging records out of the failure context and dropped the
  `[repeated N times]` marker from error signatures, so repeated failures compare as unchanged.
- **0.2.20:** Added the Debian symbols rule and clipped each edit's text in critique prompts.
