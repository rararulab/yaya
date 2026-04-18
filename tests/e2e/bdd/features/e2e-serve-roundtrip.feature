Feature: E2E serve roundtrip

  These scenarios mirror specs/e2e-serve-roundtrip.spec and prove an
  installed yaya wheel can boot the kernel, expose the web adapter,
  answer through the echo provider, and shut down cleanly.

  Scenario: AC-01 echo round-trip lands an assistant done frame within five seconds
    Given yaya serve is running as a subprocess on an ephemeral port with OPENAI_API_KEY unset
    When the test opens a WebSocket to /ws and sends a user.message with text hi
    Then an assistant.done frame arrives within five seconds and its content starts with (echo)

  Scenario: AC-02 the kernel exits cleanly on SIGINT with no traceback on stderr
    Given yaya serve is running as a subprocess
    When the test sends the platform shutdown signal and the process exits
    Then the captured stderr contains no Traceback header and no token unhandled
