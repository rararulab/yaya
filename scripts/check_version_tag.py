from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")


def load_project_version(pyproject_path: Path) -> str:
    with pyproject_path.open("rb") as handle:
        data = tomllib.load(handle)

    project = data.get("project")
    if not isinstance(project, dict):
        raise ValueError(f"Missing [project] table in {pyproject_path}")  # noqa: TRY004

    version = project.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError(f"Missing project.version in {pyproject_path}")

    return version


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate tag version against pyproject.")
    parser.add_argument("--pyproject", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args()

    if not SEMVER_RE.match(args.expected_version):
        print(f"error: expected version must be semver (x.y.z): {args.expected_version}", file=sys.stderr)
        return 1

    try:
        project_version = load_project_version(args.pyproject)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not SEMVER_RE.match(project_version):
        print(
            f"error: project version must be semver (x.y.z): {args.pyproject} has {project_version}",
            file=sys.stderr,
        )
        return 1

    if project_version != args.expected_version:
        print(
            f"error: version mismatch: {args.pyproject} has {project_version}, expected {args.expected_version}",
            file=sys.stderr,
        )
        return 1

    print(f"ok: {args.pyproject} matches expected version {args.expected_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
