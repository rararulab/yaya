"""Regression tests for ``scripts/_parse_spec_result.py``.

The parser classifies ``agent-spec lifecycle --format json`` output
into hard-fail (blocks merge) and soft-report (visible only). Issue
#39 demands that the harness actually enforces what the docs promise:

* AC-02 — a boundary violation on the **owning** spec fails the build.
* AC-03 — an unbound scenario (manifests as ``verdict=fail`` on a
  real, non-boundary scenario name) fails the build.

Both ACs are pure functions of the parser's decision logic, so we
invoke the script as a subprocess with hand-written JSON payloads
that mirror the real shapes emitted by ``agent-spec``. This gives us
a fast, hermetic regression net without needing the Rust toolchain in
the test environment. The tests also cover the soft-report paths so
future changes cannot accidentally upgrade non-owning boundary noise
to a hard fail.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "_parse_spec_result.py"


def _run(payload: dict[str, object], *, spec: str, owning_spec: str = "") -> subprocess.CompletedProcess[str]:
    """Invoke the parser script with ``payload`` on stdin.

    Returns the completed process so tests can assert on both exit
    code and the single-line summary written to stdout.
    """
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--spec",
            spec,
            "--min-score",
            "0.6",
            "--owning-spec",
            owning_spec,
        ],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )


def _ok_payload(**overrides: object) -> dict[str, object]:
    """Return a green lifecycle payload, overridable for regressions."""
    base: dict[str, object] = {
        "lint_issues": 0,
        "quality_score": 1.0,
        "verification": {"results": []},
    }
    base.update(overrides)
    return base


def test_clean_payload_is_ok() -> None:
    """Baseline — a clean lifecycle run exits 0 with ``status=OK``."""
    result = _run(_ok_payload(), spec="specs/example.spec")
    assert result.returncode == 0
    assert "status=OK" in result.stdout


def test_parse_error_hard_fails() -> None:
    """Bad JSON means agent-spec itself exploded — never silently pass."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--spec", "specs/example.spec", "--min-score", "0.6", "--owning-spec", ""],
        input="not json",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "status=PARSE_ERROR" in result.stdout


def test_quality_below_floor_hard_fails() -> None:
    """Sloppy spec authoring (quality < 0.6) must block merge."""
    payload = _ok_payload(quality_score=0.5)
    result = _run(payload, spec="specs/example.spec")
    assert result.returncode == 1
    assert "status=HARD_FAIL" in result.stdout
    assert "quality=0.50<min=0.60" in result.stdout


def test_unbound_scenario_hard_fails() -> None:
    """AC-03 — a real scenario with verdict=fail blocks the build.

    ``agent-spec`` reports an unbound scenario (one whose ``Test:``
    selector does not resolve) as ``verdict=fail`` with a scenario
    name that is NOT the ``[boundaries]`` pseudo-scenario. The parser
    must treat that as a hard fail so CI catches it.
    """
    payload = _ok_payload(
        verification={
            "results": [
                {"scenario_name": "happy path", "verdict": "fail"},
            ],
        }
    )
    result = _run(payload, spec="specs/example.spec")
    assert result.returncode == 1
    assert "status=HARD_FAIL" in result.stdout
    assert "scenario_fail(happy path)" in result.stdout


def test_owning_spec_boundary_violation_hard_fails() -> None:
    """AC-02 — a boundary violation on the owning spec blocks the build.

    The wrapper detects the owning spec via
    ``scripts/_detect_owning_spec.py`` and passes it through as
    ``--owning-spec``. When ``--spec`` matches, boundary pseudo-
    scenario failures promote from soft to hard.
    """
    payload = _ok_payload(
        verification={
            "results": [
                {"scenario_name": "[boundaries] Allowed", "verdict": "fail"},
            ],
        }
    )
    result = _run(payload, spec="specs/owned.spec", owning_spec="specs/owned.spec")
    assert result.returncode == 1
    assert "status=HARD_FAIL" in result.stdout
    assert "boundary_fail=1" in result.stdout


def test_non_owning_spec_boundary_violation_is_soft() -> None:
    """Cross-cutting PR — boundary noise on a non-owning spec is soft.

    Without this carve-out every cross-cutting PR would fail N-1
    specs. The soft-report path is load-bearing; lock it in so
    future refactors cannot accidentally hard-fail these again.
    """
    payload = _ok_payload(
        verification={
            "results": [
                {"scenario_name": "[boundaries] Forbidden", "verdict": "fail"},
            ],
        }
    )
    result = _run(payload, spec="specs/other.spec", owning_spec="specs/owned.spec")
    assert result.returncode == 0
    assert "status=OK" in result.stdout
    assert "soft:boundary_fail=1" in result.stdout


def test_skip_verdicts_are_soft_reported() -> None:
    """Verify SKIPs require an AI backend — they must never hard-fail."""
    payload = _ok_payload(
        verification={
            "results": [
                {"scenario_name": "happy path", "verdict": "skip"},
                {"scenario_name": "edge", "verdict": "skip"},
            ],
        }
    )
    result = _run(payload, spec="specs/example.spec")
    assert result.returncode == 0
    assert "status=OK" in result.stdout
    assert "skipped=2" in result.stdout
