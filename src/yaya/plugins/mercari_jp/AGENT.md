# src/yaya/plugins/mercari_jp — Agent Guidelines

## Philosophy
Mercapi-backed Mercari Japan search tool. This plugin gives the agent a
structured product-discovery surface for Mercari Japan without changing
the kernel.

## External Reality
- `specs/plugin-mercari_jp.spec` is the BDD contract.
- Tests live under `tests/plugins/mercari_jp/` and must not hit the live
  network.
- The request shape follows `take-kun/mercapi` search behavior, adapted
  locally because upstream `mercapi 0.4.2` requires `httpx<0.28` while
  yaya uses current `httpx`.
- Result coverage may differ from native Mercari JP web UI ranking.

## Constraints
- `Category.TOOL`; registers only the v1 tool `mercari_jp_search`.
- Do not log in, purchase, mutate carts, store session cookies, solve
  CAPTCHA, rotate proxies, or bypass 403/anti-bot responses.
- Missing optional fields such as seller rating or condition are
  warnings/nulls, not parser failures.
- Use deterministic parsing and scoring; the LLM writes user-facing
  recommendations from the returned JSON.

## Interaction
- `plugin.py` owns the plugin lifecycle and tool registration.
- `search.py` owns request models, Mercapi-compatible request signing,
  normalization, and scoring.
- Tool results use the existing `ToolOk`/`ToolError` envelope.
