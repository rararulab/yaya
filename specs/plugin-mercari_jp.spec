spec: task
name: "plugin-mercari_jp"
tags: [plugin, tool, bdd]
---

## Intent

The bundled Mercari Japan plugin lets the default agent retrieve
structured product candidates through a Mercapi-compatible Mercari JP
search client so it can recommend the top three visible options for a
natural-language shopping request without requiring Mercari JP account
access.

## Decisions

- The plugin is a `Category.TOOL` plugin that registers a v1 tool
  named `mercari_jp_search`.
- The search source follows the request shape implemented by
  `take-kun/mercapi` for Mercari JP web search.
- HTTP 403 and blocked upstream responses are terminal rejected results;
  the plugin does not bypass them.
- Search output is structured JSON containing source metadata,
  candidates, score reasons, and warnings.
- Candidate ranking is deterministic and happens before the LLM writes
  user-facing recommendations.
- The search surface accepts optional native Mercari narrowing fields
  (`category_ids`, `brand_ids`, `item_condition`, `shipping_payer`) and
  maps them onto the Mercapi JSON payload. Unset fields preserve the
  pre-#191 payload shape so no existing caller regresses.

## Boundaries

### Allowed Changes
- src/yaya/plugins/mercari_jp/
- tests/plugins/mercari_jp/
- tests/bdd/features/plugin-mercari_jp.feature
- tests/bdd/test_plugins.py
- specs/plugin-mercari_jp.spec
- pyproject.toml
- src/yaya/plugins/AGENT.md
- scripts/check_coverage.py
- tests/scripts/test_check_coverage.py

### Forbidden
- src/yaya/kernel/
- src/yaya/cli/
- src/yaya/core/
- CAPTCHA, proxy rotation, account login, purchase, cart, or checkout automation
- docs/dev/plugin-protocol.md
- GOAL.md

## Completion Criteria

Scenario: Search returns structured candidates from Mercapi search
  Test:
    Package: yaya
    Filter: tests/plugins/mercari_jp/test_plugin.py::test_search_returns_structured_candidates_from_mercapi_response
  Level: unit
  Given Mercapi returns a Mercari search response with visible product candidates
  When the mercari_jp_search tool runs with a keyword and price ceiling
  Then it returns normalized candidates with source metadata, prices, URLs, and score reasons

Scenario: Rejected Mercapi responses are not bypassed
  Test:
    Package: yaya
    Filter: tests/plugins/mercari_jp/test_plugin.py::test_search_rejects_forbidden_mercapi_response_without_bypass
  Level: unit
  Given Mercapi returns HTTP 403 for a Mercari search request
  When the mercari_jp_search tool runs
  Then it returns a rejected tool error explaining that the source refused the request

Scenario: Empty Mercapi results stay successful and explain search drift
  Test:
    Package: yaya
    Filter: tests/plugins/mercari_jp/test_plugin.py::test_search_returns_empty_candidates_with_warning
  Level: unit
  Given Mercapi returns a Mercari search response with no product candidates
  When the mercari_jp_search tool runs
  Then it returns an empty candidate list with warnings containing Mercari coverage and Japanese keyword guidance

Scenario: Filter fields map onto the Mercapi payload
  Test:
    Package: yaya
    Filter: tests/plugins/mercari_jp/test_plugin.py::test_filter_fields_land_on_mercapi_payload
  Level: unit
  Given a search request with category, brand, item_condition, and shipping_payer filters set
  When the Mercapi payload is built
  Then the payload carries the expected category, brand, condition, and shipping-payer IDs

## Out of Scope

- Authenticated Mercari sessions.
- Purchase execution or cart mutation.
- Native Mercari JP result completeness guarantees.
