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
Agent version:  0.2.2
Submission ID:  20260901T004910Z-0a48b255
Status:         Qualified
Hosted Smoke:   hello succeeded (1/1)
Validation:     static check, Agent execution, canonical patch, and final build all succeeded
```

The locally retained archive is:

```text
dist/agent-submission-archimedes-v0.2.2-20260901T004910Z-0a48b255.zip
SHA-256: 96123cf3af5e153663ce47ac45763a994917453defcce8d2a8dd937c760c1a63
```

The portal digest still needs to be compared with this complete local digest. A matching Agent name
and version alone do not prove byte identity.

Qualification proves that this immutable bundle satisfies the current static package checks and the
Hosted Smoke Test contract. It does **not** prove repair performance on the public Development Cases,
compliance with the still-unpublished foundation-model delivery policy, or success on Full/hidden
evaluation.

Source development now targets Archimedes `0.2.4`. Any source, manifest, dependency, or configuration
change after the qualified `0.2.2` upload requires a new immutable upload. Never describe local
`0.2.4` results as results of submission `20260901T004910Z-0a48b255`.

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
architectures under `platform` and the failure-log path under `task` and `paths`. Archimedes `0.2.4`
accepts this shape as well as the existing flat Starter Kit task shape. This version also
retains the tail of oversized logs, ranks concrete Debian/linker/test failures ahead of wrapper
errors, extracts causal repository paths, and prioritizes Debian packaging files for model context.

The development release excludes hidden labels, reference repairs, source-side success logs,
organizer audit records, and previous run artifacts. Do not seek, reconstruct, or bundle those
materials. Preserve upstream and distribution licenses when using or redistributing source packages.

All 1,510 paths in the archive's internal `SHA256SUMS` verified directly against ZIP members. Five
source-input filenames contain Debian epoch colons and cannot be extracted to NTFS; use a Linux
filesystem for complete materialization. Static analysis of all 200 released failure logs produced
a non-empty diagnostic and recognized family for every log, including ten logs over two megabytes.
One actual-source replay selected and applied the generic multiarch-path repair to `node-re2` while
changing only `debian/rules`. These are integrity, ingestion, and candidate-selection results—not
Verified Build Success Rate, because organizer-equivalent target builds were not run.

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

- Keep the qualified `0.2.2` archive and submission ID immutable and backed up.
- Compare its full platform SHA-256 with the retained local digest.
- Do not start Full Evaluation while the platform reports it disabled or before model/network policy
  and resource limits are published.
- Confirm the organizer-supported real LLM configuration before evaluating; `mock` is development
  only and does not satisfy the competition foundation-model rule.
- Re-check the CSTCloud Rules, Timeline, FAQ, and Data & Downloads pages immediately before every new
  upload or Full Evaluation request because material operational parameters are still pending.
- Use `buildbench-challenge@googlegroups.com` for rule or infrastructure clarification.
