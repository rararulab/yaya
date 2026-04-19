"""AC-03 self-test: a deliberately-broken binary must fail the gate.

The post-install smoke is only useful if it actually blocks a merge
when the CLI regresses. This test constructs a stand-in executable
that always exits non-zero with a JSON-shaped but wrong payload,
then drives the same helpers (:func:`run`, :func:`json_stdout`) over
it and asserts each helper raises ``AssertionError``. If any helper
silently passes against the broken stand-in, the gate is a
rubber-stamp and the test fails loudly.

This test runs alongside the real smoke tests under ``pytest
tests/e2e`` — on CI, locally, and from the release pipeline.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from .conftest import json_stdout, run

pytestmark = [pytest.mark.integration]


_BROKEN_SCRIPT_POSIX = """\
#!/usr/bin/env bash
# Deliberately-broken stand-in for the yaya CLI. Exits 1 on every
# invocation with plausible-looking but wrong JSON on stdout so the
# smoke helpers cannot mistake it for a passing run.
printf '{"ok": false, "error": "broken_binary_under_test"}\\n'
exit 1
"""


def _write_broken_binary(tmp_path: Path) -> Path:
    """Write a platform-appropriate always-fail stand-in and return its path."""
    if sys.platform.startswith("win"):
        path = tmp_path / "broken-yaya.cmd"
        path.write_text(
            '@echo off\r\necho {"ok": false, "error": "broken_binary_under_test"}\r\nexit /b 1\r\n',
            encoding="utf-8",
        )
    else:
        path = tmp_path / "broken-yaya"
        path.write_text(_BROKEN_SCRIPT_POSIX, encoding="utf-8")
        # chmod +x — the `run` helper invokes the path directly, so
        # POSIX requires the exec bit.
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_broken_binary_fails_assertions(tmp_path: Path) -> None:
    """Every helper must raise AssertionError against the broken stand-in."""
    broken = _write_broken_binary(tmp_path)
    # Explicitly NOT routing through the `yaya_bin` fixture — this is a
    # synthetic target, not the installed binary.
    result = run(str(broken), "version")
    assert result.returncode != 0, (
        "broken stand-in returned 0 — the smoke gate would have passed it; "
        "fix the fixture or the helpers before trusting the smoke"
    )

    # json_stdout must reject a non-zero result loudly.
    with pytest.raises(AssertionError) as exc_info:
        json_stdout(result)
    assert "exited" in str(exc_info.value)

    # Canonical pattern in every sibling test: `assert returncode == 0`.
    # Prove it refuses here; if this branch passes we have a false gate.
    canonical_ok = False
    try:
        assert result.returncode == 0, result.stderr
        canonical_ok = True
    except AssertionError:
        canonical_ok = False
    assert not canonical_ok, (
        "canonical `assert result.returncode == 0` passed against the broken "
        "stand-in — the smoke gate is a rubber-stamp"
    )
