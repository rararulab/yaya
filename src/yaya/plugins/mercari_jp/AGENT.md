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

## Filters (#191)
Beyond keyword/price/sort/status, the tool now surfaces Mercari's
native narrowing fields. All optional; unset fields round-trip as
empty lists so the Mercari payload stays identical to pre-#191:

- `category_ids: list[int]` — Mercari `categoryId` (e.g. 7 = スマホ).
- `brand_ids: list[int]` — Mercari `brandId`.
- `item_condition` — `new` / `like_new` / `no_scratches`
  `small_scratches` / `scratches` / `poor`. Maps 1:1 to Mercari's
  `itemConditionId` 1..6 via `_ITEM_CONDITION_IDS` in `search.py`.
- `shipping_payer` — `seller` (送料込み) or `buyer`.

Name/brand lookup from free-form strings to ids is deliberately
out-of-scope; future work lives in a separate `mercari_jp_category_lookup`
tool and an optional `mercari_jp_item_detail(item_id)` tool for seller
ratings / shipping details.

## Interaction
- `plugin.py` owns the plugin lifecycle and tool registration.
- `search.py` owns request models, Mercapi-compatible request signing,
  normalization, and scoring.
- Tool results use the existing `ToolOk`/`ToolError` envelope.
