"""Ordered kernel configuration loader.

Resolves yaya's settings in a fixed merge order, most-specific wins:

1. Command-line flags (handled per command in ``yaya.cli.commands``).
2. Environment variables (``YAYA_*``). Nested keys use ``__`` as the
   delimiter so ``YAYA_LLM_OPENAI__MODEL=gpt-4o`` lands at
   ``config.plugin_config("llm_openai")["model"]``.
3. User config file at ``$XDG_CONFIG_HOME/yaya/config.toml`` (default
   ``~/.config/yaya/config.toml``). Absent is fine — no auto-create.
4. Built-in defaults declared on :class:`KernelConfig`.

Plugin authors do NOT subclass this. Each plugin reads its own
sub-tree via :meth:`KernelContext.config` (populated by the registry
from :meth:`KernelConfig.plugin_config`); the structure inside that
sub-tree is the plugin's business.

**Extra-key handling.** Pydantic-settings only honours
``env_nested_delimiter`` for declared fields. To support arbitrary
plugin namespaces, this module ships a custom env source
(:class:`_NestedEnvExtras`) that scans ``YAYA_<NS>__<KEY>`` for any
``<NS>`` that is *not* a declared kernel field and groups them into
nested dicts. Combined with ``extra="allow"`` they land in
:attr:`pydantic.BaseModel.model_extra` and are exposed via
:meth:`KernelConfig.plugin_config`.

Layering: only imports ``pydantic`` / ``pydantic_settings`` and the
Python standard library. No imports from ``cli``, ``plugins``, or
``core``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast, override

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

__all__ = [
    "CONFIG_PATH",
    "CompactionConfig",
    "KernelConfig",
    "SessionConfig",
    "default_config_path",
    "flatten_kernel_config",
    "load_config",
]


def default_config_path() -> Path:
    """Return the resolved user config path.

    Honors ``XDG_CONFIG_HOME``; falls back to ``~/.config`` when unset
    or empty per the XDG Base Directory Specification.
    """
    raw = os.environ.get("XDG_CONFIG_HOME") or ""
    base = Path(raw) if raw else Path.home() / ".config"
    return base / "yaya" / "config.toml"


CONFIG_PATH = default_config_path()
"""Module-level default. Tests monkeypatch this to redirect the loader."""

_ENV_PREFIX = "YAYA_"
_ENV_DELIM = "__"


class _NestedEnvExtras(PydanticBaseSettingsSource):
    """Lift ``YAYA_<NS>__<KEY>...`` env vars into nested dicts under ``<ns>``.

    Only non-declared top-level namespaces are surfaced here — declared
    fields (``port``, ``log_level``, ...) stay with the standard
    ``EnvSettingsSource`` so type coercion and validation paths
    continue to work as documented.

    Keys are lowercased; the rest of the dotted path follows the same
    ``__`` delimiter so ``YAYA_LLM_OPENAI__SUB__KEY=v`` becomes
    ``{"llm_openai": {"sub": {"key": "v"}}}``.
    """

    @override
    def get_field_value(
        self,
        field: Any,
        field_name: str,
    ) -> tuple[Any, str, bool]:
        # Not used: we never claim individual declared fields.
        return None, field_name, False

    @override
    def __call__(self) -> dict[str, Any]:
        declared = set(self.settings_cls.model_fields.keys())
        result: dict[str, Any] = {}
        for raw_key, raw_value in os.environ.items():
            if not raw_key.startswith(_ENV_PREFIX):
                continue
            stripped = raw_key[len(_ENV_PREFIX) :]
            if _ENV_DELIM not in stripped:
                continue  # Top-level fields belong to EnvSettingsSource.
            head, _, tail = stripped.partition(_ENV_DELIM)
            namespace = head.lower()
            if namespace in declared:
                # Declared nested fields stay with the standard env source.
                continue
            cursor: dict[str, Any] = result.setdefault(namespace, {})
            parts = [p.lower() for p in tail.split(_ENV_DELIM) if p]
            if not parts:
                continue
            for part in parts[:-1]:
                nxt: Any = cursor.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    cursor[part] = nxt
                cursor = cast("dict[str, Any]", nxt)
            cursor[parts[-1]] = raw_value
        return result


class SessionConfig(BaseModel):
    """Session / tape-store settings (see ``docs/dev/plugin-protocol.md``).

    Attributes:
        store: ``"file"`` persists tapes as jsonl under :attr:`dir`;
            ``"memory"`` keeps them in-process (tests, ``yaya hello``).
        dir: Directory for jsonl files. When ``None`` (default) the
            kernel derives it from ``YAYA_STATE_DIR`` /
            ``XDG_STATE_HOME`` — see
            :func:`yaya.kernel.session.default_session_dir`.
        default_id: Session id assumed when no explicit ``--resume``
            is supplied. ``None`` means "mint a fresh id per run".
    """

    store: Literal["file", "memory"] = "file"
    dir: Path | None = None
    default_id: str | None = None


class CompactionConfig(BaseModel):
    """Conversation compaction settings (see :mod:`yaya.kernel.compaction`).

    Compaction is **opt-in** by default — ``auto=False`` — so upgrading
    the kernel never surprises a user with summarised context. Operators
    flip the knob in ``config.toml`` once they have wired a summariser:

    .. code-block:: toml

        [session.compaction]
        auto = true
        threshold_tokens = 50_000
        target_tokens = 10_000

    Attributes:
        auto: Master switch for the auto-trigger
            :class:`~yaya.kernel.compaction.CompactionManager`. Off by
            default; manual :meth:`~yaya.kernel.session.Session.compact`
            always works regardless.
        threshold_tokens: Approximate post-last-anchor window size
            (per :func:`~yaya.kernel.compaction.estimate_text_tokens`)
            that triggers an auto-compaction. ``0`` disables the check
            even when ``auto=True``.
        target_tokens: Soft ceiling handed to the summariser so it
            knows how much the compacted history should weigh.
    """

    auto: bool = False
    threshold_tokens: int = 50_000
    target_tokens: int = 10_000


class KernelConfig(BaseSettings):
    """Resolved kernel + plugin configuration.

    Top-level fields are kernel concerns. Anything else lives under an
    arbitrary top-level key keyed by plugin name (``llm_openai``,
    ``web``, ...) and is exposed via :meth:`plugin_config`. We rely on
    pydantic v2's ``extra="allow"`` to stash unknown keys in
    :attr:`model_extra`.

    The merge order — env > toml > defaults — is implemented in
    :meth:`settings_customise_sources`.
    """

    model_config = SettingsConfigDict(
        env_prefix=_ENV_PREFIX,
        env_nested_delimiter=_ENV_DELIM,
        # Plugin namespaces live under arbitrary top-level keys; extra
        # MUST be "allow" so pydantic preserves them in model_extra.
        extra="allow",
    )

    bind_host: str = "127.0.0.1"
    """Loopback bind address per the local-first invariant in GOAL.md."""

    port: int = 0
    """Default port. ``0`` means "let the OS pick". CLI flags override."""

    plugins_enabled: list[str] | None = None
    """If set, only these plugins load. ``None`` means "load all discovered"."""

    plugins_disabled: list[str] = Field(default_factory=list)
    """Plugins to skip even when discovered."""

    log_level: str = "INFO"
    """Root log level. Per-plugin overrides happen via ``YAYA_LOG_LEVEL``."""

    session: SessionConfig = Field(default_factory=SessionConfig)
    """Session / tape-store policy. See :class:`SessionConfig`."""

    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    """Conversation compaction policy. See :class:`CompactionConfig`."""

    @classmethod
    @override
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Pin the merge order: init > env (kernel) > env (extras) > toml > defaults.

        Pydantic-settings 2.x consults sources left-to-right and the
        first hit wins. ``init_settings`` carries explicit kwargs (used
        only by tests that bypass the env/file path); env vars beat the
        TOML file; built-in defaults come last via the model itself.

        The TOML source is bound dynamically here so tests can monkeypatch
        the module-level :data:`CONFIG_PATH` between :class:`KernelConfig`
        instantiations.
        """
        sources: list[PydanticBaseSettingsSource] = [
            init_settings,
            env_settings,
            _NestedEnvExtras(settings_cls),
        ]
        toml_path = CONFIG_PATH
        if toml_path.exists():
            sources.append(TomlConfigSettingsSource(settings_cls, toml_file=toml_path))
        return tuple(sources)

    def plugin_config(self, plugin_name: str) -> Mapping[str, Any]:
        """Return the resolved config sub-tree for ``plugin_name``.

        Returns an empty mapping when the plugin has no namespace —
        plugins MUST tolerate an empty config and fall back to their
        own defaults rather than raising on first use.

        Args:
            plugin_name: The plugin's ``name`` attribute. Matched
                verbatim (lower-case) against the top-level TOML or env
                key, so ``llm_openai`` reads ``[llm_openai]`` from TOML
                and ``YAYA_LLM_OPENAI__*`` from the environment.
        """
        extra = self.model_extra or {}
        value: Any = extra.get(plugin_name)
        if isinstance(value, Mapping):
            # Defensive copy: callers shouldn't be able to mutate our
            # internal state through the returned mapping.
            return {str(k): v for k, v in cast("dict[Any, Any]", value).items()}
        return {}


