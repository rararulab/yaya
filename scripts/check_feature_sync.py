"""Verify every mirrored `.feature` stays in sync with its companion spec.

Each `.feature` is the executable Gherkin for a spec's Completion
Criteria scenarios. When an author edits a spec's scenarios without
updating the `.feature`, pytest-bdd would either run stale text or
silently fail to bind — the scenarios become de-facto unverified.

This check closes that gap. For every mirrored pair, the script compares:

  * Set of scenario names
  * Ordered list of Given/When/Then/And steps per scenario

Both directions are enforced:

  * every `specs/*.spec` must have a matching `.feature`;
  * every mirrored `.feature` must have a matching `.spec`.

If any drift or missing mirror exists, exit non-zero with a
human-readable diff. Intended to run as part of `just check`, CI, and
pre-commit.

stdlib-only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SCENARIO_RE = re.compile(r"^\s*Scenario:\s*(?P<name>.+?)\s*$")
STEP_RE = re.compile(r"^\s*(?:Given|When|Then|And|But)\s+(?P<text>.+?)\s*$")
# agent-spec `.spec` scenarios include a `Test:` / `Package:` / `Filter:`
# / `Level:` block BEFORE the Given/When/Then. We skip those when
# extracting steps so the comparison is Gherkin-to-Gherkin.
SKIP_RE = re.compile(r"^\s*(Test|Package|Filter|Level):\s*")


@dataclass(frozen=True)
class Scenario:
    name: str
    steps: tuple[str, ...]


def _repo_path(path: Path, repo: Path) -> str:
    """Return a stable repo-relative path for cross-platform logs."""
    return path.relative_to(repo).as_posix()


def parse_scenarios(path: Path) -> list[Scenario]:
    """Return the ordered list of scenarios in ``path``.

    Works for both Gherkin `.feature` files and agent-spec `.spec` files —
    the parser is lenient about indentation and silently drops
    agent-spec's `Test:` / `Package:` / `Filter:` / `Level:` metadata
    lines so the two formats compare cleanly.
    """
    scenarios: list[Scenario] = []
    current_name: str | None = None
    current_steps: list[str] = []
    in_scenarios_section = path.suffix == ".feature"

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            heading = line[3:].strip().lower()
            in_scenarios_section = heading in {
                "completion criteria",
                "completion criteria (bdd)",
                "scenarios",
            }
            if not in_scenarios_section and current_name is not None:
                scenarios.append(Scenario(current_name, tuple(current_steps)))
                current_name = None
                current_steps = []
            continue

        if not in_scenarios_section:
            continue

        if SKIP_RE.match(line):
            continue

        scenario_match = SCENARIO_RE.match(line)
        if scenario_match:
            if current_name is not None:
                scenarios.append(Scenario(current_name, tuple(current_steps)))
            current_name = scenario_match.group("name").strip()
            current_steps = []
            continue

        step_match = STEP_RE.match(line)
        if step_match and current_name is not None:
            current_steps.append(step_match.group("text").strip())

    if current_name is not None:
        scenarios.append(Scenario(current_name, tuple(current_steps)))

    return scenarios


def diff_scenarios(spec: list[Scenario], feature: list[Scenario]) -> list[str]:
    """Compare ordered scenario lists; return human-readable drift lines."""
    diffs: list[str] = []
    spec_names = [s.name for s in spec]
    feature_names = [s.name for s in feature]

    if spec_names != feature_names:
        diffs.append("scenario list mismatch:")
        diffs.append(f"  .spec    : {spec_names}")
        diffs.append(f"  .feature : {feature_names}")
        return diffs

    for spec_sc, feat_sc in zip(spec, feature, strict=True):
        if spec_sc.steps != feat_sc.steps:
            diffs.append(f"scenario {spec_sc.name!r} step drift:")
            diffs.append(f"  .spec    : {list(spec_sc.steps)}")
            diffs.append(f"  .feature : {list(feat_sc.steps)}")
    return diffs


def iter_feature_dirs(repo: Path) -> list[Path]:
    """Return the feature directories that mirror repo specs."""
    candidates = [
        repo / "tests" / "bdd" / "features",
        repo / "tests" / "e2e" / "bdd" / "features",
    ]
    return [path for path in candidates if path.is_dir()]


def _feature_stems(feature_dirs: Iterable[Path]) -> dict[str, Path]:
    """Return a stable stem -> feature path map across all mirror dirs."""
    mapping: dict[str, Path] = {}
    for feature_dir in feature_dirs:
        for feature_path in sorted(feature_dir.glob("*.feature")):
            mapping[feature_path.stem] = feature_path
    return mapping


def collect_sync_errors(repo: Path) -> list[str]:
    """Return human-readable sync errors for all spec/feature pairs."""
    specs_dir = repo / "specs"
    feature_dirs = iter_feature_dirs(repo)

    errors: list[str] = []
    specs = sorted(specs_dir.glob("*.spec")) if specs_dir.is_dir() else []
    features_by_stem = _feature_stems(feature_dirs)
    feature_stems = set(features_by_stem)

    for spec_path in specs:
        if spec_path.stem not in feature_stems:
            expected = (
                feature_dirs[0] if feature_dirs else repo / "tests" / "bdd" / "features"
            ) / f"{spec_path.stem}.feature"
            errors.append(f"❌ {_repo_path(spec_path, repo)}: no matching {_repo_path(expected, repo)}")

    for feature_path in sorted(features_by_stem.values()):
        spec_path = specs_dir / f"{feature_path.stem}.spec"
        if not spec_path.is_file():
            errors.append(f"❌ {_repo_path(feature_path, repo)}: no matching {_repo_path(spec_path, repo)}")
            continue

        spec_scenarios = parse_scenarios(spec_path)
        feature_scenarios = parse_scenarios(feature_path)
        diffs = diff_scenarios(spec_scenarios, feature_scenarios)
        if diffs:
            errors.append(
                "\n".join([
                    f"❌ {_repo_path(feature_path, repo)} drift from {_repo_path(spec_path, repo)}:",
                    *(f"   {line}" for line in diffs),
                ])
            )

    return errors


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    specs_dir = repo / "specs"
    feature_dirs = iter_feature_dirs(repo)

    if not specs_dir.is_dir():
        print(f"no specs dir at {specs_dir}; nothing to check")
        return 0

    errors = collect_sync_errors(repo)
    if errors:
        print("\n".join(errors))
        return 1

    if not feature_dirs:
        print(f"no feature dirs under {repo / 'tests'}; nothing to check")
        return 0

    features = sorted(path for feature_dir in feature_dirs for path in feature_dir.glob("*.feature"))
    if not features:
        joined = ", ".join(_repo_path(path, repo) for path in feature_dirs)
        print(f"no .feature files in {joined}; nothing to check")
        return 0

    for feature_path in features:
        spec_path = specs_dir / f"{feature_path.stem}.spec"
        feature_scenarios = parse_scenarios(feature_path)
        print(
            f"✅ {_repo_path(feature_path, repo)} in sync with "
            f"{_repo_path(spec_path, repo)} "
            f"({len(feature_scenarios)} scenarios)"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
