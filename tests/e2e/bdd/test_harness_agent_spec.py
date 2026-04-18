"""Pytest-bdd execution of specs/harness-agent-spec.spec scenarios.

The Gherkin text in ``tests/bdd/features/harness-agent-spec.feature`` is
the authoritative BDD contract for the post-install CLI harness. Each
scenario binds to step definitions in this module via pytest-bdd;
changing the scenario text without a matching step def causes pytest to
fail with ``StepDefinitionNotFoundError``.

These scenarios exercise the installed ``yaya`` binary via subprocess
(like ``tests/e2e/test_cli_smoke.py``), so the module lives under
``tests/e2e/bdd/`` — the default ``just test`` suite excludes
``tests/e2e`` entirely, and the e2e-smoke job / ``just test-e2e`` picks
this file up automatically via ``pytest tests/e2e -v``. A ``skipif``
guard additionally skips the whole module when ``yaya`` is not on PATH,
so importing the module outside the smoke venv degrades gracefully
instead of failing collection.

This complements (does not replace) the engineering-level tests in
``tests/e2e/test_cli_smoke.py``. BDD here proves the scenarios the spec
advertises are actually executed; the pytest integration tests cover
edge cases and JSON-shape assertions that are not worth surfacing in
Gherkin.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

# Reuse the e2e helpers directly — step defs wire Gherkin to the real
# CLI, and these helpers encapsulate subprocess invocation + JSON parsing
# exactly as the companion engineering tests do.
from tests.e2e.conftest import json_stdout, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("yaya") is None,
        reason="yaya not installed on PATH — run via `just test-e2e`",
    ),
]

FEATURE_FILE = Path(__file__).resolve().parents[2] / "bdd" / "features" / "harness-agent-spec.feature"
scenarios(str(FEATURE_FILE))


# -- Scenario 1: yaya version exits zero after wheel install ---------------


@given("the wheel was installed into a fresh venv")
def _wheel_installed(yaya_bin: str) -> None:
    # The ``yaya_bin`` fixture (tests/e2e/conftest.py) already asserts
    # the CLI is on PATH. The Given exists to read naturally in the
    # scenario text and to document the precondition.
    assert yaya_bin, "yaya_bin fixture must resolve to an installed binary"


@when("the user runs `yaya version`", target_fixture="version_result")
def _run_version(yaya_bin: str) -> subprocess.CompletedProcess[str]:
    return run(yaya_bin, "version")


@then("the process exits 0 with a non-empty stdout")
def _version_exits_zero(version_result: subprocess.CompletedProcess[str]) -> None:
    assert version_result.returncode == 0, version_result.stderr
    assert version_result.stdout.strip(), "expected non-empty stdout"


# -- Scenario 2: --json version canonical shape ---------------------------


@given("the installed yaya")
def _installed_yaya(yaya_bin: str) -> None:
    assert yaya_bin


@when(
    "the user runs `yaya --json version`",
    target_fixture="json_version_result",
)
def _run_json_version(yaya_bin: str) -> subprocess.CompletedProcess[str]:
    return run(yaya_bin, "--json", "version")


@then(
    'stdout is a JSON object with ok=true, action="version", and a string version field',
)
def _assert_json_version_shape(
    json_version_result: subprocess.CompletedProcess[str],
) -> None:
    payload = json_stdout(json_version_result)
    assert payload["ok"] is True
    assert payload["action"] == "version"
    version = payload.get("version")
    assert isinstance(version, str) and version, f"expected non-empty string version field, got {version!r}"


# -- Scenario 3: unknown command exits non-zero ---------------------------


@when(
    "the user runs an unrecognized subcommand",
    target_fixture="unknown_result",
)
def _run_unknown(yaya_bin: str) -> subprocess.CompletedProcess[str]:
    return run(yaya_bin, "this-command-does-not-exist")


@then("the process exits with a non-zero code")
def _assert_nonzero(unknown_result: subprocess.CompletedProcess[str]) -> None:
    assert unknown_result.returncode != 0, f"expected non-zero exit; got 0 with stdout:\n{unknown_result.stdout}"
