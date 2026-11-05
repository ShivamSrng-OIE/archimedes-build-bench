# Archimedes

Archimedes is an autonomous repair agent for cross-architecture package build
failures, built for the [Build-Bench Challenge](https://matrix.cstcloud.cn/build-bench/)
(ICSE 2027 Competition Track).

Given a software package that builds on one CPU architecture but fails on
another (x86_64 ↔ aarch64), together with the failing build log, Archimedes
identifies the root cause and makes a small, evidence-backed edit to the package
so that it builds on the target architecture. When it cannot justify a fix, it
leaves the package untouched.

## How it works

1. **Failure forensics** finds the first causal error in the build log and
   classifies it (compilation, linking, packaging, dependency, and so on).
2. **Architecture analysis** scans the source for architecture-specific
   constructs such as x86-only compiler flags, intrinsic headers, hard-coded
   multiarch paths and ABI assumptions.
3. **Evidence-gated repair** builds each proposed fix from claims backed by
   direct evidence. A fix is applied only when its confidence clears a
   threshold that rises with the risk of the change.
4. **Guarded execution** applies edits atomically through a strict path and
   policy check. Fixes that would suppress failures or skip tests are refused.

Most repairs come from deterministic rules. An OpenAI-compatible model is used
for critique and for planning when no rule applies.

## Requirements

- Python 3.11
- Git
- Linux with Docker Engine for the official Starter Kit commands
  (on Windows, use WSL 2 with Ubuntu)

The agent itself uses only the Python standard library.

## Installation

```bash
git clone <repository-url> archimedes
cd archimedes
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install pytest mypy ruff       # development tools only
```

## Usage

Run the test suite and static checks:

```bash
python -m pytest -q
python -m mypy --strict agents/archimedes/src
python -m ruff check agents/archimedes/src tests dev
```

Run the agent on a prepared workspace (`input/`, `work/repo/`, `output/`):

```bash
cd agents/archimedes
BB_WORKSPACE=/path/to/workspace python -m src.main
```

Without model settings the agent runs fully offline using its deterministic
analysis and repair rules.

## Packaging with the Starter Kit

Download the Build-Bench Starter Kit from the
[Data & Downloads](https://matrix.cstcloud.cn/build-bench/data-downloads.html)
page and extract it to a Linux filesystem (on WSL, use your home directory,
not `/tmp`). Then:

```bash
./bb doctor
./bb ready --agent ./agents/archimedes --json
```

`bb ready` checks the agent, runs the example case end to end and writes
the submission ZIP into the Starter Kit's own `dist/` folder.

## Project structure

```text
agents/archimedes/   the repair agent (manifest, source, agent README)
tests/               unit and integration tests
dev/                 evaluation, replay and packaging tools
docs/                design documentation and the full codebase guide
```

## Documentation

A complete walkthrough of the architecture, configuration and outputs is in
[docs/CODEBASE_GUIDE.md](docs/CODEBASE_GUIDE.md).
