"""Verify every `.feature` file under tests/bdd/features/ stays in sync
with its companion `specs/*.spec`.

Each `.feature` is the executable Gherkin for a spec's Completion
Criteria scenarios. When an author edits a spec's scenarios without
updating the `.feature`, pytest-bdd would either run stale text or
silently fail to bind — the scenarios become de-facto unverified.

This check closes that gap. For every `tests/bdd/features/X.feature`,
the script looks for `specs/X.spec` and compares:

  * Set of scenario names
  * Ordered list of Given/When/Then/And steps per scenario

If any drift, exit non-zero with a human-readable diff. Intended to
run as part of `just check` and in CI.

stdlib-only.
"""

from __future__ import annotations

import re
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


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    features_dir = repo / "tests" / "bdd" / "features"
    specs_dir = repo / "specs"

    if not features_dir.is_dir():
        print(f"no features dir at {features_dir}; nothing to check")
        return 0

    overall_ok = True
    features = sorted(features_dir.glob("*.feature"))
    if not features:
        print(f"no .feature files in {features_dir}; nothing to check")
        return 0

    for feature_path in features:
        spec_path = specs_dir / f"{feature_path.stem}.spec"
        if not spec_path.is_file():
            print(f"❌ {feature_path.relative_to(repo)}: no matching {spec_path.relative_to(repo)}")
            overall_ok = False
            continue

        spec_scenarios = parse_scenarios(spec_path)
        feature_scenarios = parse_scenarios(feature_path)
        diffs = diff_scenarios(spec_scenarios, feature_scenarios)

        if diffs:
            overall_ok = False
            print(f"❌ {feature_path.relative_to(repo)} drift from {spec_path.relative_to(repo)}:")
            for line in diffs:
                print(f"   {line}")
        else:
            print(
                f"✅ {feature_path.relative_to(repo)} in sync with "
                f"{spec_path.relative_to(repo)} "
                f"({len(feature_scenarios)} scenarios)"
            )

    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
