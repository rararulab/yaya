"""Tests for ``yaya.kernel.config``.

AC-bindings from ``specs/kernel-config.spec``:

* AC-01 env overrides file → ``test_env_var_overrides_file_value``
* AC-02 redaction is exercised in ``tests/cli/test_config.py``.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from yaya.kernel import config as cfg_mod
from yaya.kernel.config import KernelConfig, load_config


@pytest.fixture(autouse=True)
def _clean_yaya_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip ``YAYA_*`` from the env so each test starts blank.

    pytest-randomly + parallel test runs would otherwise leak settings
    across cases via the shared process env.
    """
    import os

    for key in list(os.environ):
        if key.startswith("YAYA_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def empty_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``CONFIG_PATH`` at a non-existent tmp file."""
    target = tmp_path / "config.toml"
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", target)
    return target


def test_defaults_when_no_env_no_file(empty_config_path: Path) -> None:
    _ = empty_config_path  # ensure fixture wired even though we don't read it.
    cfg = load_config()
    assert cfg.bind_host == "127.0.0.1"
    assert cfg.port == 0
    assert cfg.log_level == "INFO"
    assert cfg.plugins_disabled == []
    assert cfg.plugins_enabled is None


def test_env_var_overrides_default(monkeypatch: pytest.MonkeyPatch, empty_config_path: Path) -> None:
    """AC-01 (env over defaults). ``YAYA_PORT`` reaches ``KernelConfig.port``."""
    _ = empty_config_path
    monkeypatch.setenv("YAYA_PORT", "9000")
    cfg = load_config()
    assert cfg.port == 9000


def test_file_overrides_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A user TOML at the resolved path lifts values above the built-in defaults."""
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        dedent(
            """
            port = 8080
            log_level = "DEBUG"
            """
        ).strip()
    )
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", toml_path)

    cfg = load_config()
    assert cfg.port == 8080
    assert cfg.log_level == "DEBUG"


def test_env_var_overrides_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """AC-01: env beats the TOML file when both set the same key."""
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        dedent(
            """
            [llm_openai]
            model = "gpt-4"
            """
        ).strip()
    )
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", toml_path)
    monkeypatch.setenv("YAYA_LLM_OPENAI__MODEL", "gpt-4o")

    cfg = load_config()
    assert cfg.plugin_config("llm_openai")["model"] == "gpt-4o"


def test_plugin_namespace_via_env(monkeypatch: pytest.MonkeyPatch, empty_config_path: Path) -> None:
    """``YAYA_<NS>__<KEY>`` lifts an arbitrary plugin sub-tree into model_extra."""
    _ = empty_config_path
    monkeypatch.setenv("YAYA_LLM_OPENAI__MODEL", "gpt-4o")
    monkeypatch.setenv("YAYA_LLM_OPENAI__API_KEY", "sk-abc123")

    cfg = load_config()
    sub = cfg.plugin_config("llm_openai")
    assert sub == {"model": "gpt-4o", "api_key": "sk-abc123"}


def test_plugin_namespace_via_toml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A ``[plugin_name]`` section in TOML reaches ``plugin_config(name)``."""
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        dedent(
            """
            [tool_bash]
            timeout_s = 30
            """
        ).strip()
    )
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", toml_path)

    cfg = load_config()
    assert cfg.plugin_config("tool_bash") == {"timeout_s": 30}


def test_plugin_config_returns_empty_for_unknown_plugin(empty_config_path: Path) -> None:
    """Plugins that have no config section get an empty mapping, not None."""
    _ = empty_config_path
    cfg = load_config()
    sub = cfg.plugin_config("never-configured")
    assert sub == {}


def test_plugin_config_returns_defensive_copy(monkeypatch: pytest.MonkeyPatch, empty_config_path: Path) -> None:
    """Mutating the returned mapping must not affect the next ``plugin_config`` call."""
    _ = empty_config_path
    monkeypatch.setenv("YAYA_X_FOO__K", "v")
    cfg = load_config()

    first = dict(cfg.plugin_config("x_foo"))
    # `dict(Mapping[str, str])` returns `dict[str, str]`, but mypy infers
    # `dict[Any, Any]` here from the `cfg.plugin_config` Mapping ABI; the
    # write is intentional — we want to verify the original Mapping is NOT
    # a view that propagates mutation. Ignore the spurious index complaint.
    first["k"] = "tampered"  # type: ignore[index]
    second = cfg.plugin_config("x_foo")
    assert second["k"] == "v"


def test_default_config_path_uses_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``XDG_CONFIG_HOME`` is honoured when set; otherwise ``~/.config``."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    p = cfg_mod.default_config_path()
    assert p == tmp_path / "xdg" / "yaya" / "config.toml"

    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path / "home"))
    p2 = cfg_mod.default_config_path()
    assert p2 == tmp_path / "home" / ".config" / "yaya" / "config.toml"


def test_kernel_config_constructible_directly() -> None:
    """Direct ``KernelConfig(port=...)`` (init-source path) works for tests."""
    cfg = KernelConfig(port=4242, bind_host="127.0.0.1")
    assert cfg.port == 4242


def test_deeply_nested_env_namespace(monkeypatch: pytest.MonkeyPatch, empty_config_path: Path) -> None:
    """``YAYA_<NS>__<SUB>__<KEY>`` builds a nested dict tree."""
    _ = empty_config_path
    monkeypatch.setenv("YAYA_LLM_OPENAI__NESTED__INNER", "v")
    cfg = load_config()
    sub = cfg.plugin_config("llm_openai")
    assert sub == {"nested": {"inner": "v"}}


def test_declared_field_env_does_not_appear_in_extras(monkeypatch: pytest.MonkeyPatch, empty_config_path: Path) -> None:
    """A delimited env var whose top-level token is a declared field is skipped.

    The custom :class:`_NestedEnvExtras` source must NOT pull such a
    var into ``model_extra``; the standard env source still owns it.
    """
    _ = empty_config_path
    # ``log_level`` IS declared; the custom source must short-circuit.
    monkeypatch.setenv("YAYA_LOG_LEVEL__SUB", "noise")
    cfg = load_config()
    assert cfg.plugin_config("log_level") == {}


def test_get_field_value_returns_none_sentinel(empty_config_path: Path) -> None:
    """The custom source never claims declared fields directly."""
    _ = empty_config_path
    from yaya.kernel.config import _NestedEnvExtras

    src = _NestedEnvExtras(KernelConfig)
    field = next(iter(KernelConfig.model_fields.values()))
    value, name, complex_flag = src.get_field_value(field, "port")
    assert value is None
    assert name == "port"
    assert complex_flag is False


def test_unrelated_env_var_ignored(monkeypatch: pytest.MonkeyPatch, empty_config_path: Path) -> None:
    """Vars without the YAYA_ prefix are not picked up at all."""
    _ = empty_config_path
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak")
    cfg = load_config()
    assert "openai_api_key" not in (cfg.model_extra or {})


def test_env_top_level_alone_does_not_create_namespace(
    monkeypatch: pytest.MonkeyPatch, empty_config_path: Path
) -> None:
    """``YAYA_FOO=bar`` (no delimiter) is not lifted into extras by our source."""
    _ = empty_config_path
    monkeypatch.setenv("YAYA_FOO", "bar")
    cfg = load_config()
    # The standard env source DOES preserve it under model_extra (since
    # extra="allow" is set), but the value is the bare string. We just
    # assert the custom source did not synthesize a namespace dict for
    # the unprefixed-but-non-nested case.
    extras = cfg.model_extra or {}
    assert extras.get("foo") != {}
