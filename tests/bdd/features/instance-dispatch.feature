Feature: Instance-scoped LLM provider dispatch

  The executable Gherkin mirror of specs/instance-dispatch.spec.

  Scenario: AC-01 llm_openai builds one client per owned instance on load
    Given a ConfigStore with two providers.<id> rows whose plugin meta equals llm-openai
    When the plugin on_load runs against a KernelContext bound to that store
    Then self._clients contains one AsyncOpenAI per instance id and unrelated instances are skipped

  Scenario: AC-02 llm_openai dispatch filters by instance id against the owned-client dict
    Given an llm_openai plugin holding a stub client under instance id prod
    When a llm.call.request payload provider equals an unowned id
    Then the plugin emits no llm.call.response and no llm.call.error

  Scenario: AC-03 llm_echo answers only for owned instance ids
    Given a llm_echo plugin with active instance set containing llm-echo
    When a llm.call.request for a non-owned provider id is published
    Then no llm.call.response event is emitted by the llm-echo plugin

  Scenario: AC-04 strategy_react resolves model from the active instance config
    Given a ConfigStore with provider set to instance-a whose config model field equals gpt-4.1
    When strategy_react handles a strategy.decide.request
    Then the emitted strategy.decide.response carries provider instance-a and model gpt-4.1

  Scenario: AC-05 llm_openai rebuilds only the edited instance on providers.<id> config.updated
    Given an llm_openai plugin with two seeded instances prod and azure
    When providers.prod.base_url is updated and config.updated is delivered
    Then only the prod client is rebuilt and the azure client instance remains the same object
