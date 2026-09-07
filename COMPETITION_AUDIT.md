# Build-Bench CSTCloud compliance audit - 2026-09-07

## Scope and authority

This audit reviewed every participant-facing HTML page reachable from the live CSTCloud Build-Bench
site, the authenticated-workflow pages that are publicly served, the platform health response, and
every published resource linked from Data & Downloads. The live CSTCloud site and versioned release
artifacts take precedence over the older GitHub Pages preview and local planning notes.

Audit base URL: <https://matrix.cstcloud.cn/build-bench/>

The review did not access hidden Cases, organizer-only records, private credentials, evaluator
secrets, or another Team's data. Authenticated submission evidence is limited to the status and log
the participant supplied for their own version.

## Page-by-page review

| Page | Live participant requirement or state | Archimedes disposition |
|---|---|---|
| `index.html` | Runnable LLM Agent; x86_64, aarch64, riscv64 bidirectional scope; verified target builds determine success. | Architecture model supports all three ISAs; worktree repair and canonical-patch model documented. |
| `task.html` | Read Case evidence, autonomously modify only the permitted worktree, write structured completion output; no patch-similarity scoring. | Workspace boundary, result writer, guarded edits, and clean-worktree assumptions implemented. |
| `submission.html` | Shallow managed-Python 3.11 ZIP using manifest/protocol `0.1`; non-interactive entrypoint; exact dependency pins; no secrets or runtime artifacts. | Hosted static check and Smoke Test passed for v0.2.8. Manifest remains protocol `0.1`; standard-library-only lockfile remains valid. |
| `rules.html` | LLM foundation model; no Case-specific answers, hidden material, build bypass, sandbox/evaluator access, or undeclared intervention; reproducibility disclosure required. | Case-specific example path was previously removed; path/policy critics and disclosure exist. Real-model delivery remains unresolved and is a Full Evaluation blocker. |
| `timeline.html` | Public development and validation are listed as open on 7 September; final Agent selection is 13 November; results are due by 20 November. | Public resources were refreshed, but the live rules still contain pending parameters after the scheduled freeze. Recheck before evaluation. |
| `faq.html` | No custom Docker in rc.2; no current `bb-build`; no secrets; Smoke Test is qualification only; separate versioned Case sets. | Managed runtime retained; `BbBuildFeedbackProvider` remains fail-closed; qualification is not represented as an official score. |
| `data-downloads.html` | Starter Kit rc.2 remains current; Development Cases v0.1 were regenerated for the public phase; protocol schemas and runtime images remain pending. | Current release digest verified. Dataset-informed parser, context, and repair changes are versioned as v0.2.9. No dataset content is bundled. |
| `register.html` | Captain account, accurate 1-5 member roster, unique Team name, optional unique institutional email, acceptance of rules. | Team registration is proven indirectly by the participant's authenticated qualified submission; roster accuracy remains a participant responsibility. |
| `login.html` | Captain signs in to manage Team and submissions. | No codebase impact. Credentials are never stored in the repository. |
| `team.html` | Team name is public leaderboard identity; registration/roster is maintained in the portal. | Participant action only; no Agent-bundle impact. |
| `my-submissions.html` | Upload creates immutable versions; static checks and Smoke Test qualify a version; Full Evaluation requires an explicit later action. | v0.2.8 is qualified under submission `20260907T040030Z-2c652819`; Full Evaluation remains disabled in the portal. |
| `evaluation-detail.html` | Evaluation advances through queued, preparing, evaluating, finalizing, completed; score appears only after finalization. | No Full Evaluation is documented for this candidate; current platform health reports Full Evaluation disabled. |
| `leaderboard.html` | Hosted Smoke Tests do not affect ranking; only compatible organizer-published Full Evaluations are compared; Build Success Rate is primary. | No score claim is made from the 1/1 Smoke Test or public-log parser audit. |
| `contact.html` | Official support email is `buildbench-challenge@googlegroups.com`. | Recorded in the submission checklist for unresolved policy questions. |

## Published release evidence

### Starter Kit

- Version: `v0.1.0-rc.2`
- Local checkout `VERSION`: `0.1.0-rc.2`
- Status: still labeled published pre-release

The latest public Agent-baseline repository also remains on Starter Kit rc.2. No manifest or Agent
protocol migration is currently justified.

### Development Cases v0.1

- Generated: `2026-09-07T01:22:06Z`
- Cases: 200
- Directions: 100 `aarch64-to-x86_64`, 100 `x86_64-to-aarch64`
- Ecosystem: Ubuntu Launchpad Debian source packages
- Archive bytes: 222,919,777
- Archive SHA-256: `94372a28dbd81ece8f5007b83ec4b4ff1b0590de43a528be4461ae7f2bbd675d`
- Case schema: `buildbench.case.v0.1`

The outer archive checksum and all 1,510 internal checksummed members were verified locally. This
archive supersedes the 26 August bytes under the same `v0.1` label, so the digest is required for
unambiguous provenance. Five members contain Debian epoch colons and cannot be materialized on
NTFS; direct ZIP verification passed and full extraction requires a Linux filesystem.

