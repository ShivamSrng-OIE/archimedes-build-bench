# Build-Bench submission and evaluation checklist for Archimedes

Last live-site audit: **7 September 2026 UTC**.

This checklist is subordinate to the current CSTCloud platform, versioned competition rules, and
the Starter Kit used to create an upload. The former GitHub Pages site is no longer the operational
submission portal.

Official participant pages:

- [Competition overview](https://matrix.cstcloud.cn/build-bench/index.html)
- [Challenge definition](https://matrix.cstcloud.cn/build-bench/task.html)
- [Participation and package contract](https://matrix.cstcloud.cn/build-bench/submission.html)
- [Competition rules](https://matrix.cstcloud.cn/build-bench/rules.html)
- [Timeline](https://matrix.cstcloud.cn/build-bench/timeline.html)
- [FAQ](https://matrix.cstcloud.cn/build-bench/faq.html)
- [Data and downloads](https://matrix.cstcloud.cn/build-bench/data-downloads.html)
- [My Submissions](https://matrix.cstcloud.cn/build-bench/my-submissions.html)
- [Leaderboard](https://matrix.cstcloud.cn/build-bench/leaderboard.html)
- [Contact](https://matrix.cstcloud.cn/build-bench/contact.html)

## Current immutable submission

The following organizer-run qualification evidence was reported from the CSTCloud portal:

```text
Agent:          archimedes
Agent version:  0.2.9
Submission ID:  20260907T041925Z-629a77c4
Status:         Qualified
Hosted Smoke:   hello succeeded (1/1)
Validation:     static check, Agent execution, canonical patch, and final build all succeeded
```

The locally retained archive is:

```text
dist/agent-submission-archimedes-v0.2.9-20260907T041925Z-629a77c4.zip
SHA-256: 6a86ef3249bceebf4e06a037863942284c22715867413e67dc56069c50465668
```

The complete local digest was recomputed immediately before browser upload, and the portal displays
the matching prefix `6a86ef3249bc…` for the immutable version.

Qualification proves that this immutable bundle satisfies the current static package checks and the
Hosted Smoke Test contract. It does **not** prove repair performance on the public Development Cases,
compliance with the still-unpublished foundation-model delivery policy, or success on Full/hidden
evaluation.

Any source, manifest, dependency, or configuration change after this qualified `0.2.9` upload
requires a new version and immutable upload. Do not attribute later local changes to submission
`20260907T041925Z-629a77c4`.

Qualified v0.2.9 release:

```text
Source commit:  291081f
Submission ID:  20260907T041925Z-629a77c4
Archive:        dist/agent-submission-archimedes-v0.2.9-20260907T041925Z-629a77c4.zip
Archive bytes:  90,304
SHA-256:        6a86ef3249bceebf4e06a037863942284c22715867413e67dc56069c50465668
Static check:   passed (79 files; standard-library-only dependency lock)
Test suite:     100 passed, 20 subtests passed
Quality gates:  Ruff passed; strict mypy passed for 73 source files; compileall passed
Reproducibility: two consecutive official package runs produced the same SHA-256
Hosted Smoke:   hello succeeded (1/1); final clean-patch validation succeeded
```

The official local `bb ready` orchestration is still blocked before testing by the unpublished
`buildbench-validator-runtime:v0` and `buildbench-example-assets:v0` images. This is an organizer
asset-availability limitation. The organizer-hosted pipeline supplied those assets and qualified
the exact immutable v0.2.9 archive successfully.

## Current platform and release state

At the audit time:

- Starter Kit `v0.1.0-rc.2` remains the latest published kit and matches this checkout's `VERSION`.
- Managed Python 3.11 and Agent protocol `0.1` remain the published submission contract.
- Development Cases `v0.1` are published: 200 Ubuntu Launchpad cases, split evenly between
  `x86_64-to-aarch64` and `aarch64-to-x86_64`.
- The current Development Cases archive was generated at `2026-09-07T01:22:06Z`; its size is
  222,919,777 bytes and SHA-256 is
  `94372a28dbd81ece8f5007b83ec4b4ff1b0590de43a528be4461ae7f2bbd675d`.
- This digest supersedes an earlier archive under the same `v0.1` label. Pin the digest as well as
  the version whenever development evidence is recorded.
- Separate protocol-schema downloads are still marked **Coming soon**.
- Official runtime images are still marked **Registry pending**.
- The public platform health response reports Full Evaluation disabled, not ready, and no Full
  Evaluation worker available.
- The rules still label the final score denominator, tie-breaker, timeouts, CPU/memory/storage,
  concurrency, build-feedback budget, network/API policy, credential delivery, submission frequency,
  rerun/cancellation rules, and feedback visibility as pending.

The public phase is now listed as open. The public health endpoint still reports Full Evaluation
disabled, not ready, and without a worker. Use the live portal and release index for availability,
but do not infer unpublished evaluation limits from pilot behavior.

## Critical foundation-model gate

The rules require an LLM to be the Agent's foundation model. Archimedes supports real local or remote
OpenAI-compatible models, but its safe development default remains `mock`. The qualified `hello`
Smoke Test can be solved deterministically and therefore does not demonstrate that a real model was
available during organizer execution.

Do **not** start Full Evaluation until the organizer publishes the network and credential mechanism
and the exact immutable candidate has completed a real-model smoke run through that mechanism.
Never put an API key, `.env` file, credential, or private endpoint secret inside
`agents/archimedes` or an Agent ZIP.

Record for the evaluated immutable version:

- exact fast and strong model identifiers and versions;
- provider and endpoint type;
- organizer-approved credential-injection method;
- network availability assumptions;
- model-call, token, cost, timeout, repair-iteration, and build-request limits;
- prompt strategy, retrieval sources, static tools, external data, and known nondeterminism.

## Public Development Cases v0.1

The public archive contains case metadata using `schema_version: buildbench.case.v0.1`, with
architectures under `platform` and the failure-log path under `task` and `paths`. Archimedes `0.2.9`
accepts this shape as well as the existing flat Starter Kit task shape. This version also
retains the tail of oversized logs, ranks concrete Debian/linker/test failures ahead of wrapper
errors and echoed commands, extracts causal repository paths, and prioritizes Debian packaging
files for model context.

The development release excludes hidden labels, reference repairs, source-side success logs,
organizer audit records, and previous run artifacts. Do not seek, reconstruct, or bundle those
materials. Preserve upstream and distribution licenses when using or redistributing source packages.

All 1,510 paths in the archive's internal `SHA256SUMS` verified directly against ZIP members. Five
source-input filenames contain Debian epoch colons and cannot be extracted to NTFS; use a Linux
filesystem for complete materialization. Static analysis of all 200 released failure logs produced
a non-empty diagnostic and recognized family for every log, including ten logs over two megabytes;
106 logs now yield a repository-path candidate and no selected primary matches the audited set of
known command/inventory/warning-only false-positive prefixes.
Three actual-source replays selected generic multiarch-path repairs for `node-re2`, `rocm-llvm`, and
`buteo-sync-plugins` without model calls. The first two each changed only `debian/rules`; the third
changed only two failure-log-proven `.install` tokens under its declared debhelper compat 13. These
are integrity, ingestion, and candidate-selection results—not Verified Build Success Rate, because
organizer-equivalent target builds were not run.

A fourth actual-source replay, `qmidiarp`, selected a portable-flag repair without a model call. It
changed only `configure.ac`, removed exactly the x86-only flags explicitly rejected in the released
ARM failure log, preserved the other flags, and did not edit generated `configure`. This remains
candidate-selection evidence until an organizer-equivalent target build validates it.

## Build and package a new candidate

Use Linux or WSL2 with Docker Engine 24+ or Docker Desktop using Linux containers. From the Starter
Kit root:

```bash
./bb version
./bb doctor
./bb demo
./bb ready --agent ./agents/archimedes --json
```

Do not claim readiness unless `ready` exits zero, returns `"status": "succeeded"`, creates
`dist/agent-submission.zip`, and reports the same SHA-256 as the file.

Inspect the exact archive:

```bash
unzip -l dist/agent-submission.zip
sha256sum dist/agent-submission.zip
```

Its root must contain only the allowed shallow Agent bundle:

```text
agent.yaml
requirements.lock
README.md
src/
```

It must not contain the Starter Kit, tests, Development Cases, caches, logs, `.env`, credentials,
prior patches, model weights, symlinks, virtual environments, or run artifacts. Upload only the ZIP
emitted by `bb`; never manually zip the repository.

Run the stricter development audit as an additional check:

```bash
python dev/submission_preflight.py --json
```

Manual attestations in that command record external facts; they do not bypass an organizer check.

## Upload, qualify, and evaluate

1. Open **My Submissions** and upload `dist/agent-submission.zip` as a new immutable version.
2. Confirm the complete platform digest against the local SHA-256.
3. Wait for static checks, then run or await the Hosted Smoke Test as directed by the portal.
4. Review the retained entrypoint, dependency, workspace, result-schema, canonical-patch, permission,
   and build diagnostics.
5. Treat any change as a new version and repeat packaging, digest comparison, and qualification.
6. Start Full Evaluation only when the portal reports it enabled, the frozen operational policy is
   published, real-model delivery is proven, and the Team intentionally chooses to spend its allowed
   evaluation attempt.
7. Before the final-selection deadline, explicitly select the qualified immutable version intended
   for hidden evaluation. A later upload does not modify an active or completed evaluation snapshot.

## Reproducibility record

Retain the submitted ZIP, complete SHA-256, source commit, lockfile, manifest, exact model/provider
configuration, prompt and disclosure text, local `bb ready` JSON, Hosted Smoke log, platform
submission ID, platform digest, Full Evaluation ID, and permitted evaluation diagnostics.

The required final method disclosure covers model/provider/version, prompt and repair-loop design,
retrieval and static tools, training or fine-tuning, external datasets or knowledge bases,
third-party services, nondeterminism, caching, dependencies, and reproducibility constraints.

## Items requiring participant attention

- Keep the qualified `0.2.9` archive, digest, and submission ID immutable and backed up.
- Treat portal submission `20260907T041925Z-629a77c4` as the only version carrying the recorded
  v0.2.9 qualification evidence; retain v0.2.8 only as a fallback snapshot.
- Do not start Full Evaluation while the platform reports it disabled or before model/network policy
  and resource limits are published.
- Confirm the organizer-supported real LLM configuration before evaluating; `mock` is development
  only and does not satisfy the competition foundation-model rule.
- Re-check the CSTCloud Rules, Timeline, FAQ, and Data & Downloads pages immediately before every new
  upload or Full Evaluation request because material operational parameters are still pending.
- Use `buildbench-challenge@googlegroups.com` for rule or infrastructure clarification.
