"""Tests for scripts/check_feature_sync.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit


def _load_sync_checker() -> Any:
    """Import scripts/check_feature_sync.py by path."""
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "check_feature_sync.py"
    spec = importlib.util.spec_from_file_location("check_feature_sync", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load sync checker from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_feature_sync"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sync_checker() -> Any:
    return _load_sync_checker()


def _write_spec(path: Path, scenario_name: str = "happy path") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join([
            "spec: task",
            f'name: "{path.stem}"',
            "---",
            "",
            "## Completion Criteria",
            "",
            f"Scenario: {scenario_name}",
            "  Test:",
            "    Package: yaya",
            "    Filter: tests/test_example.py::test_example",
            "  Level: unit",
            "  Given a precondition",
            "  When an action runs",
            "  Then an outcome is observed",
            "",
        ]),
        encoding="utf-8",
    )


def _write_feature(path: Path, scenario_name: str = "happy path") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join([
            "Feature: Example",
            "",
            f"  Scenario: {scenario_name}",
            "    Given a precondition",
            "    When an action runs",
            "    Then an outcome is observed",
            "",
        ]),
        encoding="utf-8",
    )


def test_collect_sync_errors_reports_spec_without_feature(sync_checker: Any, tmp_path: Path) -> None:
    _write_spec(tmp_path / "specs" / "orphan.spec")
    (tmp_path / "tests" / "bdd" / "features").mkdir(parents=True)

    errors = sync_checker.collect_sync_errors(tmp_path)

    assert errors == [
        "❌ specs/orphan.spec: no matching tests/bdd/features/orphan.feature",
    ]


def test_collect_sync_errors_reports_spec_when_features_dir_is_missing(sync_checker: Any, tmp_path: Path) -> None:
    _write_spec(tmp_path / "specs" / "orphan.spec")

    errors = sync_checker.collect_sync_errors(tmp_path)

    assert errors == [
        "❌ specs/orphan.spec: no matching tests/bdd/features/orphan.feature",
    ]


def test_collect_sync_errors_reports_feature_without_spec(sync_checker: Any, tmp_path: Path) -> None:
    _write_feature(tmp_path / "tests" / "bdd" / "features" / "orphan.feature")
    (tmp_path / "specs").mkdir()

    errors = sync_checker.collect_sync_errors(tmp_path)

    assert errors == [
        "❌ tests/bdd/features/orphan.feature: no matching specs/orphan.spec",
    ]


def test_collect_sync_errors_accepts_matching_spec_and_feature(sync_checker: Any, tmp_path: Path) -> None:
    _write_spec(tmp_path / "specs" / "example.spec")
    _write_feature(tmp_path / "tests" / "bdd" / "features" / "example.feature")

    assert sync_checker.collect_sync_errors(tmp_path) == []
