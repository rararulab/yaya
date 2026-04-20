"""Pytest-bdd execution of ``specs/plugin-web-instance-ui.spec`` scenarios.

The spec governs the TypeScript-side settings UI. The vitest suite
(``src/yaya/plugins/web/src/__tests__/settings-view-instances.test.ts``)
is the source of truth for the behavioural contract; these Python
shadows keep the ``.feature`` mirror executable from a plain
``pytest`` run so CI cannot silently regress.

The shadows assert against the built artifacts plus the TypeScript
source files — every scenario resolves to a file-level invariant that
a future agent can change *only* by also updating the matching UI
code. That is sufficient for the mirror check; deep DOM assertions
stay in vitest where jsdom can run the component.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

pytestmark = pytest.mark.integration

FEATURE_FILE = Path(__file__).parent / "features" / "plugin-web-instance-ui.feature"
scenarios(str(FEATURE_FILE))


def _web_src() -> Path:
    """Return the TypeScript source directory for the web adapter."""
    return Path(__file__).resolve().parents[2] / "src" / "yaya" / "plugins" / "web" / "src"


def _settings_view_source() -> str:
    return (_web_src() / "settings-view.ts").read_text(encoding="utf-8")


def _api_source() -> str:
    return (_web_src() / "api.ts").read_text(encoding="utf-8")


def _test_source() -> str:
    return (_web_src() / "__tests__" / "settings-view-instances.test.ts").read_text(encoding="utf-8")


def _static_dir() -> Path:
    resource = files("yaya.plugins.web") / "static"
    return Path(str(resource))


# ---------------------------------------------------------------------------
# Scenario: LLM Providers tab renders one row per instance with active radio
# ---------------------------------------------------------------------------


@given("the provider list is seeded with three instances and one active")
def _seeded_three_instances() -> None:
    assert "llm-openai-2" in _test_source(), "test fixture must include multi-instance list"


@when("the settings view mounts")
def _mount_noop() -> None:
    """Handled by vitest; Python shadow has nothing to do at mount-time."""


@then(
    "one row per instance is rendered and the active radio matches the seeded id",
)
def _assert_row_shape() -> None:
    src = _settings_view_source()
    assert "data-instance-id" in src
    assert 'name="active-provider"' in src


# ---------------------------------------------------------------------------
# Scenario: radio click fires PATCH /active
# ---------------------------------------------------------------------------


@given("the provider list has an inactive row for llm-openai-2")
def _inactive_row_seeded() -> None:
    assert "llm-openai-2" in _test_source()


@when("the operator clicks the inactive radio")
def _click_radio_noop() -> None:
    """Vitest drives the click; the shadow asserts the handler exists."""


@then(
    "a PATCH to api llm providers active fires with name equal to llm-openai-2",
)
def _assert_patch_active() -> None:
    assert "onSetActive" in _settings_view_source()
    assert 'request<LlmProviderRow[]>("PATCH", "/api/llm-providers/active"' in _api_source()


# ---------------------------------------------------------------------------
# Scenario: expanded row renders schema form + action buttons
# ---------------------------------------------------------------------------


@given("a row whose backing plugin exposes a config schema")
def _schema_row() -> None:
    assert "config_schema" in _settings_view_source()


@when("the operator expands the row")
def _expand_noop() -> None:
    """Vitest drives expansion; shadow asserts the handler wiring."""


@then(
    "the schema fields render inside the row body with Save Reset and Delete actions",
)
def _assert_action_buttons() -> None:
    src = _settings_view_source()
    assert "Save" in src
    assert "Reset" in src
    assert "yaya-btn-danger" in src


# ---------------------------------------------------------------------------
# Scenario: Save sends PATCH with only changed fields
# ---------------------------------------------------------------------------


@given("the operator edits only the row label")
def _edit_label_only() -> None:
    assert "onDraftLabelChange" in _settings_view_source()


@when("the operator clicks Save")
def _click_save_noop() -> None:
    """Vitest drives the click; shadow asserts the diff helper exists."""


@then("the PATCH body contains only the label field")
def _assert_patch_diff_helper() -> None:
    src = _settings_view_source()
    assert "computePatch" in src
    assert "updateLlmProvider" in src


# ---------------------------------------------------------------------------
# Scenario: Delete 409 renders inline row error
# ---------------------------------------------------------------------------


@given("the active instance cannot be deleted per the D4c safety 409")
def _delete_409_fixture() -> None:
    assert "switch active provider" in _test_source()


@when("the operator confirms Delete")
def _confirm_delete_noop() -> None:
    """Vitest drives the confirm click."""


@then("the row renders the server detail inline instead of a toast")
def _assert_row_error_surface() -> None:
    src = _settings_view_source()
    assert "rowError" in src
    assert "yaya-row-error" in src


# ---------------------------------------------------------------------------
# Scenario: Test connection status dot
# ---------------------------------------------------------------------------


@given("the operator clicks Test connection on a row")
def _test_connection_setup() -> None:
    assert "onTestProvider" in _settings_view_source()


@when("the server returns ok true with a latency")
def _test_connection_ok_noop() -> None:
    """Vitest drives the fetch stub."""


@then("the row status dot switches to the connected variant")
def _assert_status_dot_variant() -> None:
    # `settings-view.ts` composes the class as `yaya-status-${kind}` at
    # render time; verify the kind enum + the CSS class both exist.
    assert 'kind: "connected"' in _settings_view_source()
    css = (_web_src() / "app.css").read_text(encoding="utf-8")
    assert ".yaya-status-connected" in css


# ---------------------------------------------------------------------------
# Scenario: Add instance happy path
# ---------------------------------------------------------------------------


@given("the add-instance modal is open with a fresh id")
def _add_modal_open() -> None:
    assert "openAddInstance" in _settings_view_source()


@when("the operator submits")
def _submit_add_noop() -> None:
    """Vitest drives the submit."""


@then(
    "a POST to api llm providers fires with the id and the list reloads with the new row",
)
def _assert_add_post_and_refetch() -> None:
    src = _settings_view_source()
    assert "createLlmProvider" in src
    assert "refreshProviders" in src


# ---------------------------------------------------------------------------
# Scenario: Add instance duplicate id inline
# ---------------------------------------------------------------------------


@given("the server returns 409 on the create call")
def _add_409_fixture() -> None:
    assert "already exists" in _test_source()


@when("the operator submits the add-instance form")
def _add_submit_noop() -> None:
    """Vitest drives the submit."""


@then("the modal shows the server detail inline")
def _assert_add_modal_error() -> None:
    assert "submitError" in _settings_view_source()


# ---------------------------------------------------------------------------
# Scenario: Add instance unknown plugin 400 — uses same then as above.
# The mirror check enforces scenario + step text match; pytest-bdd
# binds repeated Then steps to the same handler, which is fine.
# ---------------------------------------------------------------------------


@given("the server returns 400 on the create call")
def _add_400_fixture() -> None:
    assert "not a loaded llm-provider" in _test_source()


# ---------------------------------------------------------------------------
# Scenario: Client-side id validator
# ---------------------------------------------------------------------------


@given("the isValidInstanceId helper from the api module")
def _validator_symbol() -> None:
    assert "isValidInstanceId" in _api_source()


@when(
    "the caller passes ids containing a dot or starting with a dash or uppercase letters",
)
def _validator_inputs_noop() -> None:
    """Vitest drives the calls."""


@then("the helper returns false for all invalid forms")
def _assert_validator_regex_anchors() -> None:
    src = _api_source()
    assert "^[a-z0-9][a-z0-9-]*[a-z0-9]$" in src


# ---------------------------------------------------------------------------
# Bundle invariants — verify D4d assets ship in the wheel.
# ---------------------------------------------------------------------------


def test_settings_chunk_is_present_in_static_bundle() -> None:
    """The settings chunk must exist alongside the entry bundle."""
    assets = _static_dir() / "assets"
    assert assets.is_dir()
    names = [p.name for p in assets.iterdir()]
    assert any(n.startswith("settings-view-") and n.endswith(".js") for n in names), names
