# Archimedes release, submission, and evaluation record

Last checked: **4 November 2026 EDT**

## Current v0.2.15 preparation

The [current participation guide](https://matrix.cstcloud.cn/build-bench/submission.html?lang=en#model-access)
now specifies organizer-injected `BB_MODEL_BASE_URL`, `BB_MODEL_NAME`, and
`BB_MODEL_TOKEN_FILE`. Hosted Smoke requires both a completed official model call
and independent repair validation. An older Smoke pass must be rerun, and an
earlier final selection must be explicitly reconfirmed. Final confirmation
cannot be changed. The deadline is the end of **13 November 2026, Beijing time**.
The [download page](https://matrix.cstcloud.cn/build-bench/data-downloads.html?lang=en)
publishes Starter Kit `0.1.0-rc.5`; its release notes correct generated patches
for added and deleted text files.
The current [rules](https://matrix.cstcloud.cn/build-bench/rules.html) and
[My Submissions](https://matrix.cstcloud.cn/build-bench/my-submissions.html)
pages agree that confirmation is irreversible. Some FAQ text still describes
changing a selection before the deadline; follow the current rules and
confirmation UI for this release.

```text
Agent version:       0.2.16 (recommended final)
Archive:             dist/agent-submission.zip
Archive bytes:       102,735
SHA-256:             a9f36b97a584687522cbe3af3e656441ff9563b7723a6b597698c22e13164e25
Starter Kit:         0.1.0-rc.5, official ZIP SHA-256 1bd384af...dcfb56 verified
Archive contents:    81 members, roots README.md, agent.yaml, requirements.lock, src
Local Python tests:  168 passed, 2 skipped, 62 subtests passed (Python 3.11.15)
Strict mypy:         no issues in 75 source files
Official bb ready:   succeeded, hello 1/1 incl. final validation, same SHA-256
                     (run 20261105T024325Z-32551, kit on WSL ext4 home);
                     forensics: family packaging, "bad exit status ... (%build)"
Archive audit:       81 members byte-identical to source, CRC ok, no symlinks
                     or secrets, all requirements pinned with ==
Forensics diff:      22 logs (16 synthetic, 4 Launchpad, 2 OBS) old vs new:
                     only the 2 OBS logs changed, both as intended
Agent-image replay:  genuine OBS log with build-root noise repaired in mock,
                     simulated hosted (1 compliant request, token not leaked)
                     and hosted-endpoint-down modes
Hosted Smoke:        pending
New submission ID:   pending
Final confirmation:  pending

Previous candidate:  0.2.15, SHA-256 5278102b...cbcacd, submission
                     20261105T013341Z-320e4be3, Hosted Smoke passed 1/1 (model
                     access, Agent execution, final validation). Its hosted
                     agent.log showed the forensics defect fixed in 0.2.16, so do
                     not confirm it.
```

### 4 November 2026 recheck

The website still publishes Starter Kit `0.1.0-rc.5` (documentation revision
`2026-10-20-patch-modes`) and the 13 November 2026 (UTC+8) confirmation deadline.
The site's direct model-access guide link returns 404, but the identical guide ships
in the kit as `model-access/guide.en.md` (v0.1, 15 October 2026). The website
publishes no per-request token, request-count, or timeout limits beyond that guide.

v0.2.15 changed after the 21 October package: a hosted-model error during the
critique of a deterministic repair no longer aborts the run, and a critique
rejection falls back to the deterministic candidate when the model offers no
admissible alternative. Both paths have unit tests.

The earlier local `bb ready` failures (`/dev/null: Permission denied` in the
Validator's nested OBS chroot) were caused by running the kit from WSL's `/tmp`,
which is mounted `nodev`. With the unmodified rc.5 kit extracted under the WSL
ext4 home directory, the official `./bb ready --agent ./agents/archimedes --json`
exited 0 with `"status": "succeeded"`: `hello` reproduced its initial failure, the
Agent completed, the canonical patch was generated, and final validation
succeeded (1/1). The ready artifact's SHA-256 equals the `dist/` archive above.
Evidence is retained under `runs/ready/20261105T004659Z-v0215/`.

The exact Agent was also replayed in `python:3.11.9-slim-bookworm` with a
simulated hosted endpoint. It sent one `/v1/chat/completions` request containing
only `model`, `messages`, `store: false`, and `max_completion_tokens`, with the
bearer token read from the injected file; the token never appeared in logs or
output. With the endpoint unreachable it still applied the repair. Hosted Smoke
remains the qualification gate for the official model call.

## Historical v0.2.14 selection (requires new validation)

## Selected organizer-qualified final

```text
Agent:           archimedes
Agent version:   0.2.14
Submission ID:   20261003T193341Z-1c80de94
Portal status:   Qualified; Final version selected
Hosted Smoke:    hello passed 1/1, including clean-patch final validation
Selected:        4 October 2026 at 10:57 AM EDT
Source commit:   093b7f2
Archive:         dist/agent-submission-v0.2.14-ready.zip
Archive bytes:   100,092
SHA-256:         714bec9725b99493af37b2ab4e9ea38c9d721ee1fda692aa30eb9c2a7bf9d7ed
```

The authenticated portal confirmed this selection, showed the complete SHA-256 above, and marked the
v0.2.14 history row `Final version selected`. It showed no completed organizer Full Evaluation,
score, or rank. The organizer—not a Hosted Smoke Test or Development Validation run—creates the
canonical hidden evaluation and leaderboard result.

The exact v0.2.11 repaired worktree passed `rocm-llvm` ARM64 Development Validation in run
`DV-20260921T154515Z-2ce55011`. The organizer-corrected earlier run
`DV-20260921T040125Z-5afed455` passed `node-re2`, `buteo-sync-plugins`, and `qmidiarp`. This is four
public target-build observations across two runs. The selected v0.2.14 replay and focused bundle
produce byte-identical repair files. This is regression equivalence to four public build observations,
not a single four-Case v0.2.14 run or a hidden score.

## Qualification and regression evidence

```text
Agent version:   0.2.14
Starter Kit:     0.1.0-rc.3
Official ready:  succeeded
Ready run ID:    20261003T193000Z-v0214-final
Source commit:   093b7f2
Archive:         dist/agent-submission-v0.2.14-ready.zip
Archive bytes:   100,092
SHA-256:         714bec9725b99493af37b2ab4e9ea38c9d721ee1fda692aa30eb9c2a7bf9d7ed
Example Case:    hello passed initial reproduction, Agent, patch, and final build
Submission ID:   20261003T193341Z-1c80de94
Portal status:   Qualified; Hosted Smoke passed 1/1; Final version selected
```

The v0.2.14 archive was emitted by the official `bb ready` workflow in a privileged Linux Docker
environment using the rc.3 immutable runtime images. Direct Docker Desktop runs of both this checkout
and an untouched rc.3 kit encountered host bind-mount and nested-build permission failures. The
official runner was not modified to bypass them.

The full local suite (149 passed, two platform-specific symlink tests skipped, 56 subtests), Ruff,
strict typing over 75 source files, bytecode compilation, archive/source comparison, secret scan,
and official readiness are green at release commit `093b7f2`. Coverage and Bandit were not installed
in the final Python environment, so no v0.2.14 claim is made for those optional checks. All 200 public
source replays completed. The four modified public worktrees reproduce the same nine file digests as
the v0.2.11 repairs that already received target-build validation. The v0.2.14 focused Worktree
Bundle is also byte-for-byte identical to the validated v0.2.11 bundle. These are strong regression
checks but do not replace the organizer's Hosted Smoke qualification or public target-build evidence.

The portal records digest prefix `714bec9725b9...`, passed static checks, and a successful Hosted
Smoke Test with final validation. The displayed terminal log was transcribed to
`runs/submission-20261003T193341Z-1c80de94/PORTAL_EVIDENCE.md` with SHA-256
`f988aa35d1401db25bd35f752677798f8895909cea580e63d51b0cfb4fc9bce8`; it is clearly labeled as a
transcription because the download control did not yield a local file. After an explicit action-time
confirmation, v0.2.14 replaced v0.2.11 as the selected final on 4 October 2026 at 10:57 AM EDT.
The final portal state is transcribed separately in
`runs/submission-20261003T193341Z-1c80de94/FINAL_SELECTION.md`, SHA-256
`3c41ee48ca2df23c0f1b6fea1089bb4b1803c8fccff02344b0b6debde5965a05`.

An independent audit of the exact ZIP found 81 unique members, only `agent.yaml`,
`requirements.lock`, `README.md`, and `src/` at the root, deterministic timestamps, regular deflated
members, no path traversal, symlinks, encrypted entries, prohibited outputs, duplicate names, or
detected credential patterns, and a successful ZIP CRC test. Its uncompressed content is 280,433
bytes and exactly matches the committed Agent source.

## Package contract

Upload only a checked archive from the current Starter Kit. Its ZIP root must contain:

```text
agent.yaml
requirements.lock
README.md
src/
```

It must not contain the Starter Kit, tests, Development Cases, worktree bundles, caches, logs,
credentials, `.env`, patches, model weights, symlinks, virtual environments, or previous runs.
Every dependency must be exactly pinned; Archimedes currently needs no third-party Python package.

The entrypoint is non-interactive and may modify only `/workspace/work/repo`. It reads bounded input
from `/workspace/input`, writes structured output under `/workspace/output`, and does not start
Docker or contact the Validator directly. The organizer derives and reapplies the canonical patch to
a clean Case before target build validation.

## Foundation-model gate

The rules require a foundation-model Repair Agent. Archimedes supports explicitly configured local
or remote OpenAI-compatible providers, but its safe credential-free development default is `mock`.
The deterministic `hello` Case and deterministic public repair paths do not prove that a real model
will be available in Full Evaluation.

At the time of v0.2.14, participant pages did not publish the organizer's model endpoint, model IDs,
credential injection, network policy, or released build-feedback interface. v0.2.14 could use
`bb-build` only when the actual runtime provides both the executable and the exact protocol-0.1 Case
declaration; it never infers gateway sockets, tokens, or Validator configuration. Never put a secret
into the ZIP. Before Full Evaluation, retain the exact organizer-approved provider/model identifiers,
endpoint type, network and secret-delivery method, call/token/cost/time/build budgets, prompts, tools,
and nondeterminism settings.

## Pre-upload procedure

From the current Starter Kit root in a supported Linux/Docker environment:

```bash
./bb version
./bb doctor
./bb check --agent ./agents/archimedes
./bb model-check
./bb ready --agent ./agents/archimedes --json
# If local Validator setup fails independently of the Agent, use the documented
# model-dependent path: ./bb package --agent ./agents/archimedes
```

Do not claim `bb ready` success without a zero exit, `"status": "succeeded"`,
and matching artifact SHA-256. The published model-access guide permits local
`bb check` plus `bb package` before Hosted Smoke for model-dependent Agents.
Verify the archive inventory and scan the staged archive for prohibited content.

## Portal procedure

1. Recheck [Model access](https://matrix.cstcloud.cn/build-bench/submission.html?lang=en#model-access),
   [Rules](https://matrix.cstcloud.cn/build-bench/rules.html), and
   [Data and downloads](https://matrix.cstcloud.cn/build-bench/data-downloads.html).
2. Upload the exact immutable ZIP through
   [My submissions](https://matrix.cstcloud.cn/build-bench/my-submissions.html).
3. Compare the complete portal digest with the local SHA-256 and preserve the submission ID.
4. Wait for static checking and run/await Hosted Smoke exactly as the portal directs.
5. Inspect every retained packaging, execution, patch, and final-build diagnostic.
6. After a current Hosted Smoke pass, explicitly confirm the intended qualified
   version in My Submissions. The final version cannot be changed afterward.
7. Preserve any later organizer Full Evaluation ID and permitted logs separately from public
   Development Validation and Hosted Smoke evidence.

## Development Validation procedure

The validation service accepts the documented source-package layout or complete unpacked repaired
source trees. A worktree bundle is separate from the Agent ZIP. It contains root `manifest.json`
schema `0.1`, suite `development-recommended-v1`, and repaired sources under
`cases/<case-id>/worktree/input/`.

Never include API keys, passwords, tokens, Git metadata, quilt state, or unrelated files. Preserve
the run ID and logs. A platform failure must be distinguished from a source preparation,
configuration, compile, link, test, packaging, or artifact failure before changing the Agent.

## Evidence boundaries

- `bb ready` success: exact local packaging and Example Case evidence.
- Hosted Smoke 1/1: organizer runtime and one smoke Case; not a score.
- Development Validation pass: one selected public target build; not a hidden evaluation.
- Completed Full Evaluation: only source of an official hidden score.
- Leaderboard entry: only the organizer's compatible published evaluation batch determines rank.

## Historical immutable records

Keep these for provenance, but do not attribute later code to them:

| Version | Submission | SHA-256 | Evidence |
|---|---|---|---|
| 0.2.9 | `20260907T041925Z-629a77c4` | `6a86ef3249bceebf4e06a037863942284c22715867413e67dc56069c50465668` | Qualified; Hosted Smoke 1/1 |
| 0.2.11 | `20260921T155822Z-9f76439b` | `d9b6c41801b58720f493810e5c3b3469b34d39ab98efd065ad1931c32697f4be` | Qualified; Hosted Smoke 1/1; former final selection |
| 0.2.12 | `20261003T110859Z-cf35ceb6` | `80c22280d3cfe5a94523c5e40c3e8cf3e0a6cce0883625202bfe1e6bc44fd2f7` | Qualified; Hosted Smoke 1/1; not selected |
| 0.2.13 | `20261003T181216Z-814ddbfa` | `02650f9e429e7e780c5d610ba148c8fb08972b57246663442cecb1c16d20cb09` | Qualified; Hosted Smoke 1/1; not selected |
| 0.2.14 | `20261003T193341Z-1c80de94` | `714bec9725b99493af37b2ab4e9ea38c9d721ee1fda692aa30eb9c2a7bf9d7ed` | Qualified; Hosted Smoke 1/1; selected final |

The comprehensive current rules/site/corpus audit is in `COMPETITION_AUDIT.md`.
