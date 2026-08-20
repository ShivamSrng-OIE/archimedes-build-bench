# Build-Bench submission checklist for Archimedes

This checklist complements the versioned Starter Kit, Submission Guide, and Competition Rules.
If they conflict, the current organizer-published materials and the Starter Kit release used for
the upload take precedence.

Official references: [Build-Bench Challenge](https://aiops-lab-nku.github.io/BuildBench/),
[Participate and Submit](https://aiops-lab-nku.github.io/BuildBench/submission.html), and the
[My Submissions portal](https://aiops-lab-nku.github.io/BuildBench/my-submissions.html). The
[ICSE 2027 Competition Track](https://conf.researchr.org/track/icse-2027/icse-2027-competition-track)
is a separate paper/track workflow.

## Current competition schedule

- Team registration opens with the protocol freeze on **31 August 2026**.
- Public development, validation, uploads, and Hosted Smoke Tests open on **7 September 2026**.
- The final qualified Agent version must be frozen by **13 November 2026**.
- Organizers plan to publish final results by **20 November 2026**.

The current checkout contains Starter Kit `0.1.0-rc.2`, but it predates the 31 August protocol
freeze. Refresh from the official Data & Downloads release before the first official upload and
reconcile any manifest, workspace, credential, build-feedback, or resource-policy changes.

## Register the Team

1. The Team leader registers on the Build-Bench competition website.
2. Use a unique Team name and provide an email address for every member.
3. Include at most five people, including the Team leader.
4. Each person and email address may appear on only one Team.
5. Disclose organizer conflicts through the official support channel.

## Freeze the method configuration

The competition requires an LLM foundation model. Before qualification, record and test:

- exact fast and strong model names and versions;
- provider and endpoint type;
- organizer-approved credential-injection mechanism;
- network policy and availability assumptions;
- model-call, token, monetary, build-request, iteration, time, CPU, memory, and storage limits;
- prompt strategy, retrieval sources, static tools, external data, and known nondeterminism.

Never put an API key or `.env` file in `agents/archimedes` or the ZIP. The current rulebook says
the organizer credential and network mechanism will be published separately. Until that mechanism
is available and exercised, the submission is not competition-ready even if local checks pass.

## Validate in Linux or WSL2

Run the development-side strict audit first. It does not replace organizer checks, and it exits
nonzero until every local, model, registration, and hosted qualification gate is explicitly proven:

```bash
python dev/submission_preflight.py --json
```

After registration, organizer-compatible model delivery, a real-model smoke run, and submission
access are confirmed, add `--team-registered --submission-access-confirmed
--competition-model-access-confirmed --model-smoke-sha256 <CURRENT_ZIP_SHA256>`. Add
`--hosted-smoke-sha256 <PLATFORM_VERSION_SHA256>` only after that exact immutable ZIP passes the
platform test. The supplied digests must match the current archive. The registration/access flags
are manual attestations; they do not bypass or simulate any organizer check.

Run from the Starter Kit root, using the organizer-pinned images:

```bash
./bb version
./bb doctor
./bb demo
./bb check --agent ./agents/archimedes
./bb test --agent ./agents/archimedes
./bb ready --agent ./agents/archimedes --json
```

Do not continue unless `ready` returns `"status": "succeeded"`, prints the current ZIP SHA-256,
and creates `dist/agent-submission.zip`. Inspect the archive root:

```bash
unzip -l dist/agent-submission.zip
sha256sum dist/agent-submission.zip
```

It must contain `agent.yaml`, `requirements.lock`, `README.md`, and `src/` at the ZIP root. It must
not contain the Starter Kit, tests, development Cases, caches, logs, `.env`, credentials, prior
patches, model weights, or run artifacts. Upload only the ZIP emitted by `bb`; do not manually zip
the repository.

## Upload and qualify

1. Open **My Submissions** on the Build-Bench website.
2. Choose **Make new submission**.
3. Upload `dist/agent-submission.zip`, not this repository and not the Starter Kit.
4. Record the immutable Agent-version identifier and verify its platform digest against the local
   SHA-256.
5. Wait for static checking and run the Hosted Smoke Test.
6. Review entrypoint, dependency, permission, workspace, result-schema, build-feedback, and model
   diagnostics. Any change requires a new immutable upload.
7. Once a version qualifies, explicitly select that version and choose **Start evaluation** for a
   public Full Evaluation. Uploading alone does not start evaluation.
8. Before 13 November, explicitly freeze the final qualified version for hidden evaluation.

The Agent ZIP is uploaded through Build-Bench **My Submissions**. The ICSE Competition Track
HotCRP site is for competition proposals and any separately requested solution paper or organizer
report; it is not the Agent-bundle upload target.

## Retain reproducibility evidence

Keep the submitted ZIP, SHA-256, source commit, lockfile, exact configuration, model/provider
identifiers, prompt files, disclosure text, local `bb ready` JSON, Hosted Smoke Test result, and
permitted platform diagnostics. The organizers may request clarification or a reproducibility
demonstration, and any public system description must correspond to the evaluated immutable version.

## Current local blockers

At the time this checklist was written:

- `buildbench-validator-runtime:v0` and `buildbench-example-assets:v0` were not accessible from the
  configured Docker registry, so the full official `bb ready` workflow could not finish locally;
- final network, credential injection, build-feedback schema, and resource budgets were still
  pending the organizer's protocol freeze;
- no organizer-approved real foundation-model configuration had been injected into the managed
  runtime.

The rc.2 local Runner uses `--network none` and does not forward `ARCHIMEDES_*` variables. A host
Ollama smoke therefore validates the adapter but does not satisfy the separate
`competition_model_access` gate.

These are release/infrastructure blockers, not reasons to bypass `bb ready` or submit the current
provisional archive as final.
