"""Tests for ``scripts/check_feature_sync.py``."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_feature_sync.py"


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location("check_feature_sync", SCRIPT_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load sync checker from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_feature_sync"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def checker() -> Any:
    return _load_script()


def _write_spec(path: Path, *, scenario_name: str = "happy path", step: str = "the system is ready") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join([
            "spec: task",
            'name: "demo"',
            "---",
            "",
            "## Completion Criteria",
            "",
            f"Scenario: {scenario_name}",
            "  Test:",
            "    Package: yaya",
            "    Filter: tests/demo.py::test_demo",
            "  Level: unit",
            f"  Given {step}",
            "  When the user runs the demo",
            "  Then the system responds",
        ]),
        encoding="utf-8",
    )


def _write_feature(path: Path, *, scenario_name: str = "happy path", step: str = "the system is ready") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join([
            "Feature: Demo",
            "",
            f"  Scenario: {scenario_name}",
            f"    Given {step}",
            "    When the user runs the demo",
            "    Then the system responds",
        ]),
        encoding="utf-8",
    )


def test_collect_sync_errors_flags_missing_feature(tmp_path: Path, checker: Any) -> None:
    _write_spec(tmp_path / "specs" / "demo.spec")

    errors = checker.collect_sync_errors(tmp_path)

    assert errors
    assert "specs/demo.spec" in errors[0]
    assert "tests/bdd/features/demo.feature" in errors[0]


def test_collect_sync_errors_flags_orphan_feature(tmp_path: Path, checker: Any) -> None:
    _write_feature(tmp_path / "tests" / "bdd" / "features" / "demo.feature")

    errors = checker.collect_sync_errors(tmp_path)

    assert errors
    assert "tests/bdd/features/demo.feature" in errors[0]
    assert "specs/demo.spec" in errors[0]


def test_collect_sync_errors_accepts_e2e_feature_dir(tmp_path: Path, checker: Any) -> None:
    _write_spec(tmp_path / "specs" / "demo.spec")
    _write_feature(tmp_path / "tests" / "e2e" / "bdd" / "features" / "demo.feature")

    errors = checker.collect_sync_errors(tmp_path)

    assert errors == []


def test_collect_sync_errors_reports_step_drift(tmp_path: Path, checker: Any) -> None:
    _write_spec(tmp_path / "specs" / "demo.spec", step="the system is ready")
    _write_feature(
        tmp_path / "tests" / "bdd" / "features" / "demo.feature",
        step="the system is definitely ready",
    )

    errors = checker.collect_sync_errors(tmp_path)

    assert errors
    assert "step drift" in errors[0]
