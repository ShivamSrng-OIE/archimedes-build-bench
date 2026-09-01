# Build-Bench CSTCloud compliance audit - 2026-09-01

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
| `submission.html` | Shallow managed-Python 3.11 ZIP using manifest/protocol `0.1`; non-interactive entrypoint; exact dependency pins; no secrets or runtime artifacts. | Hosted static check passed for v0.2.2. Manifest remains protocol `0.1`; standard-library-only lockfile remains valid. |
| `rules.html` | LLM foundation model; no Case-specific answers, hidden material, build bypass, sandbox/evaluator access, or undeclared intervention; reproducibility disclosure required. | Case-specific example path was previously removed; path/policy critics and disclosure exist. Real-model delivery remains unresolved and is a Full Evaluation blocker. |
| `timeline.html` | Protocol freeze scheduled for 31 August; public phase scheduled for 7 September; final Agent selection 13 November; results by 20 November. | Dates recorded, but the live rules still contain pending parameters after the scheduled freeze. Recheck before evaluation. |
| `faq.html` | No custom Docker in rc.2; no current `bb-build`; no secrets; Smoke Test is qualification only; separate versioned Case sets. | Managed runtime retained; `BbBuildFeedbackProvider` remains fail-closed; qualification is not represented as an official score. |
| `data-downloads.html` | Starter Kit rc.2 remains current; Development Cases v0.1 and dataset card released; protocol schemas and runtime images pending. | Starter Kit version matches. Development schema support added in v0.2.3. No dataset content is bundled. |
| `register.html` | Captain account, accurate 1-5 member roster, unique Team name, optional unique institutional email, acceptance of rules. | Team registration is proven indirectly by the participant's authenticated qualified submission; roster accuracy remains a participant responsibility. |
| `login.html` | Captain signs in to manage Team and submissions. | No codebase impact. Credentials are never stored in the repository. |
| `team.html` | Team name is public leaderboard identity; registration/roster is maintained in the portal. | Participant action only; no Agent-bundle impact. |
| `my-submissions.html` | Upload creates immutable versions; static checks and Smoke Test qualify a version; Full Evaluation requires an explicit later action. | v0.2.2 is qualified under submission `20260901T004910Z-0a48b255`; source changes are versioned as v0.2.3. |
| `evaluation-detail.html` | Evaluation advances through queued, preparing, evaluating, finalizing, completed; score appears only after finalization. | No evaluation exists yet; platform health reports Full Evaluation disabled. |
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

- Generated: `2026-08-26T13:41:21Z`
- Cases: 200
- Directions: 100 `aarch64-to-x86_64`, 100 `x86_64-to-aarch64`
- Ecosystem: Ubuntu Launchpad Debian source packages
- Archive bytes: 222,919,804
- Archive SHA-256: `87757c62e7be128ae50c7ce7192d74ae80ea789391acce004e3f6445ca7bcc8a`
- Case schema: `buildbench.case.v0.1`

The archive checksum was verified locally. Its metadata places architecture fields under `platform`
and identifies the target log through `task.initial_failure_evidence` and
`paths.target_failure_log`. Archimedes v0.2.3 now accepts those fields without weakening path
containment. It also has a schema-validated fallback for the public runner shape: when generated
input metadata lacks architecture fields, only the documented case ID and architecture identity
are recovered from the worktree-root `case.json`; that file cannot replace input policy metadata.

All 200 released target-failure logs were processed through Archimedes' deterministic forensics:

| Failure family | Logs |
|---|---:|
| Compilation | 108 |
| Linking | 69 |
| Configuration | 11 |
| Build preparation | 5 |
| Test | 4 |
| Environment | 2 |
| Dependency | 1 |

Every log produced a non-empty primary diagnostic and a recognized family. This is parser coverage
only; it is not a repair-success result and cannot be compared with Verified Build Success Rate.

An organizer announcement published by Nankai University on 27 August states that formal ranking is
planned over 1,016 hidden Cases spanning all six migration directions. It also names Meituan as a
model-API supporter. The participant rules still do not publish the corresponding endpoint,
credential-injection mechanism, network policy, or usage limits, so neither statement authorizes an
Agent-side assumption or embedded credential.

## Qualified immutable version

Participant-supplied CSTCloud evidence:

- Agent/version: `archimedes` `0.2.2`
- Submission ID: `20260901T004910Z-0a48b255`
- Public status: `Qualified`
- Hosted Smoke Test: `hello` passed 1/1
- Pipeline evidence: submission valid, Case prepared, initial failure reproduced, Agent completed,
  canonical patch generated, final validation succeeded

Retained local archive:

`dist/agent-submission-archimedes-v0.2.2-20260901T004910Z-0a48b255.zip`

Local SHA-256:

`96123cf3af5e153663ce47ac45763a994917453defcce8d2a8dd937c760c1a63`

The full platform digest was not present in the supplied log, so byte-for-byte identity remains an
explicit participant check.

## Material changes since the prior local checklist

1. The operational registration/submission portal is now CSTCloud, not GitHub Pages.
2. Registration is active and the participant has a qualified immutable upload.
3. Development Cases v0.1 are released and checksum-verifiable.
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

1. Compare the portal's full SHA-256 for v0.2.2 with the retained local archive.
2. Obtain the organizer-published real-model/network/credential configuration and perform an
   organizer-compatible real-model smoke run.
3. Wait for Full Evaluation readiness and frozen operational limits.
4. Decide whether to preserve qualified v0.2.2 for the first Full Evaluation or upload and qualify
   v0.2.3 after the released-schema compatibility change and full readiness checks.
