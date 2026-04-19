"""Post-install smoke for ``yaya plugin list``.

Guards the 0.1 bundled plugin catalog. If a future pyproject / wheel
change drops an entry-point (or ships the wheel without the web
adapter's bundled assets), the registry snapshot will show fewer
rows and this test fails before a release ships.
"""

from __future__ import annotations

import pytest

from .conftest import json_stdout, run

pytestmark = [pytest.mark.integration]

# Superset check — extras are allowed, removals fail. Maps each
# bundled plugin name to its declared category so a silent category
# drift (e.g. web moved from `adapter` to `tool`) also fails.
EXPECTED_BUNDLED: dict[str, str] = {
    "agent-tool": "tool",
    "llm-echo": "llm-provider",
    "llm-openai": "llm-provider",
    "mcp-bridge": "tool",
    "memory-sqlite": "memory",
    "strategy-react": "strategy",
    "tool-bash": "tool",
    "web": "adapter",
}


def test_plugin_list_includes_bundled_plugins(yaya_bin: str) -> None:
    """Every bundled entry-point loads post-install with the right category."""
    payload = json_stdout(run(yaya_bin, "--json", "plugin", "list"))
    assert payload["ok"] is True
    assert payload["action"] == "plugin.list"
    plugins = payload.get("plugins")
    assert isinstance(plugins, list), "plugins field must be a list"

    by_name = {row["name"]: row for row in plugins if isinstance(row, dict)}
    missing = sorted(set(EXPECTED_BUNDLED) - set(by_name))
    assert not missing, (
        f"bundled plugins missing from `yaya plugin list`: {missing}. "
        f'Check pyproject.toml [project.entry-points."yaya.plugins.v1"]'
    )

    for name, expected_category in EXPECTED_BUNDLED.items():
        row = by_name[name]
        assert row.get("status") == "loaded", (
            f"plugin {name!r} loaded with status {row.get('status')!r}; "
            f"expected 'loaded' — check for missing deps or import errors"
        )
        assert row.get("category") == expected_category, (
            f"plugin {name!r} has category {row.get('category')!r}; expected {expected_category!r}"
        )


def test_plugin_list_text_mode_exits_zero(yaya_bin: str) -> None:
    """Text-mode `plugin list` renders the rich table without crashing."""
    result = run(yaya_bin, "plugin", "list")
    assert result.returncode == 0, result.stderr
    # Each bundled plugin's name appears somewhere in the table output.
    for name in EXPECTED_BUNDLED:
        assert name in result.stdout, f"plugin {name!r} missing from text-mode output; stdout:\n{result.stdout}"
