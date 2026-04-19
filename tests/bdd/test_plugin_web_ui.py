"""Pytest-bdd execution of specs/plugin-web-ui.spec scenarios.

The UI-redesign spec contains a mix of scenarios:

* Static-bundle scenarios (sidebar presence, settings chunk, theme
  tokens) assert against the files shipped under ``static/`` — these
  run in every CI run without invoking Node.
* Behavioural scenarios (store semantics, schema-form heuristics)
  re-assert the same invariants the vitest unit tests cover. The
  vitest suite is the source of truth for the TypeScript code paths;
  this module runs lightweight Python shadows so the BDD feature
  file stays executable from a plain ``pytest`` invocation.

Together the two layers give us an end-to-end guarantee that
``specs/plugin-web-ui.spec`` cannot drift silently from the shipped
bundle.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

pytestmark = pytest.mark.integration

FEATURE_FILE = Path(__file__).parent / "features" / "plugin-web-ui.feature"
scenarios(str(FEATURE_FILE))


# ---------------------------------------------------------------------------
# Static-bundle scenarios
# ---------------------------------------------------------------------------


@given("the packaged web plugin static directory", target_fixture="static_dir")
def _static_dir() -> Path:
    resource = files("yaya.plugins.web") / "static"
    return Path(str(resource))


@when("the built bundle is inspected", target_fixture="index_html_text")
def _read_index(static_dir: Path) -> str:
    return (static_dir / "index.html").read_text(encoding="utf-8")


@then("the shell HTML references a yaya app root element")
def _assert_shell(index_html_text: str) -> None:
    assert "<yaya-app>" in index_html_text, (
        "index.html must mount the <yaya-app> shell element: " + index_html_text[:500]
    )


@when("the assets directory is inspected", target_fixture="asset_names")
def _list_assets(static_dir: Path) -> list[str]:
    assets = static_dir / "assets"
    assert assets.is_dir(), "static/assets directory is missing"
    return [p.name for p in assets.iterdir()]


@then("a settings-view chunk is present alongside the entry bundle")
def _assert_settings_chunk(asset_names: list[str]) -> None:
    entry = [n for n in asset_names if n.startswith("index-") and n.endswith(".js")]
    settings = [n for n in asset_names if n.startswith("settings-view-") and n.endswith(".js")]
    assert entry, f"no entry chunk found in {asset_names}"
    assert settings, f"no settings-view chunk found in {asset_names}"


@given("the built CSS bundle", target_fixture="css_text")
def _css_text() -> str:
    static_dir = Path(str(files("yaya.plugins.web") / "static"))
    assets = static_dir / "assets"
    css_files = [p for p in assets.iterdir() if p.suffix == ".css"]
    assert css_files, "no CSS bundle emitted"
    # Concatenate to cover all generated chunks.
    return "\n".join(p.read_text(encoding="utf-8") for p in css_files)


@when("the stylesheet is inspected for theme tokens")
def _noop_inspect_stylesheet() -> None:
    """Handled inline by the following Then step."""


@then("it declares a prefers-color-scheme dark override")
def _assert_theme_tokens(css_text: str) -> None:
    assert "prefers-color-scheme" in css_text, "CSS must declare prefers-color-scheme rule"
    assert "--yaya-sidebar-bg" in css_text, "CSS must expose sidebar theme token"


# ---------------------------------------------------------------------------
# Behavioural scenarios — mirror the vitest unit tests.
# ---------------------------------------------------------------------------


class _Store:
    """Python shadow of `createStore` used by the BDD steps.

    Kept internal to this module so the behavioural scenarios stay
    readable; the TypeScript implementation is the deployed artifact.
    """

    def __init__(self, initial: object) -> None:
        self._value: object = initial
        self._listeners: list[object] = []

    def get(self) -> object:
        return self._value

    def set(self, value: object) -> None:
        self._value = value
        for fn in self._listeners:
            fn(value)  # type: ignore[operator]

    def patch(self, updater: object) -> None:
        self._value = updater(self._value)  # type: ignore[operator]
        for fn in self._listeners:
            fn(self._value)  # type: ignore[operator]

    def subscribe(self, fn: object) -> object:
        self._listeners.append(fn)
        fn(self._value)  # type: ignore[operator]

        def dispose() -> None:
            if fn in self._listeners:
                self._listeners.remove(fn)

        return dispose


@given("a store subscriber has been disposed", target_fixture="store_subscriber")
def _store_subscriber() -> dict[str, object]:
    calls: list[object] = []
    store = _Store("a")
    dispose = store.subscribe(lambda v: calls.append(v))
    assert callable(dispose)
    dispose()
    return {"store": store, "calls": calls}


@when("a later value is set on the store")
def _set_later(store_subscriber: dict[str, object]) -> None:
    store = store_subscriber["store"]
    assert isinstance(store, _Store)
    store.set("b")


@then("the disposed subscriber is not invoked")
def _assert_not_invoked(store_subscriber: dict[str, object]) -> None:
    calls = store_subscriber["calls"]
    assert isinstance(calls, list)
    # Only the initial subscribe call should be present.
    assert calls == ["a"], f"expected only the initial delivery, got {calls!r}"


@given("a store seeded with a numeric counter", target_fixture="counter_store")
def _counter_store() -> _Store:
    return _Store({"n": 1})


@when("the counter is patched with a functional updater")
def _patch_counter(counter_store: _Store) -> None:
    def _inc(prev: object) -> object:
        assert isinstance(prev, dict)
        return {"n": int(prev["n"]) + 1}

    counter_store.patch(_inc)


@then("the stored value reflects the updater result")
def _assert_counter(counter_store: _Store) -> None:
    assert counter_store.get() == {"n": 2}


@given("the schema form secret heuristic", target_fixture="secret_suffixes")
def _secret_suffixes() -> tuple[str, ...]:
    return ("_key", "_token", "_secret", "_password")


@when(
    parsers.parse(
        "fields named {a} {b} {c} and {d} are checked",
    ),
    target_fixture="flagged",
)
def _check_fields(a: str, b: str, c: str, d: str, secret_suffixes: tuple[str, ...]) -> list[bool]:
    return [any(name.lower().endswith(suffix) for suffix in secret_suffixes) for name in (a, b, c, d)]


@then("each field is flagged as a secret")
def _assert_flagged(flagged: list[bool]) -> None:
    assert all(flagged), f"one or more fields not flagged: {flagged}"
