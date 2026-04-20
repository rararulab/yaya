Feature: Web adapter HTTP CRUD for LLM provider instances

  The bundled web adapter's /api/llm-providers surface operates on
  provider instances in the providers.<id>.* namespace. This feature
  mirrors specs/plugin-web-instance-crud.spec Completion Criteria and
  is kept in sync by scripts/check_feature_sync.py.

  Scenario: Listing instances returns bare array with active flag
    Given a config store seeded with two llm-openai instances and one llm-echo instance with provider set to openai-gpt4
    When a client issues GET api llm providers
    Then the response is a bare array with id plugin label active and config per row and openai-gpt4 is the only active row

  Scenario: Listing instances masks secrets by default
    Given a config store seeded with an instance whose config carries an api_key field
    When a client issues GET api llm providers without the show flag
    Then the api_key value is masked in the response

  Scenario: Show flag reveals secrets on list
    Given a config store seeded with an instance whose config carries an api_key field
    When a client issues GET api llm providers with show equal to 1
    Then the api_key value is returned unmasked

  Scenario: Creating an instance materialises providers keys and returns 201
    Given an admin router wired to a stub registry with llm-openai loaded
    When a client POSTs api llm providers with id plugin label and config
    Then the response is 201 and providers id plugin providers id label and providers id config fields are written
    And the returned row echoes the supplied values

  Scenario: Creating an instance with a dotted id is rejected
    Given an admin router wired to a stub registry
    When a client POSTs api llm providers with an id containing a dot
    Then the response is 400 with a message explaining the id must not contain dots

  Scenario: Creating an instance with a duplicate id returns 409
    Given a config store seeded with an existing llm-openai instance
    When a client POSTs api llm providers with the same id
    Then the response is 409

  Scenario: Creating an instance with an unknown plugin returns 400
    Given an admin router wired to a registry without llm-missing loaded
    When a client POSTs api llm providers referencing a non-llm-provider plugin
    Then the response is 400

  Scenario: Creating an instance without id auto-generates one
    Given an admin router wired to a stub registry with llm-openai loaded
    When a client POSTs api llm providers omitting id
    Then the returned row carries an id of the form plugin dash uuid8 and the label falls back to plugin id

  Scenario: Patching an instance merges label and config partially
    Given a config store seeded with an instance that has an existing api_key
    When a client PATCHes api llm providers id with a new label and a new model
    Then the label and model are updated and the api_key field survives untouched

  Scenario: Patching on an unknown instance id returns 404
    Given an admin router wired to a stub registry
    When a client PATCHes api llm providers ghost with a label
    Then the response is 404

  Scenario: Deleting an instance clears its providers keys
    Given a config store seeded with two llm-openai instances
    When a client DELETEs api llm providers openai-gpt4
    Then the response is 204 and providers openai-gpt4 plugin and providers openai-gpt4 api_key are removed

  Scenario: Deleting the active instance is blocked with 409
    Given a config store where provider equals openai-gpt4 and that instance exists
    When a client DELETEs api llm providers openai-gpt4
    Then the response is 409 with a message about switching the active provider

  Scenario: Deleting the last instance of a plugin is blocked with 409
    Given a config store where llm-echo has exactly one instance
    When a client DELETEs that instance
    Then the response is 409 with a message about being the only instance

  Scenario: Switching active to an unknown instance id returns 404
    Given a config store seeded with known instances
    When a client PATCHes api llm providers active with an unknown id
    Then the response is 404

  Scenario: Switching active to an instance whose plugin is not loaded returns 400
    Given a config store seeded with an instance whose plugin is not loaded
    When a client PATCHes api llm providers active with that id
    Then the response is 400

  Scenario: Test endpoint routes on a bridge session
    Given an admin router wired to a bus whose stub provider echoes llm call response for the instance id
    When a client POSTs api llm providers id test
    Then the llm call request carries a bridge web-api-test session id and the response is ok true with latency_ms
