Feature: Mercari Japan search plugin

  The executable Gherkin mirror of specs/plugin-mercari_jp.spec.

  Scenario: Search returns structured candidates from Mercapi search
    Given Mercapi returns a Mercari search response with visible product candidates
    When the mercari_jp_search tool runs with a keyword and price ceiling
    Then it returns normalized candidates with source metadata, prices, URLs, and score reasons

  Scenario: Rejected Mercapi responses are not bypassed
    Given Mercapi returns HTTP 403 for a Mercari search request
    When the mercari_jp_search tool runs
    Then it returns a rejected tool error explaining that the source refused the request

  Scenario: Empty Mercapi results stay successful and explain search drift
    Given Mercapi returns a Mercari search response with no product candidates
    When the mercari_jp_search tool runs
    Then it returns an empty candidate list with warnings containing Mercari coverage and Japanese keyword guidance

  Scenario: Filter fields map onto the Mercapi payload
    Given a search request with category, brand, item_condition, and shipping_payer filters set
    When the Mercapi payload is built
    Then the payload carries the expected category, brand, condition, and shipping-payer IDs
