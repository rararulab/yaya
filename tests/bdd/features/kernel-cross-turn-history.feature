Feature: Kernel agent loop carries cross-turn conversation history

  The kernel agent loop hydrates each turn with prior conversation
  history from the session tape. Without this, the LLM sees only the
  current user message on every turn — an obvious blocker for any
  multi-turn dialogue (and for the web sidebar's click-to-resume flow).

  Scenarios mirror specs/kernel-cross-turn-history.spec Completion
  Criteria and are kept in sync by scripts/check_feature_sync.py.

  Scenario: Prior user and assistant messages are loaded from the tape into the next turn
    Given a session tape with one completed user/assistant exchange
    And an AgentLoop wired to the session store for that workspace
    When a second user.message.received event is published on the same session
    Then the strategy.decide.request carries the prior user message, the prior assistant reply, and the new user message in order

  Scenario: The most recent compaction anchor elides pre-anchor history
    Given a session tape with two pre-compaction messages followed by a compaction anchor and one post-anchor message
    And an AgentLoop wired to the session store for that workspace
    When a new user.message.received event arrives on the same session
    Then the strategy.decide.request omits the pre-anchor messages and starts with the compaction summary as a system message

  Scenario: Loops without a session store preserve the 0.1 single-message fallback
    Given an AgentLoop constructed without a session store
    When a user.message.received event arrives
    Then the strategy.decide.request carries only the incoming user message
