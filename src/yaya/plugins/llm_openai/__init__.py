"""OpenAI LLM-provider plugin (AsyncOpenAI SDK).

Bundled plugin satisfying the ``llm-provider`` category. After D4b
(#123) the plugin is **instance-scoped**: it subscribes to
``llm.call.request`` and ``config.updated``, maintains one
``AsyncOpenAI`` client per configured ``providers.<id>.*`` row whose
``plugin`` meta equals ``llm-openai``, and dispatches by matching
``ev.payload["provider"]`` (an *instance id*) against the owned
client dict. Per-instance ``api_key`` / ``base_url`` / ``model``
fields win over the legacy ``OPENAI_API_KEY`` / ``OPENAI_BASE_URL``
env vars. See ``docs/dev/plugin-protocol.md`` for the dispatch and
hot-reload patterns.
"""

from yaya.plugins.llm_openai.plugin import OpenAIProvider

plugin: OpenAIProvider = OpenAIProvider()
"""Entry-point target — referenced by ``yaya.plugins.v1`` in pyproject.toml."""

__all__ = ["OpenAIProvider", "plugin"]
