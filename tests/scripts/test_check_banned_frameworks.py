"""Tests for scripts/check_banned_frameworks.py.

The scanner is the mechanical enforcement of AGENT.md §4. Tests cover
the happy path against the real repo plus injected violations under
``tmp_path`` for both surfaces (declared deps + AST imports).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_banned_frameworks.py"


def _load_script() -> Any:
    """Import the script by path — it is not a package."""
    spec = importlib.util.spec_from_file_location("check_banned_frameworks", SCRIPT_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load scanner from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_banned_frameworks"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def scanner() -> Any:
    return _load_script()


def _make_repo(tmp_path: Path, *, pyproject: str = "", src_files: dict[str, str] | None = None) -> Path:
    """Build a minimal fake repo under ``tmp_path`` and return its root."""
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    for relative, body in (src_files or {}).items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------- happy path


def test_real_repo_passes(scanner: Any) -> None:
    """Whatever main looks like today, the scanner must not flag it."""
    result = scanner.run(REPO_ROOT)
    assert result.ok, [v.render() for v in result.violations]
    assert result.violations == []


def test_real_repo_passes_via_subprocess() -> None:
    """End-to-end: subprocess returns 0 on the live repo."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------- pyproject scan


def test_detects_banned_in_project_dependencies(tmp_path: Path, scanner: Any) -> None:
    repo = _make_repo(
        tmp_path,
        pyproject=('[project]\nname = "x"\nversion = "0"\ndependencies = ["langchain>=0.3", "httpx>=0.28"]\n'),
    )
    result = scanner.run(repo)
    assert not result.ok
    assert any(v.package == "langchain" for v in result.violations)


def test_detects_banned_in_dependency_groups(tmp_path: Path, scanner: Any) -> None:
    repo = _make_repo(
        tmp_path,
        pyproject=(
            '[project]\nname = "x"\nversion = "0"\ndependencies = []\n[dependency-groups]\ndev = ["crewai==0.5"]\n'
        ),
    )
    result = scanner.run(repo)
    assert not result.ok
    assert any(v.package == "crewai" for v in result.violations)


def test_detects_banned_in_optional_dependencies(tmp_path: Path, scanner: Any) -> None:
    repo = _make_repo(
        tmp_path,
        pyproject=(
            '[project]\nname = "x"\nversion = "0"\ndependencies = []\n'
            '[project.optional-dependencies]\nrag = ["llama-index"]\n'
        ),
    )
    result = scanner.run(repo)
    assert not result.ok
    assert any(v.package == "llama-index" for v in result.violations)


# ---------------------------------------------------------- import scan


def test_detects_banned_import_in_src(tmp_path: Path, scanner: Any) -> None:
    repo = _make_repo(
        tmp_path,
        pyproject='[project]\nname = "x"\nversion = "0"\ndependencies = []\n',
        src_files={"src/yaya/foo.py": "import langgraph\n"},
    )
    result = scanner.run(repo)
    assert not result.ok
    hits = [v for v in result.violations if v.surface == "import"]
    assert any(v.package == "langgraph" for v in hits)
    assert any("foo.py:1" in v.location for v in hits)


def test_detects_banned_from_import(tmp_path: Path, scanner: Any) -> None:
    repo = _make_repo(
        tmp_path,
        pyproject='[project]\nname = "x"\nversion = "0"\ndependencies = []\n',
        src_files={"tests/foo_test.py": "from langchain.chains import LLMChain\n"},
    )
    result = scanner.run(repo)
    assert not result.ok
    assert any(v.package == "langchain" for v in result.violations if v.surface == "import")


def test_string_mention_is_not_a_violation(tmp_path: Path, scanner: Any) -> None:
    """AST scan must not false-positive on string mentions of a ban."""
    repo = _make_repo(
        tmp_path,
        pyproject='[project]\nname = "x"\nversion = "0"\ndependencies = []\n',
        src_files={"src/yaya/notes.py": 'X = "we do not import langchain anywhere"\n'},
    )
    result = scanner.run(repo)
    assert result.ok, [v.render() for v in result.violations]


# ---------------------------------------------------------- normalization


@pytest.mark.parametrize(
    "raw",
    ["langchain", "Langchain", "LANGCHAIN", "  langchain  "],
)
def test_name_normalization_catches_case_variants(raw: str, scanner: Any) -> None:
    assert scanner.normalize_distribution_name(raw) == "langchain"
    assert scanner.normalize_distribution_name(raw) in scanner.BANNED_PACKAGES


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("llama-index", "llama-index"),
        ("llama_index", "llama-index"),
        ("Llama.Index", "llama-index"),
        ("LANGCHAIN_CORE", "langchain-core"),
    ],
)
def test_pypa_normalization_collapses_separators(raw: str, normalized: str, scanner: Any) -> None:
    """PyPA spec: ``_`` / ``.`` / runs of ``-`` all collapse to ``-`` after lower()."""
    assert scanner.normalize_distribution_name(raw) == normalized
    assert normalized in scanner.BANNED_PACKAGES


def test_strip_requirement_extras_handles_specifiers(scanner: Any) -> None:
    assert scanner._strip_requirement_extras("langchain>=0.3") == "langchain"
    assert scanner._strip_requirement_extras("langchain[extras]==1.0") == "langchain"
    assert scanner._strip_requirement_extras("langchain ; python_version < '3.13'") == "langchain"


def test_strip_requirement_extras_drops_url_form(scanner: Any) -> None:
    """URL / git refs cannot be matched against the ban list — return ''."""
    assert scanner._strip_requirement_extras("git+https://example.com/x.git") == ""
    assert scanner._strip_requirement_extras("https://example.com/x.tar.gz") == ""


# ---------------------------------------------------------- CLI shape


def test_cli_returns_zero_on_clean_tree(tmp_path: Path, scanner: Any) -> None:
    repo = _make_repo(
        tmp_path,
        pyproject='[project]\nname = "x"\nversion = "0"\ndependencies = ["httpx"]\n',
    )
    rc = scanner.main(["--repo-root", str(repo)])
    assert rc == 0


def test_cli_returns_one_on_violation(tmp_path: Path, scanner: Any) -> None:
    repo = _make_repo(
        tmp_path,
        pyproject='[project]\nname = "x"\nversion = "0"\ndependencies = ["langchain"]\n',
    )
    rc = scanner.main(["--repo-root", str(repo)])
    assert rc == 1


def test_cli_json_output_shape(tmp_path: Path, scanner: Any, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _make_repo(
        tmp_path,
        pyproject='[project]\nname = "x"\nversion = "0"\ndependencies = ["langchain"]\n',
    )
    rc = scanner.main(["--repo-root", str(repo), "--json"])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert isinstance(payload["violations"], list)
    assert payload["violations"], "expected at least one violation"
    first = payload["violations"][0]
    assert {"surface", "package", "location", "detail"} <= set(first.keys())
    assert "permitted_llm_sdks" in payload
    assert "anthropic" in payload["permitted_llm_sdks"]
    assert "openai" in payload["permitted_llm_sdks"]