def load_config() -> KernelConfig:
    """Construct a :class:`KernelConfig` honoring the documented merge order.

    Equivalent to ``KernelConfig()`` today — exists as a stable factory
    so callers (CLI, registry) don't depend on the constructor signature
    if a future revision needs to inject extra sources.
    """
    return KernelConfig()


def flatten_kernel_config(cfg: KernelConfig) -> dict[str, Any]:
    """Flatten a :class:`KernelConfig` into dotted-key → value pairs.

    Used by :meth:`yaya.kernel.config_store.ConfigStore.migrate_from_kernel_config`
    to seed a fresh DB from the legacy TOML / env layering. Plugin
    sub-trees live under ``plugin.<name>.<key>`` so the live store's
    prefix queries (``plugin.<name>.``) match what
    :meth:`yaya.kernel.registry.PluginRegistry._make_context` needs to
    expose per-plugin views.

    Top-level kernel scalars (``port``, ``bind_host``, ``log_level``,
    ``plugins_enabled``, ``plugins_disabled``) map to their direct
    names so strategy-react's existing ``ctx.config["provider"]``
    reads keep working after migration.

    Nested kernel sub-models (``session.*``, ``compaction.*``)
    flatten through their attribute names.

    Args:
        cfg: Resolved :class:`KernelConfig` from :func:`load_config`.

    Returns:
        A flat dict ready to hand to
        :meth:`yaya.kernel.config_store.ConfigStore.migrate_from_kernel_config`.
    """
    out: dict[str, Any] = {}

    # Declared scalar / simple fields.
    out["bind_host"] = cfg.bind_host
    out["port"] = cfg.port
    out["log_level"] = cfg.log_level
    if cfg.plugins_enabled is not None:
        out["plugins_enabled"] = list(cfg.plugins_enabled)
    if cfg.plugins_disabled:
        out["plugins_disabled"] = list(cfg.plugins_disabled)

    # Nested kernel sub-models — only persist non-default fields so a
    # fresh install does not wake up with every knob enumerated.
    session_dump: dict[str, Any] = cfg.session.model_dump(exclude_none=True)
    for k, v in session_dump.items():
        out[f"session.{k}"] = _jsonify(v)

    compaction_dump: dict[str, Any] = cfg.compaction.model_dump(exclude_none=True)
    for k, v in compaction_dump.items():
        out[f"compaction.{k}"] = _jsonify(v)

    # Plugin sub-trees under model_extra — key each leaf as
    # ``plugin.<namespace>.<dotted-path>`` so downstream prefix
    # queries match the plugin the leaf belongs to.
    extras = cfg.model_extra or {}
    for namespace, value in extras.items():
        _flatten_into(f"plugin.{namespace}", value, out)

    return out


def _jsonify(value: Any) -> Any:
    """Coerce non-JSON primitives (``Path``, ``PosixPath``) into strings."""
    if isinstance(value, Path):
        return str(value)
    return value


def _flatten_into(prefix: str, value: Any, out: dict[str, Any]) -> None:
    """Recursively flatten a nested config sub-tree into dotted keys."""
    if isinstance(value, Mapping):
        for k, v in cast("Mapping[str, Any]", value).items():
            _flatten_into(f"{prefix}.{k}", v, out)
        return
    # Lists, scalars, and None pass through verbatim; migration target
    # accepts any JSON-safe value.
    out[prefix] = _jsonify(value)
