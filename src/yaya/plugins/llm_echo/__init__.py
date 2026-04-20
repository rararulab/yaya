"""Echo LLM-provider plugin (deterministic, zero-config, dev-only).

Bundled plugin satisfying the ``llm-provider`` category. After D4b
(#123) the plugin is **instance-scoped**: it subscribes to
``llm.call.request`` and ``config.updated``, tracks the set of
instance ids under ``providers.<id>.*`` whose ``plugin`` meta
equals ``llm-echo``, and replies with a deterministic
``(echo) <last user message>`` body so users can verify the kernel
end-to-end without any API key. The D4a bootstrap seeds a default
``llm-echo`` instance so a fresh ``yaya serve`` round-trips without
any configuration.
"""

from yaya.plugins.llm_echo.plugin import EchoLLM

plugin: EchoLLM = EchoLLM()
"""Entry-point target — referenced by ``yaya.plugins.v1`` in pyproject.toml."""

__all__ = ["EchoLLM", "plugin"]
