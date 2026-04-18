Feature: CLI kernel commands

  These scenarios mirror specs/cli-kernel-commands.spec and keep the
  minimal built-in CLI surface executable: hello, serve, and plugin
  management all stay wired to the kernel contracts they advertise.

  Scenario: yaya hello under --json returns ok=true with received=true
    Given a fresh kernel with no LLM configured
    When `yaya --json hello` is invoked
    Then the command exits 0 and stdout carries `{"ok": true, "action": "hello", "received": true}`

  Scenario: Error path — yaya serve rejects --host
    Given the serve command registered on the CLI app
    When the user invokes `yaya serve --host 0.0.0.0`
    Then Typer exits with code 2 because no such flag is defined

  Scenario: yaya plugin list under --json returns a valid plugins array
    Given the four bundled seed plugins installed via entry points
    When `yaya --json plugin list` is invoked
    Then stdout carries `{"ok": true, "action": "plugin.list", "plugins": [...]}` listing every bundled plugin

  Scenario: Error path — yaya plugin remove of a bundled plugin emits ok=false with suggestion
    Given a bundled plugin named strategy-react
    When `yaya --json plugin remove strategy-react --yes` is invoked
    Then the command exits 1 and stdout carries `{"ok": false, "error": "...bundled...", "suggestion": "...bundled..."}`

  Scenario: Error path — yaya plugin install rejects unsupported scheme
    Given a source string with an unsupported URL scheme like `git+ssh`
    When `yaya --json plugin install "git+ssh://example.com/foo.git" --yes` is invoked
    Then `validate_install_source` raises ValueError before any subprocess is spawned
    And the CLI surfaces ok=false with a suggestion

  Scenario: yaya serve shuts down cleanly when the shutdown event is set
    Given `run_serve` is driven via its test-only shutdown_event hook
    When the event is set after boot
    Then stdout carries `{"ok": true, "action": "shutdown", "reason": "signal"}` and the task returns exit code 0

  Scenario: yaya serve warns when no web adapter plugin is loaded
    Given the kernel boots without any plugin whose category is adapter and name starts with web
    When `run_serve` starts up
    Then a human-facing warning whose text contains "no web adapter" lands on stderr and the kernel continues running

  Scenario: yaya plugin install under --json requires --yes
    Given the user passes --json without --yes
    When `yaya --json plugin install some-pkg` is invoked
    Then the command exits 1 with ok=false and error=confirmation_required
