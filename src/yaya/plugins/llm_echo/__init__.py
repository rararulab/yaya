"""Echo LLM-provider plugin (deterministic, zero-config, dev-only).

Bundled plugin satisfying the ``llm-provider`` category for the
``echo`` provider id. Subscribes to ``llm.call.request``, filters
payloads where ``provider == "echo"``, and replies with a
deterministic ``(echo) <last user message>`` body so users can
verify the kernel end-to-end without any API key.
"""

from yaya.plugins.llm_echo.plugin import EchoLLM

plugin: EchoLLM = EchoLLM()
"""Entry-point target — referenced by ``yaya.plugins.v1`` in pyproject.toml."""

__all__ = ["EchoLLM", "plugin"]
