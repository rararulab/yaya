Feature: Post-install yaya CLI harness

  These scenarios prove the repository actually exercises the installed
  yaya CLI end-to-end after a wheel install: version reports non-empty,
  --json version emits the canonical envelope, and unknown subcommands
  exit non-zero. They are the minimum executable target that keeps the
  agent-spec harness honest — remove this file and the harness reverts
  to a documentation promise.

  Scenarios mirror specs/harness-agent-spec.spec Completion Criteria
  and are kept in sync by scripts/check_feature_sync.py.

  Scenario: yaya version exits zero after wheel install
    Given the wheel was installed into a fresh venv
    When the user runs `yaya version`
    Then the process exits 0 with a non-empty stdout

  Scenario: yaya --json version emits the canonical shape
    Given the installed yaya
    When the user runs `yaya --json version`
    Then stdout is a JSON object with ok=true, action="version", and a string version field

  Scenario: unknown command exits non-zero
    Given the installed yaya
    When the user runs an unrecognized subcommand
    Then the process exits with a non-zero code
