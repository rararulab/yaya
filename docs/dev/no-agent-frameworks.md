# No third-party AI agent frameworks

yaya is the kernel. The agent loop, plugin registry, tool orchestration,
strategy dispatch, memory, and session state are all implemented in
this repo. Using a higher-level agent framework would defeat the point
and — more pressingly — violates an external constraint on this
project.

## The rule

> You are not permitted to use high-level AI agent frameworks. This
> includes (but is not limited to) LangChain, LangGraph, LlamaIndex,
> and similar libraries designed to simplify agent creation.
>
> Permitted: libraries for web scraping, data handling, and the
> official SDKs provided by OpenAI (e.g., `openai`) or Anthropic
> (e.g., `anthropic`) for direct interaction with their LLM APIs.

## Explicit block list

The following PyPI packages are banned from `pyproject.toml`
dependencies, `src/yaya/**` imports, test code, and `vendor/`
(except as reference material, not imported):

- LangChain family: `langchain`, `langchain-core`, `langchain-openai`,
  `langchain-community`, `langgraph`, `langsmith`, `langserve`.
- LlamaIndex family: `llama-index`, `llama-index-core`,
  `llama-index-llms-*`, `llama-parse`.
- Haystack family: `haystack-ai`, `farm-haystack`.
- AutoGen family: `autogen`, `pyautogen`, `autogenstudio`.
- CrewAI family: `crewai`, `crewai-tools`.
- Semantic Kernel: `semantic-kernel`.
- DSPy / Instructor / Guidance / Marvin / Griptape / Mirascope / Smol.
- Anthropic / OpenAI agent-flavoured wrappers: `anthropic-agents`,
  `openai-agents` — use the **raw** `openai` and `anthropic` SDKs only.

## Permitted LLM access

- `openai` (the official Python SDK, `AsyncOpenAI`). Covers OpenAI,
  Azure OpenAI, and every OpenAI-compatible endpoint (DeepSeek,
  Moonshot, ollama, lm-studio, LiteLLM gateway) via
  `OPENAI_BASE_URL` + `OPENAI_API_KEY`.
- `anthropic` (the official Python SDK, `AsyncAnthropic`). Covers
  Claude with native tool use, prompt caching, and streaming.

No other LLM client, wrapper, router, or gateway library is permitted.
If a new provider is needed, add a yaya `llm-provider` plugin that
calls the vendor's official SDK.

## Permitted general-purpose libraries

As long as a library is **not** positioned as an agent framework,
these categories are fine:

- HTTP clients: `httpx` (for non-LLM traffic only).
- Web frameworks: `fastapi`, `starlette`, `websockets`, `uvicorn`.
- CLI: `typer`, `rich`.
- Data validation & settings: `pydantic`, `pydantic-settings`.
- Persistence: `aiosqlite`, stdlib `sqlite3`, `jsonlines` — **not**
  vector-store-as-a-service libraries that bundle retrieval-chain
  logic.
- Logging: `loguru`.
- Test infrastructure: `pytest` and ecosystem (`pytest-asyncio`,
  `pytest-httpx`, `pytest-cov`, `pytest-timeout`, `pytest-randomly`,
  `hypothesis`, `syrupy`).
- Build: `hatchling`, `pyinstaller`, `uv`.
- MCP: the **official** `mcp` SDK (from Anthropic). Community wrappers
  like `fastmcp` are permitted only if the core interface used is the
  protocol SDK; review required.

## When you are tempted to pull in a framework

Don't. Vendor a ~200-line minimal implementation into
`src/yaya/kernel/` instead. Specifically:

| Temptation | yaya equivalent |
|---|---|
| LangChain `AgentExecutor` | The kernel's fixed agent loop (#12). |
| LangGraph state machine | Plugin dispatch via the closed event catalog (#11). |
| LlamaIndex VectorStoreIndex | A future `memory_embeddings` plugin calling the vendor SDK directly (e.g. `openai.embeddings.create`). |
| LangChain `ChatMessageHistory` | The session tape (#32). |
| LangChain `Tool` abstraction | The tool contract (#27). |
| LangSmith tracing | The event bus + tape log (#32). |

If a vendor-ban question is truly ambiguous, open a `governance`
issue before writing the import.

## Enforcement

- PR review checks `pyproject.toml` and `git diff` against this list.
- CI runs a dependency scanner (see the tracking issue) that fails
  the build on a banned package or on a banned import in `src/yaya/`.
- `vendor/` contains reference repos (e.g. `bub/`, `kimi-cli/`) — they
  are NEVER imported at runtime; `src/yaya/` must not reference
  anything under `vendor/`.
