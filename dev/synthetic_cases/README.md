# Synthetic portability cases

These fixtures exercise generic failure shapes only. They contain no Build-Bench hidden-case
material. Each case has immutable-style `case.json` metadata, an initial `failure.log`, and a
miniature repository under `repo/`. Cases that can be compiled use ordinary C/Make/CMake files;
the low-familiarity fixture is intentionally analysis-only.

The metadata records source/target architecture, expected causal mechanism and file, expected
repair family, portable-fallback availability, discovery expectation, difficulty, and initial /
repaired failure frontiers. These are development surrogates, not substitutes for official
verified build success.