Its metadata places architecture fields under `platform`
and identifies the target log through `task.initial_failure_evidence` and
`paths.target_failure_log`. Archimedes accepts those fields without weakening path
containment. It also has a schema-validated fallback for the public runner shape: when generated
input metadata lacks architecture fields, only the documented case ID and architecture identity
are recovered from the worktree-root `case.json`; that file cannot replace input policy metadata.

All 200 released target-failure logs were processed through Archimedes' deterministic forensics:

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

Every log produced a non-empty primary diagnostic and a recognized family after suppressing benign
zero summaries, echoed compiler/Cargo commands, environment inventories, warning-only probes, and
generic cascades. Ten logs exceed two megabytes; bounded head-plus-tail loading kept their terminal
failures. One hundred six logs yielded a repository-path candidate for guarded context lookup, up
from 92 before the second release-driven parser pass. No selected primary matched the audited set
of known command/inventory/warning-only false-positive prefixes. These are parser/context coverage
results only and cannot be compared with Verified Build Success Rate.

Actual-source replays of released cases `node-re2`, `rocm-llvm`, and `buteo-sync-plugins` selected
generic Debian multiarch repairs. The first two each changed one line in `debian/rules`, replacing a
hard-coded source triplet with the target value obtained from `dpkg-architecture`; `rocm-llvm` fixed
a CMake package-directory argument explicitly present in the failure log. The third case declares
debhelper compat 13 and changed only the two `.install` tokens named missing by the log to use
`${DEB_HOST_MULTIARCH}`; an unobserved hard-coded token was left untouched. All edits passed the
Agent's confidence, portability, policy, and atomic-edit gates without model calls. No
organizer-equivalent target build was available, so these are candidate-selection evidence, not
verified repair successes.

An additional actual-source replay of released case `qmidiarp` selected a generic portable-flag
repair for its non-x86 target compiler diagnostics. It changed only the canonical `configure.ac`
assignment, removed exactly the three rejected x86-only flags (`-msse`, `-msse2`, and
`-mfpmath=sse`), preserved the remaining portable flags, and left generated `configure` untouched.
This is also candidate-selection evidence rather than a verified target-build success.

The resulting v0.2.9 candidate at commit `291081f` passes 100 tests plus 20 subtests, Ruff, strict
mypy over 73 source files, bytecode compilation, and the official static Agent check. Two official
package runs produced the same 90,304-byte ZIP with SHA-256
`6a86ef3249bceebf4e06a037863942284c22715867413e67dc56069c50465668`. It is not yet qualified;
qualified v0.2.8 remains the portal fallback.

An organizer announcement published by Nankai University on 27 August states that formal ranking is
planned over 1,016 hidden Cases spanning all six migration directions. It also names Meituan as a
model-API supporter. The participant rules still do not publish the corresponding endpoint,
credential-injection mechanism, network policy, or usage limits, so neither statement authorizes an
Agent-side assumption or embedded credential.

## Qualified immutable version

Live CSTCloud portal evidence captured on 7 September 2026:

- Agent/version: `archimedes` `0.2.8`
- Submission ID: `20260907T040030Z-2c652819`
- Public status: `Qualified`
- Hosted Smoke Test: `hello` passed 1/1
- Pipeline evidence: submission valid, Case prepared, initial failure reproduced, Agent completed,
  canonical patch generated, final validation succeeded

Uploaded local archive:

`dist/agent-submission.zip`

Local SHA-256:

`da9a37379fb6e92022c8f0e9e21c7228af6d4a358238cfaf519687baa584dd79`

The portal displayed the matching digest prefix `da9a37379fb6…`; the complete local digest was
recomputed immediately before browser upload. The immutable version is qualified but has no Full
Evaluation because the portal action remains disabled.

## Material changes since the prior local checklist

1. The operational registration/submission portal is now CSTCloud, not GitHub Pages.
2. Registration is active and the participant has a qualified immutable v0.2.8 upload.
3. Development Cases v0.1 were silently regenerated under the same label; the 7 September digest
   and all internal checksums are verified.
4. The released development metadata shape adds nested `platform`, `task`, and `paths` fields that
   were not handled by the old Agent loader.
5. Full Evaluation is not merely awaiting a participant click: the public platform health response
   reports it disabled and has no available Full Evaluation worker.
6. Protocol schemas, official runtime images, and important operational policies remain unpublished
   despite the scheduled protocol-freeze date.
7. The Hosted Smoke Test proves packaging and the lightweight repair path, not use of a real
   foundation model or performance on the public/hidden Cases.

## Unresolved gates

The live rules still defer these values:

- score denominator and tie-breaker;
- Agent/build timeouts and iteration/build-request budgets;
- CPU, memory, storage, and concurrency limits;
- network access and external-API rules;
- organizer-managed credential delivery;
- upload, Smoke Test, and Full Evaluation frequency limits;
- cancellation, rerun, correction, and feedback-visibility rules;
- final build-feedback protocol.

Archimedes must not guess these values or implement an unpublished `bb-build` interface. The next
evaluation candidate must be re-audited when the CSTCloud rules carry the promised version/publication
update.

## Required next decisions

1. Obtain the organizer-published real-model/network/credential configuration and perform an
   organizer-compatible real-model smoke run.
2. Wait for Full Evaluation readiness and frozen operational limits.
3. Qualify and reconfirm the exact v0.2.9 candidate before requesting the first Full Evaluation;
   retain qualified v0.2.8 as the fallback until that succeeds.
