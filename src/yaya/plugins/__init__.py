"""Bundled yaya plugins.

Each subpackage exposes a ``plugin`` attribute conforming to
:class:`yaya.kernel.plugin.Plugin`. Bundled plugins are registered in
this project's ``pyproject.toml`` under the ``yaya.plugins.v1`` entry
point group and load through the **same** code path as third-party
plugins — see :mod:`yaya.kernel.registry` and
``docs/dev/plugin-protocol.md``.
"""
