"""OpenAI LLM-provider plugin (AsyncOpenAI SDK).

Bundled plugin satisfying the ``llm-provider`` category for the
``openai`` provider id. Subscribes to ``llm.call.request``, filters
payloads where ``provider == "openai"``, and responds with either
``llm.call.response`` on success or ``llm.call.error`` on failure.

Configuration is env-driven (``OPENAI_API_KEY`` required,
``OPENAI_BASE_URL`` optional) per ``docs/dev/plugin-protocol.md``.
"""

from yaya.plugins.llm_openai.plugin import OpenAIProvider

plugin: OpenAIProvider = OpenAIProvider()
"""Entry-point target — referenced by ``yaya.plugins.v1`` in pyproject.toml."""

__all__ = ["OpenAIProvider", "plugin"]
