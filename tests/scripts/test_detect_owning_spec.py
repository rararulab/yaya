"""Tests for scripts/_detect_owning_spec.py.

The detector is stdlib-only Python; tests import it directly from the
scripts/ path and exercise each resolution layer. CI correctness is
load-bearing on this — a wrong owner means boundary enforcement fires
on the wrong spec.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit


def _load_detector() -> Any:
    """Import scripts/_detect_owning_spec.py by path (it is not a package)."""
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "_detect_owning_spec.py"
    spec = importlib.util.spec_from_file_location("_detect_owning_spec", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load detector from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_detect_owning_spec"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def detector() -> Any:
    return _load_detector()


@pytest.fixture
def fake_specs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a fake specs/ directory under tmp_path and chdir there."""
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "kernel-bus-and-abi.spec").write_text("stub\n")
    (specs / "kernel-registry.spec").write_text("stub\n")
    (specs / "kernel-agent-loop.spec").write_text("stub\n")
    (specs / "harness-agent-spec.spec").write_text("stub\n")
    monkeypatch.chdir(tmp_path)
    return specs


@pytest.mark.parametrize(
    ("branch", "expected_slug"),
    [
        ("issue-42-kernel-bus", "kernel-bus"),
        ("issue-11-kernel-bus-and-abi", "kernel-bus-and-abi"),
        ("codex/issue-42-kernel-bus", "kernel-bus"),
        ("claude/issue-13-kernel-registry", "kernel-registry"),
        ("feat/plugin-web", "plugin-web"),
        ("codex/feat/plugin-web", "plugin-web"),
        ("chore/bump-deps", "bump-deps"),
        ("fix-docs-typo", "docs-typo"),
        ("main", ""),
        ("", ""),
    ],
)
def test_branch_slug_extraction(detector: Any, branch: str, expected_slug: str) -> None:
    assert detector._branch_slug(branch) == expected_slug


def test_branch_match_single_candidate(detector: Any, fake_specs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_HEAD_REF", "issue-11-kernel-bus")
    assert detector.detect_owning_spec(fake_specs) == str(fake_specs / "kernel-bus-and-abi.spec")


def test_agent_prefixed_branch_match_single_candidate(
    detector: Any, fake_specs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_HEAD_REF", "codex/issue-11-kernel-bus")
    assert detector.detect_owning_spec(fake_specs) == str(fake_specs / "kernel-bus-and-abi.spec")


def test_branch_match_prefers_exact_prefix(detector: Any, fake_specs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_HEAD_REF", "issue-13-kernel-registry")
    assert detector.detect_owning_spec(fake_specs) == str(fake_specs / "kernel-registry.spec")


def test_ambiguous_slug_returns_empty(
    detector: Any,
    fake_specs: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_HEAD_REF", "issue-99-kernel")
    assert detector.detect_owning_spec(fake_specs) == ""
    err = capsys.readouterr().err
    assert "matches multiple specs" in err


def test_nonexistent_slug_returns_empty(detector: Any, fake_specs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_HEAD_REF", "issue-42-nonexistent")
    monkeypatch.setattr(detector, "_spec_from_pr_body", lambda _: "")
    monkeypatch.setattr(detector, "_spec_from_commit_trailer", lambda _: "")
    assert detector.detect_owning_spec(fake_specs) == ""


def test_main_branch_returns_empty(detector: Any, fake_specs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_HEAD_REF", "main")
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    monkeypatch.setattr(detector, "_current_branch", lambda: "")
    monkeypatch.setattr(detector, "_spec_from_pr_body", lambda _: "")
    monkeypatch.setattr(detector, "_spec_from_commit_trailer", lambda _: "")
    assert detector.detect_owning_spec(fake_specs) == ""


def test_resolve_trailer_happy(detector: Any, fake_specs: Path) -> None:
    body = "Fixes #42.\n\nSpec: specs/kernel-registry.spec\n"
    assert detector._resolve_trailer(body, fake_specs) == ("specs/kernel-registry.spec")


def test_resolve_trailer_missing_spec_returns_empty(
    detector: Any,
    fake_specs: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    body = "Spec: specs/does-not-exist.spec\n"
    assert detector._resolve_trailer(body, fake_specs) == ""
    err = capsys.readouterr().err
    assert "references missing spec" in err


def test_resolve_trailer_none_when_absent(detector: Any, fake_specs: Path) -> None:
    body = "No trailer here. Just prose.\n"
    assert detector._resolve_trailer(body, fake_specs) == ""


def test_branch_takes_precedence_over_trailers(
    detector: Any, fake_specs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the branch resolves, the PR-body and commit trailers are ignored."""
    monkeypatch.setenv("GITHUB_HEAD_REF", "issue-11-kernel-bus")
    monkeypatch.setattr(
        detector,
        "_spec_from_pr_body",
        lambda _: str(fake_specs / "kernel-registry.spec"),
    )
    monkeypatch.setattr(
        detector,
        "_spec_from_commit_trailer",
        lambda _: str(fake_specs / "kernel-agent-loop.spec"),
    )
    assert detector.detect_owning_spec(fake_specs) == str(fake_specs / "kernel-bus-and-abi.spec")


def test_pr_body_takes_precedence_over_commit_trailer(
    detector: Any, fake_specs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_HEAD_REF", "hotfix-urgent")
    monkeypatch.setattr(
        detector,
        "_spec_from_pr_body",
        lambda _: "specs/harness-agent-spec.spec",
    )
    monkeypatch.setattr(
        detector,
        "_spec_from_commit_trailer",
        lambda _: "specs/kernel-registry.spec",
    )
    assert detector.detect_owning_spec(fake_specs) == "specs/harness-agent-spec.spec"


def test_commit_trailer_is_last_resort(detector: Any, fake_specs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_HEAD_REF", "hotfix-urgent")
    monkeypatch.setattr(detector, "_spec_from_pr_body", lambda _: "")
    monkeypatch.setattr(
        detector,
        "_spec_from_commit_trailer",
        lambda _: "specs/kernel-registry.spec",
    )
    assert detector.detect_owning_spec(fake_specs) == "specs/kernel-registry.spec"


def test_no_match_anywhere_returns_empty(detector: Any, fake_specs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_HEAD_REF", "chore/bump-deps")
    monkeypatch.setattr(detector, "_spec_from_pr_body", lambda _: "")
    monkeypatch.setattr(detector, "_spec_from_commit_trailer", lambda _: "")
    assert detector.detect_owning_spec(fake_specs) == ""
