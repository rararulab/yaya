"""Detect which `specs/*.spec` (if any) the current PR "owns".

Used by `scripts/check_specs.sh` to promote the owning spec's boundary
check from soft-report to hard-fail. All other specs stay soft-reported
(boundary on cross-cutting PRs should not block merges).

Resolution order (first match wins):

1. **Branch name convention** — per
   `rararulab/.github/docs/workflow.md`, feature branches are named
   ``issue-{N}-{slug}`` and a feature PR has a ``specs/{slug}.spec``.
   We match by suffix so ``issue-42-kernel-bus`` resolves to
   ``specs/kernel-bus-and-abi.spec`` as long as exactly one spec's
   filename starts with the branch slug.
2. **PR body trailer** — a line ``Spec: specs/<path>.spec`` anywhere in
   the PR body. Fetched via ``gh pr view --json body`` when
   ``GH_TOKEN`` is present; falls back to skipping this step locally.
3. **Commit trailer on HEAD** — a line ``Spec: specs/<path>.spec`` in
   the last commit message. Works offline without GitHub.
4. **No match** — print an empty string. Meta PRs (deps bumps, docs,
   CI) have no owning spec; boundary stays soft-reported across the
   board and the build is not blocked on boundary.

stdlib-only so the CI `check` job does not need extra deps.

Exit code is always 0 unless something truly unexpected happens (e.g.
the script is invoked outside a git repo without a ``BRANCH`` env
override). Detection failures are advisory, printed on stderr, and
never block the build — the script's job is only to *inform* the
caller.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def _resolve(cmd: str) -> str | None:
    """Return the absolute path of ``cmd`` or ``None`` if not on PATH."""
    return shutil.which(cmd)


SPEC_TRAILER_RE = re.compile(r"^\s*Spec:\s*(specs/[-\w.]+\.spec)\s*$", re.MULTILINE)
BRANCH_SLUG_RE = re.compile(r"^(?:issue|feat|fix|chore|refactor)[-/](\d+)?[-/]?(.*)$")


def _current_branch() -> str:
    """Return the current branch name (PR source), empty if unavailable.

    Honours ``GITHUB_HEAD_REF`` (PR branch on GitHub Actions) first, then
    ``GITHUB_REF_NAME`` (push events and non-PR contexts), then falls back
    to ``git rev-parse --abbrev-ref HEAD``.
    """
    for env_var in ("GITHUB_HEAD_REF", "GITHUB_REF_NAME"):
        value = os.environ.get(env_var, "").strip()
        if value and value != "main":
            return value
    git = _resolve("git")
    if git is None:
        return ""
    try:
        result = subprocess.run(
            [git, "rev-parse", "--abbrev-ref", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError, subprocess.SubprocessError:
        return ""
    if result.returncode != 0:
        return ""
    branch = result.stdout.strip()
    return "" if branch in {"HEAD", "main"} else branch


def _branch_slug(branch: str) -> str:
    """Extract the descriptive slug from a conventional branch name.

    - ``issue-42-kernel-bus`` → ``kernel-bus``
    - ``feat/plugin-web`` → ``plugin-web``
    - ``chore/bump-deps`` → ``bump-deps``
    - ``main`` → ``""``
    """
    if not branch:
        return ""
    # Normalise path separator to `-`; the regex handles both.
    normalised = branch.replace("/", "-")
    match = BRANCH_SLUG_RE.match(normalised)
    if not match:
        return ""
    slug = match.group(2) or ""
    return slug.strip("-")


def _find_spec_by_slug(slug: str, specs_dir: Path) -> str:
    """Return ``specs/<file>.spec`` whose stem starts with ``slug`` and
    matches exactly one candidate; empty string otherwise.

    The slug-to-filename match is prefix-based so
    ``issue-11-kernel-bus`` can resolve to
    ``specs/kernel-bus-and-abi.spec`` (single match) but an ambiguous
    slug that matches multiple specs returns empty with a warning.
    """
    if not slug or not specs_dir.is_dir():
        return ""
    candidates = [p for p in specs_dir.glob("*.spec") if p.stem.startswith(slug)]
    if len(candidates) == 1:
        return str(candidates[0])
    if len(candidates) > 1:
        names = ", ".join(p.name for p in candidates)
        print(
            f"owning-spec: slug {slug!r} matches multiple specs ({names}); ambiguous, no owner",
            file=sys.stderr,
        )
    return ""


def _spec_from_pr_body(specs_dir: Path) -> str:
    """Parse a `Spec:` trailer from the PR body via `gh pr view`.

    Returns the spec path on success; empty string when the PR body is
    unavailable (local, no `gh`, no `GH_TOKEN`, no open PR) or no
    trailer is present.
    """
    if not os.environ.get("GH_TOKEN") and not os.environ.get("GITHUB_TOKEN"):
        return ""
    gh = _resolve("gh")
    if gh is None:
        return ""
    try:
        result = subprocess.run(
            [gh, "pr", "view", "--json", "body"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError, subprocess.SubprocessError:
        return ""
    if result.returncode != 0:
        return ""
    try:
        body = str(json.loads(result.stdout).get("body", ""))
    except json.JSONDecodeError:
        return ""
    return _resolve_trailer(body, specs_dir)


def _spec_from_commit_trailer(specs_dir: Path) -> str:
    """Parse a `Spec:` trailer from the HEAD commit message."""
    git = _resolve("git")
    if git is None:
        return ""
    try:
        result = subprocess.run(
            [git, "log", "-1", "--pretty=%B"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError, subprocess.SubprocessError:
        return ""
    if result.returncode != 0:
        return ""
    return _resolve_trailer(result.stdout, specs_dir)


def _resolve_trailer(text: str, specs_dir: Path) -> str:
    """Return the first `Spec:` trailer in ``text`` if the file exists."""
    match = SPEC_TRAILER_RE.search(text)
    if not match:
        return ""
    path = match.group(1)
    if not (Path.cwd() / path).is_file():
        print(
            f"owning-spec: trailer references missing spec {path!r}",
            file=sys.stderr,
        )
        return ""
    # Normalise to specs_dir-relative form for consistency.
    normalised = str(specs_dir.name + "/" + Path(path).name)
    if not (Path.cwd() / normalised).is_file():
        return ""
    return normalised


def detect_owning_spec(specs_dir: Path = Path("specs")) -> str:
    """Run the resolution chain and return the owning spec path (or ``""``)."""
    branch = _current_branch()
    slug = _branch_slug(branch)
    by_branch = _find_spec_by_slug(slug, specs_dir)
    if by_branch:
        return by_branch

    by_pr_body = _spec_from_pr_body(specs_dir)
    if by_pr_body:
        return by_pr_body

    by_commit = _spec_from_commit_trailer(specs_dir)
    if by_commit:
        return by_commit

    return ""


def main() -> int:
    specs_dir_env = os.environ.get("SPEC_DIR", "specs")
    result = detect_owning_spec(Path(specs_dir_env))
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
