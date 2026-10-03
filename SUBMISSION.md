# Archimedes release, submission, and evaluation record

Last checked: **3 October 2026 EDT**

## Selected organizer-qualified final

```text
Agent:           archimedes
Agent version:   0.2.11
Submission ID:   20260921T155822Z-9f76439b
Portal status:   Qualified; Final version selected
Hosted Smoke:    hello passed 1/1, including clean-patch final validation
Selected:        2 October 2026 at 11:41 PM EDT
Source commit:   e23bb1a
Archive:         dist/agent-submission-v0.2.11-ready.zip
Archive bytes:   91,407
SHA-256:         d9b6c41801b58720f493810e5c3b3469b34d39ab98efd065ad1931c32697f4be
```

The live portal preserved this selection after reload. It showed no completed organizer Full
Evaluation, score, or rank at the last check. The organizer—not a Hosted Smoke Test or Development
Validation run—creates the canonical hidden evaluation and leaderboard result.

The exact v0.2.11 repaired worktree passed `rocm-llvm` ARM64 Development Validation in run
`DV-20260921T154515Z-2ce55011`. The organizer-corrected earlier run
`DV-20260921T040125Z-5afed455` passed `node-re2`, `buteo-sync-plugins`, and `qmidiarp`. This is
four public target-build observations across two runs, not a single four-Case v0.2.11 run or a
hidden score.

## Organizer-qualified successor awaiting explicit final selection

```text
Agent version:   0.2.12
Starter Kit:     0.1.0-rc.3
Official ready:  succeeded
Ready run ID:    20261003T070000Z-v0212-final
Archive:         dist/agent-submission-v0.2.12-ready.zip
Archive bytes:   93,817
SHA-256:         80c22280d3cfe5a94523c5e40c3e8cf3e0a6cce0883625202bfe1e6bc44fd2f7
Example Case:    hello passed initial reproduction, Agent, patch, and final build
Submission ID:   20261003T110859Z-cf35ceb6
Portal status:   Qualified; Hosted Smoke passed 1/1
```

The v0.2.12 archive was emitted by the official `bb ready` workflow in a privileged Linux Docker
environment using the rc.3 immutable runtime images. Direct Docker Desktop runs of both this checkout
and an untouched rc.3 kit encountered host bind-mount and nested-build permission failures. The
official runner was not modified to bypass them.

The full local suite, lint, strict typing, bytecode compilation, archive/source comparison, secret
scan, and preflight are green at release commit `83f3e92`. All 200 public source replays completed;
the four modified public worktrees reproduce the same nine file digests as the v0.2.11 repairs that
already received target-build validation. These are strong regression checks but do not qualify the
v0.2.12 ZIP on the organizer platform.

The portal records the exact digest prefix `80c22280d3cf…`, passed static checks, and a successful
Hosted Smoke Test with final validation. The complete downloaded organizer log is retained under
`runs/submission-20261003T110859Z-cf35ceb6/smoke-test.log`. Uploading and qualification did not
replace v0.2.11; selecting v0.2.12 is a distinct consequential action.

## Package contract

Only upload the exact `bb ready` artifact. Its ZIP root must contain:

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

The current participant pages still do not publish the organizer's model endpoint, model IDs,
credential injection, network policy, or final build-feedback interface. Do not guess an internal
`bb-build` contract and never put a secret into the ZIP. Before Full Evaluation, retain the exact
organizer-approved provider/model identifiers, endpoint type, network and secret-delivery method,
call/token/cost/time/build budgets, prompts, tools, and nondeterminism settings.

## Pre-upload procedure

From the Starter Kit root in the supported Linux/Docker environment:

```bash
./bb version
./bb doctor
./bb ready --agent ./agents/archimedes --json
python dev/submission_preflight.py --json
```

Readiness requires a zero process exit, `"status": "succeeded"`, an existing ZIP, and exact
agreement between the JSON artifact SHA-256 and a fresh file hash. Then verify the archive inventory
and scan the staged archive—not merely the working tree—for prohibited content.

## Portal procedure

1. Recheck [Rules](https://matrix.cstcloud.cn/build-bench/rules.html),
   [FAQ](https://matrix.cstcloud.cn/build-bench/faq.html),
   [Data and downloads](https://matrix.cstcloud.cn/build-bench/data.html), and
   [Timeline](https://matrix.cstcloud.cn/build-bench/timeline.html).
2. Upload the exact immutable ZIP through
   [My submissions](https://matrix.cstcloud.cn/build-bench/my-submissions.html).
3. Compare the complete portal digest with the local SHA-256 and preserve the submission ID.
4. Wait for static checking and run/await Hosted Smoke exactly as the portal directs.
5. Inspect every retained packaging, execution, patch, and final-build diagnostic.
6. Keep v0.2.11 selected unless and until v0.2.12 independently qualifies and the participant
   explicitly confirms replacing the selected final at that irreversible step.
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
| 0.2.11 | `20260921T155822Z-9f76439b` | `d9b6c41801b58720f493810e5c3b3469b34d39ab98efd065ad1931c32697f4be` | Qualified; Hosted Smoke 1/1; selected final |
| 0.2.12 | `20261003T110859Z-cf35ceb6` | `80c22280d3cfe5a94523c5e40c3e8cf3e0a6cce0883625202bfe1e6bc44fd2f7` | Qualified; Hosted Smoke 1/1; not selected |

The comprehensive current rules/site/corpus audit is in `COMPETITION_AUDIT.md`.
