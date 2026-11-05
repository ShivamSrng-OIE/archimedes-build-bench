# Build-Bench participant contract and platform audit

Last comprehensive review: **4 October 2026 EDT**. The findings below are a
historical v0.2.14 record. The current 21 October 2026 update is in
[`SUBMISSION.md`](SUBMISSION.md): the organizer published model-access settings,
Starter Kit `0.1.0-rc.5`, a new Hosted Smoke requirement, and irreversible
final reconfirmation by the end of 13 November 2026, Beijing time. The new
v0.2.15 package (SHA-256 5278102b...cbcacd) passed Hosted Smoke on 4 November 2026 as submission 20261105T013341Z-320e4be3; v0.2.19 (SHA-256 4f3102f0...a92c9d) fixes hosted-log forensics and is the recommended final version, pending upload and Hosted Smoke.

## Current conclusion

The currently selected final version is the immutable, organizer-qualified `archimedes` v0.2.14
submission `20261003T193341Z-1c80de94`, SHA-256
`714bec9725b99493af37b2ab4e9ea38c9d721ee1fda692aa30eb9c2a7bf9d7ed`. The authenticated portal
showed the full digest and `Final version selected` after the participant explicitly confirmed the
replacement on 4 October 2026 at 10:57 AM EDT. Its Hosted Smoke Test passed 1/1. Its four repaired
public worktrees are byte-identical to the v0.2.11 repairs with four successful ARM64 Development
Validation observations across two runs. Those facts establish packaging, protocol, regression
equivalence, and four public target-build results; they are not a hidden Full Evaluation score or
leaderboard result.

The official Starter Kit `bb ready`
workflow completed under rc.3, including the `hello` Example Case and clean-patch final build. It
produced:

```text
Archive: dist/agent-submission-v0.2.14-ready.zip
Bytes:   100,092
SHA-256: 714bec9725b99493af37b2ab4e9ea38c9d721ee1fda692aa30eb9c2a7bf9d7ed
Run ID:  20261003T193000Z-v0214-final
```

The exact archive was uploaded as immutable submission `20261003T193341Z-1c80de94`. Static checks
passed and the Hosted Smoke Test passed 1/1: submission validation, Case preparation, initial failure
reproduction, Agent completion, canonical patch generation, and final validation all succeeded. The
v0.2.14 changes harden early failure reporting, explicit allowed-path handling, symlink rejection,
bounded JSON depth, redirect-free authenticated model requests, strict configuration values,
repository-wide repair uniqueness, probe working directories, and safe re-planning after a policy
rejection. Its four public-case worktrees remain byte-for-byte identical to the v0.2.11
target-build-qualified repairs. v0.2.14 is qualified and selected for organizer-run Full Evaluation.

The release commit passed 149 tests with two Windows-specific symlink skips and 56 subtests, Ruff,
strict mypy across 75 source files, bytecode compilation, and whitespace checks. The exact uploaded
ZIP has 81 unique regular-file members and 280,433 uncompressed bytes. A second deterministic package
operation reproduced the same SHA-256 and bytes. Direct ZIP inspection found only the four permitted
root entries and no traversal paths, duplicates, symlinks, encrypted entries, prohibited outputs, or
credential-pattern hits; its CRC test passed. Coverage and Bandit were unavailable in the final
Python environment and therefore are not claimed for v0.2.14.

## Sources reviewed

The review covered every participant-facing live page and linked workflow, including collapsed FAQ
answers and authenticated team state:

