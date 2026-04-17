# scripts — Agent Guidelines

## Purpose
Release-time and CI helper scripts. Not imported by the package.

## Architecture
- `check_version_tag.py` — verifies that the git tag matches `pyproject.toml` version during release.

## Critical Invariants
- Scripts are **standalone** — runnable with `python scripts/<name>.py` using only stdlib (or clearly documented deps).
- No imports from `yaya.*` unless absolutely required (scripts run before install in some CI contexts).
- Every script has a module-level docstring stating: what it does, when it runs, and exit-code semantics.

## What NOT To Do
- Do NOT add scripts that duplicate `justfile` recipes — extend the justfile instead.
- Do NOT put secrets or hard-coded paths here — read from env.
- Do NOT add a script without a line in the release workflow or justfile that calls it (orphans rot).
