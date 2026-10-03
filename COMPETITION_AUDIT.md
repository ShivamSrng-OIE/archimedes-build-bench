# Build-Bench participant contract and platform audit

Last comprehensive review: **3 October 2026 EDT**

## Current conclusion

The currently selected final version is the immutable, organizer-qualified `archimedes` v0.2.11
submission `20260921T155822Z-9f76439b`, SHA-256
`d9b6c41801b58720f493810e5c3b3469b34d39ab98efd065ad1931c32697f4be`. Its Hosted Smoke
Test passed 1/1, and its exact repaired public worktrees have four successful ARM64 Development
Validation observations across two runs. Those facts establish packaging, protocol, and four
public target-build results; they are not a hidden Full Evaluation score or leaderboard result.

Archimedes v0.2.12 is the locally qualified successor. The official Starter Kit `bb ready` workflow
completed under rc.3, including the `hello` Example Case and clean-patch final build. It produced:

```text
Archive: dist/agent-submission-v0.2.12-ready.zip
Bytes:   93,817
SHA-256: 80c22280d3cfe5a94523c5e40c3e8cf3e0a6cce0883625202bfe1e6bc44fd2f7
Run ID:  20261003T070000Z-v0212-final
```

The v0.2.12 changes harden untrusted input, filesystem traversal, provider responses, configuration,
JSON parsing, trace redaction, and source-context selection. Its four public-case worktrees are
byte-for-byte identical to the v0.2.11 target-build-qualified repairs. Until v0.2.12 is uploaded and
passes the organizer Hosted Smoke Test, it must not be described as organizer-qualified or selected.

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
`08efcb72ab1803ff909c639b1317d5b7329cbe15`. Live pages and versioned release assets take
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
| FAQ | No custom runtime image; no currently published participant `bb-build`; Hosted Smoke only qualifies packaging; development and hidden sets are distinct. | The managed runtime is retained. `bb-build` support remains fail-closed until the participant contract publishes it. No smoke/public result is reported as a hidden score. |
| Data and downloads | Starter Kit rc.3 and public 200-Case development data are the current participant artifacts. | Checkout and immutable runtime image references are synchronized to rc.3. The 200 logs and released sources were replayed without bundling the dataset. |
| Development Validation | Upload a source-package layout or complete unpacked repaired source trees; select public Cases; preserve run ID/logs; no secrets; results do not affect the leaderboard. | Complete unpacked worktrees are packaged under the documented manifest. Four target builds have passed for the v0.2.11 repair bytes. |
| Team and login | Captain maintains the real roster and account; credentials stay outside artifacts. | Authenticated state was inspected without persisting credentials in the repository. Roster truth remains a participant attestation. |
| My submissions | Uploads are immutable. Static checks and Hosted Smoke qualify a version. Final selection is a distinct state. | v0.2.11 is qualified and selected. v0.2.12 remains only a local candidate until the portal records otherwise. |
| Evaluation detail | Organizer evaluations move through queued/preparing/evaluating/finalizing/completed; only a completed evaluation can yield a score. | No Full Evaluation record exists for Archimedes at the audit time. |
| Leaderboard | Hosted Smoke and Development Validation do not rank teams; only compatible organizer evaluation batches count. | No score, rank, or leaderboard claim is made. |

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

Current v0.2.12 processing of all 200 released target-failure logs produced a non-empty diagnostic,
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

## Edge-case hardening in v0.2.12

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
- manifest/runtime version consistency checks.

## Known limits and unresolved organizer gates

The public participant contract still does not define every formal-evaluation parameter. In
particular, it does not yet provide a participant-facing foundation-model endpoint, credential
injection method, network policy, final build-feedback interface, full resource/time budgets, or all
rerun/scoring details. Public backend source contains future/internal build-feedback machinery, but
unlinked implementation code is not authority to implement or invoke an unpublished interface.

Archimedes supports local and remote OpenAI-compatible models, but its credential-free development
default is `mock`. The deterministic `hello` smoke Case cannot demonstrate organizer-provided model
access. Never put an API key, `.env`, private endpoint, or token into the bundle. Before Full
Evaluation, record the organizer-approved model identifiers, endpoint/provider, network and secret
delivery, budgets, and reproducibility settings.

The official rc.3 workflow succeeds in the preserved privileged Linux Docker environment. Direct
Docker Desktop execution on the host encountered environment-level bind-mount and nested-build
`/dev/null` permission failures, reproduced with a clean unmodified rc.3 kit. Those host failures are
not Agent failures and were not bypassed by changing the official runner.

## Release discipline

1. Treat v0.2.11 as the selected final until a newer immutable upload independently qualifies.
2. Upload only the exact official `bb ready` ZIP and compare its complete SHA-256 with the portal.
3. Preserve static-check, Hosted Smoke, Development Validation, and organizer-evaluation evidence
   separately; never promote one evidence class into another.
4. Re-audit live Rules, FAQ, Data, Timeline, and portal state immediately before final selection or
   organizer evaluation because published parameters can change.
5. Do not select a replacement final version without the participant's explicit confirmation at the
   irreversible selection step.