- [Overview](https://matrix.cstcloud.cn/build-bench/index.html)
- [Task](https://matrix.cstcloud.cn/build-bench/task.html)
- [Submission](https://matrix.cstcloud.cn/build-bench/submission.html)
- [Rules](https://matrix.cstcloud.cn/build-bench/rules.html)
- [Timeline](https://matrix.cstcloud.cn/build-bench/timeline.html)
- [FAQ](https://matrix.cstcloud.cn/build-bench/faq.html)
- [Data and downloads](https://matrix.cstcloud.cn/build-bench/data.html)
- [Development Validation](https://matrix.cstcloud.cn/build-bench/development-validation.html)
- [My submissions](https://matrix.cstcloud.cn/build-bench/my-submissions.html)
- [Leaderboard](https://matrix.cstcloud.cn/build-bench/leaderboard.html)
- authenticated submission, Development Validation run, build-log, team, and selection views
- the current public website source and all currently linked downloadable participant artifacts

The public website source was checked at commit
`08efcb72ab1803ff909c639b1317d5b7329cbe15`; its remote HEAD was rechecked on 3 October and was
unchanged. Live pages and versioned release assets take
precedence over old local notes, cached pages, and unlinked implementation code.

The audit did not request or access hidden Cases, reference repairs, organizer credentials,
organizer-only Validator configuration, Docker Socket access, or another team's private records.

## Page-by-page disposition

| Surface | Controlling requirement or live state | Archimedes disposition |
|---|---|---|
| Overview and Task | Repair an initially failing package worktree across x86_64, aarch64, and riscv64 directions; the canonical patch and clean target build determine success. | The Agent reads bounded evidence, changes only the repository, emits protocol `0.1` output, and never substitutes patch similarity for build success. |
| Submission | Managed Python 3.11 shallow ZIP with `agent.yaml`, exact pins, `src/`, and README; deterministic non-interactive entrypoint; no secrets or runtime debris. | rc.3 official checks pass. The standard-library-only lock is exactly pinned by being empty. ZIP inventory is rechecked before upload. |
| Rules | Foundation-model Repair Agent; no hidden material, Case-ID answers, build bypass, evaluator access, embedded credentials, or undeclared intervention. | No hidden data or credentials are present. Deterministic rules are evidence-gated and do not branch on Case ID or package name. Real-model delivery remains dependent on the organizer's unpublished execution mechanism. |
| Timeline | Participant deadlines and organizer evaluation are separate phases. | A selected qualified version is retained. Uploading a new candidate does not silently replace it. |
| FAQ | No custom runtime image; the Starter Kit does not currently ship participant `bb-build`; Hosted Smoke only qualifies packaging; development and hidden sets are distinct. | The managed runtime is retained. The adapter stays dormant unless the runtime executable and Case metadata both declare the exact protocol-0.1 capability. No smoke/public result is reported as a hidden score. |
| Data and downloads | The live downloads page labels the Starter Kit rc.2 while the participant guide and actual current kit/runtime identify rc.3; the public development corpus contains 200 selectable Cases. | The verified local kit and immutable image references are rc.3. The 200 logs and released sources were replayed without bundling the dataset. The page-label discrepancy is retained as an organizer-side ambiguity. |
| Development Validation | Upload a source-package layout or complete unpacked repaired source trees; select public Cases; preserve run ID/logs; no secrets; results do not affect the leaderboard. | Complete unpacked worktrees are packaged under the documented manifest. Four target builds have passed for the v0.2.11 repair bytes. |
| Team and login | Captain maintains the real roster and account; credentials stay outside artifacts. | Authenticated state was inspected without persisting credentials in the repository. Roster truth remains a participant attestation. |
| My submissions | Uploads are immutable. Static checks and Hosted Smoke qualify a version. Final selection is a distinct state. | v0.2.14 is qualified and selected. The portal shows its complete digest and selection timestamp; v0.2.11 remains immutable history. |
| Evaluation detail | Organizer evaluations move through queued/preparing/evaluating/finalizing/completed; only a completed evaluation can yield a score. | No Full Evaluation record exists for Archimedes at the audit time. |
| Leaderboard | Hosted Smoke and Development Validation do not rank teams; the page currently says each Team may run one Full Evaluation, and only compatible organizer-published completed evaluations count. | No score, rank, or leaderboard claim is made; the account has zero organizer evaluations. |

## Published artifact and corpus checks

The local Starter Kit is `0.1.0-rc.3`. Its official Validator and Example Assets image references
are immutable SHA-256 digests in `runner/common.sh`; the root README and development preflight use
the same values.

The Development Cases v0.1 release contains 200 Ubuntu Launchpad/Debian source-package Cases: 100
`aarch64-to-x86_64` and 100 `x86_64-to-aarch64`. The audited archive is 222,919,777 bytes with
SHA-256 `94372a28dbd81ece8f5007b83ec4b4ff1b0590de43a528be4461ae7f2bbd675d`.
All 1,510 internally checksummed members verified. Five Debian epoch filenames contain colons and
therefore require a Linux filesystem for faithful extraction; direct archive verification was not
affected.

Current v0.2.14 processing of all 200 released target-failure logs produced a non-empty diagnostic,
a recognized family, and no unknown classification:

| Failure family | Logs |
|---|---:|
| Compilation | 81 |
| Test | 42 |
| Packaging | 28 |
| Linking | 23 |
| Configuration | 13 |
| Dependency | 6 |
| Environment | 6 |
| Artifact | 1 |

One hundred six logs yielded a bounded causal source-path candidate. A full actual-source replay
completed all 200 Cases. Completion means the Agent ran and a worktree was preserved; it does not
mean 200 packages built successfully. Four targeted replays modified nine files in total, and every
modified-file digest matches the previously validated v0.2.11 repair.

## Verified public build evidence

| Development Validation run | Case evidence | Result |
|---|---|---|
| `DV-20260921T040125Z-5afed455` | `node-re2`, `buteo-sync-plugins`, `qmidiarp`, and the pre-fix `rocm-llvm` | First three passed target build; `rocm-llvm` exposed the COMGR compile failure. Organizer corrected platform-side processing inside the original run. |
| `DV-20260921T154515Z-2ce55011` | repaired `rocm-llvm` | Intake, canonical repair, ARM64 target build, and artifact verification passed on attempt 1. |

The organizer separately confirmed that the initial upload-format failure came from the platform's
original lack of support for complete unpacked source trees. The platform added that compatibility,
reran the original bundle, and preserved prior logs. This was an infrastructure/input-contract issue,
not evidence that the Agent's package repairs failed.

## Cumulative edge-case hardening in v0.2.12 through v0.2.14

- bounded and symlink-safe repository traversal with a deterministic file cap;
- bounded metadata, repository files, failure evidence, and provider responses;
- strict path containment for input, repository, output, metadata, logs, and probe executables;
- finite numeric configuration and strict provider token-usage parsing;
- validated provider URL schemes, hosts, credential absence, model IDs, rates, and logging levels;
- strict JSON response types and rejection of non-finite constants;
- no overwrite through `create_file`, including an existing empty file;
- causal-line-centered model context rather than blind file prefixes;
- nested credential redaction and JSON-safe trace values;
- trusted executable substitution for architecture probes;
- recovery for curly-quoted compiler/autoconf diagnostics;
- manifest/runtime version consistency checks;
- exact dual-gate discovery of the optional platform `bb-build` executable and task declaration;
- strict, bounded build-feedback JSON, attempt metadata, exit-status, timeout, and finite-number checks;
- safe retention of an evidence-supported repair when build feedback is unavailable or fails;
- explicit build success precedence over a weak or unchanged parsed failure frontier;
- structural discovery of a complete generated-digest declaration/definition/consumer chain without
  package paths, Case IDs, package names, function names, or CMake-variable names;
- a root `pytest.ini` boundary that prevents ignored third-party replay worktrees from entering the
  repository test suite;
- best-effort protocol diagnostics even when configuration or workspace resolution fails early;
- explicit wrapper allowed-path enforcement, rejection of symlink edits, and wrapped filesystem
  errors at the repository boundary;
- a deterministic maximum JSON nesting depth across Case, build-gateway, provider, and model data;
- redirect-free authenticated HTTP requests so bearer credentials cannot cross endpoint boundaries;
- bounded assumption/finding arrays, provider content, configuration strings, ports, and timeouts;
- repository-wide uniqueness requirements for deterministic repairs and flag candidates;
- validated probe working directories and fail-closed handling of ambiguous source matches;
- evidence-safe completion after provider, model, patch, or build-feedback failures, including a
  bounded policy-rejection re-plan path.

## Known limits and unresolved organizer gates

The public participant contract still does not define every formal-evaluation parameter. In
particular, it does not yet provide a participant-facing foundation-model endpoint, credential
injection method, network policy, released build-feedback interface, full resource/time budgets, or
all rerun/scoring details. Public backend source contains future/internal build-feedback machinery,
but unlinked implementation code is not authority by itself. Archimedes therefore invokes build
feedback only if the actual runtime supplies both the executable and the matching Case declaration;
it does not infer sockets, tokens, Validator paths, or limits.

Archimedes supports local and remote OpenAI-compatible models, but its credential-free development
default is `mock`. The deterministic `hello` smoke Case cannot demonstrate organizer-provided model
access. Never put an API key, `.env`, private endpoint, or token into the bundle. Before Full
Evaluation, record the organizer-approved model identifiers, endpoint/provider, network and secret
delivery, budgets, and reproducibility settings.

The live Leaderboard says each Team may run one Full Evaluation, while Rules sections 5.5 and 10
still list exact frequency, rerun, resource, denominator, tie-break, network, credential, and
build-feedback parameters as pending publication. The authenticated submission page exposes no
participant start control and says organizer evaluations are started only by organizers. The safe
operational interpretation is therefore to keep one qualified version explicitly selected by the
November 13 freeze and wait for the organizer-run evaluation; do not infer or attempt a participant
trigger from the Leaderboard wording.

The public pages also vary between “1,000+” and “approximately 1,000” hidden Cases, with the latter
expressly subject to final quality checks. Despite the timeline's protocol-freeze milestone, the
denominator and tie-break details remain marked pending. These inconsistencies do not change the
submission archive contract; they prevent stronger claims about the hidden evaluation size or score
calculation until the organizers publish one controlling final specification.

The official rc.3 workflow succeeds in the preserved privileged Linux Docker environment. Direct
Docker Desktop execution on the host encountered environment-level bind-mount and nested-build
`/dev/null` permission failures, reproduced with a clean unmodified rc.3 kit. Those host failures are
not Agent failures and were not bypassed by changing the official runner.

## Release discipline

1. Treat v0.2.14 submission `20261003T193341Z-1c80de94` as the selected final. Do not replace it
   without a new explicit action-time confirmation.
2. Upload only the exact official `bb ready` ZIP and compare its complete SHA-256 with the portal.
3. Preserve static-check, Hosted Smoke, Development Validation, and organizer-evaluation evidence
   separately; never promote one evidence class into another.
4. Re-audit live Rules, FAQ, Data, Timeline, and portal state immediately before final selection or
   organizer evaluation because published parameters can change.
5. Preserve the v0.2.14 selection and wait for the organizer-run Full Evaluation; no participant
   start control or official score exists at the current audit time.
