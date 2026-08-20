# Archimedes development evaluation

The synthetic corpus and evaluation runner are deliberately outside `agents/archimedes`; they are
not part of the competition bundle. The corpus spans compiler flags, optimized source, guards,
inline assembly, fallback selection, atomic linkage, ABI width, endianness, packaging/dependencies,
generated configuration, multi-file consistency, artifact paths, intrinsics, feature detection, and
an unfamiliar OOD target.

Use a fixed case order, seed, model, generation settings, model/build budget, and wall-time budget
when comparing configurations. Change only one component for an ablation. The default runner does
not execute package builds, so `verified_build_success` stays null; causal-location and repair-family
agreement are development surrogates only.

Examples:

```text
python dev/evaluate.py --configuration A --case unconditional_x86_sse_flag --output dev/pilot.jsonl
python dev/evaluate.py --configuration K --output dev/full-synthetic.csv
```

For local-model levels, set the loopback base URL and model identifiers in ordinary environment
variables. For remote pilot experiments, configure rates and a maximum estimated cost explicitly.
Do not put credentials into the case corpus, output table, submitted Agent directory, or Git.
