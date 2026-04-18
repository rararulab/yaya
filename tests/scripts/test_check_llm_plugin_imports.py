"""Tests for the ``check_llm_plugin_imports`` rule in the banned-frameworks scanner.

The v1 llm-provider contract (issue #26) bans raw HTTP clients
(``httpx`` / ``requests`` / ``aiohttp``) inside ``src/yaya/plugins/llm_*``
subpackages. This module exercises that rule via:

* The live repo — ``llm_openai`` and ``llm_echo`` must pass today.
* Injected fixtures under ``tmp_path`` that simulate a rogue plugin.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_banned_frameworks.py"


def _load_script() -> Any:
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


def test_live_llm_plugins_pass(scanner: Any) -> None:
    """Bundled ``llm_openai`` + ``llm_echo`` must not import raw HTTP clients."""
    hits = scanner.check_llm_plugin_imports(REPO_ROOT / "src")
    assert hits == [], [h.render() for h in hits]


def test_injected_httpx_import_is_flagged(tmp_path: Path, scanner: Any) -> None:
    """A fake ``llm_fake`` plugin importing ``httpx`` is rejected."""
    plugin = tmp_path / "yaya" / "plugins" / "llm_fake"
    plugin.mkdir(parents=True)
    (plugin / "__init__.py").write_text("", encoding="utf-8")
    (plugin / "plugin.py").write_text("import httpx\n", encoding="utf-8")

    hits = scanner.check_llm_plugin_imports(tmp_path)
    assert hits, "expected httpx import to be flagged"
    assert any(h.package == "httpx" for h in hits)
    assert all(h.surface == "llm-plugin-import" for h in hits)
    # Use os.sep-agnostic assertion — Windows renders path separators as ``\``.
    assert any("llm_fake" in h.location and "plugin.py" in h.location for h in hits)


def test_injected_requests_from_import_is_flagged(tmp_path: Path, scanner: Any) -> None:
    plugin = tmp_path / "yaya" / "plugins" / "llm_rogue"
    plugin.mkdir(parents=True)
    (plugin / "__init__.py").write_text("", encoding="utf-8")
    (plugin / "plugin.py").write_text("from requests import get\n", encoding="utf-8")

    hits = scanner.check_llm_plugin_imports(tmp_path)
    assert any(h.package == "requests" for h in hits)


def test_injected_aiohttp_is_flagged(tmp_path: Path, scanner: Any) -> None:
    plugin = tmp_path / "yaya" / "plugins" / "llm_rogue"
    plugin.mkdir(parents=True)
    (plugin / "__init__.py").write_text("", encoding="utf-8")
    (plugin / "plugin.py").write_text("import aiohttp\n", encoding="utf-8")

    hits = scanner.check_llm_plugin_imports(tmp_path)
    assert any(h.package == "aiohttp" for h in hits)


def test_non_llm_plugin_is_not_flagged(tmp_path: Path, scanner: Any) -> None:
    """The ban is scoped to ``llm_*`` — other plugins may use httpx freely.

    e.g. a web adapter or an HTTP tool legitimately uses ``httpx``;
    that is not what the v1 llm-provider contract forbids.
    """
    plugin = tmp_path / "yaya" / "plugins" / "tool_http"
    plugin.mkdir(parents=True)
    (plugin / "__init__.py").write_text("", encoding="utf-8")
    (plugin / "plugin.py").write_text("import httpx\n", encoding="utf-8")

    hits = scanner.check_llm_plugin_imports(tmp_path)
    assert hits == [], [h.render() for h in hits]


def test_openai_and_anthropic_sdk_imports_are_allowed(tmp_path: Path, scanner: Any) -> None:
    """The two approved SDKs must not be flagged even inside ``llm_*``."""
    plugin = tmp_path / "yaya" / "plugins" / "llm_compliant"
    plugin.mkdir(parents=True)
    (plugin / "__init__.py").write_text("", encoding="utf-8")
    (plugin / "plugin.py").write_text(
        "import openai\nimport anthropic\nfrom openai import AsyncOpenAI\n",
        encoding="utf-8",
    )

    hits = scanner.check_llm_plugin_imports(tmp_path)
    assert hits == []


def test_full_run_includes_llm_plugin_violations(tmp_path: Path, scanner: Any) -> None:
    """``run()`` aggregates the new rule alongside the pre-existing scans."""
    (tmp_path / "src" / "yaya" / "plugins" / "llm_rogue").mkdir(parents=True)
    (tmp_path / "src" / "yaya" / "plugins" / "llm_rogue" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "yaya" / "plugins" / "llm_rogue" / "plugin.py").write_text("import httpx\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0"\ndependencies = []\n',
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()

    result = scanner.run(tmp_path)
    assert not result.ok
    assert any(v.surface == "llm-plugin-import" and v.package == "httpx" for v in result.violations)
