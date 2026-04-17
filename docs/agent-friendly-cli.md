# Agent-Friendly CLI Design

Design CLIs that AI agents can operate reliably. These principles apply to all rararulab CLI tools.

## 1. Non-Interactive First

Agents cannot handle dynamic prompts. Every parameter must be passable via flags.

```bash
# Bad: interactive prompt
? Which environment? (use arrow keys)

# Good: flag-driven
my-cli deploy --env staging
```

**Rule:** If a command works interactively, it MUST also work with `--flags` only. No `stdin` prompts without a flag equivalent.

## 2. Lazy-Loaded Documentation

Agents discover commands incrementally. Don't dump all help at once.

```bash
# Good: top-level shows subcommands only
my-cli --help

# Good: detail per subcommand
my-cli deploy --help
```

**Rule:** `--help` at each level shows only that level's options.

## 3. Example-Driven Help

Agents pattern-match better than they parse prose.

```bash
# Good: copy-paste-ready examples in help text
EXAMPLES:
    my-cli deploy --env staging --service api
    my-cli deploy --env prod --service api --dry-run
```

**Rule:** Every subcommand's `--help` includes at least one complete, runnable example.

## 4. Pipe-Friendly (JSON stdout, logs stderr)

Agents chain tools together. Structured output enables downstream parsing.

```bash
# stdout: machine-readable JSON
{"ok": true, "deploy_id": "d-123", "url": "https://..."}

# stderr: human-readable logs
Deploying api to staging...
```

**Rule:** JSON on stdout, human text on stderr. Never mix formats on the same stream.

### Standard JSON Response Shape

Success:
```json
{"ok": true, "action": "deploy", "deploy_id": "d-123", "url": "https://..."}
```

Error:
```json
{"ok": false, "error": "service 'foo' not found", "suggestion": "run 'my-cli list' to see available services"}
```

The `suggestion` field gives agents a concrete next step when something fails.

## 5. Fail Fast with Actionable Errors

Agents can self-correct, but need clear signals.

```bash
# Bad: hangs silently or gives vague error
Error: something went wrong

# Good: immediate exit + what to do
{"ok": false, "error": "missing --env flag", "suggestion": "add --env staging or --env prod"}
```

**Rule:** Non-zero exit code + JSON error with `suggestion` field. Never hang, never swallow errors.

## 6. Idempotent Operations

Agents retry frequently. Repeated calls must not create duplicate side effects.

```bash
# First call
{"ok": true, "created": true, "name": "my-service"}

# Second call (same args)
{"ok": true, "created": false, "name": "my-service", "reason": "already exists"}
```

**Rule:** `create` operations return success (exit 0) if resource already exists. Use `created: true/false` to distinguish.

## 7. Dry-Run Mode

Agents should verify plans before executing. This reduces blast radius.

```bash
my-cli deploy --env prod --dry-run
{"ok": true, "dry_run": true, "would_deploy": "api@v1.2.3", "target": "prod"}
```

**Rule:** Destructive or stateful commands MUST support `--dry-run`. Output shows what would happen without doing it.

## 8. Force/Confirm Bypass

Keep safety nets for humans, but let agents automate.

```bash
# Human: gets confirmation prompt
my-cli delete --service api

# Agent: bypasses prompt
my-cli delete --service api --yes
```

**Rule:** Interactive confirmations must have a `--yes` or `--force` flag to bypass.

## 9. Predictable Command Structure

Consistent naming reduces agent learning cost.

```
resource verb [args] [flags]

my-cli service list
my-cli service create my-service --description "..."
my-cli service deploy my-service --env staging
my-cli service delete my-service --yes
```

**Rule:** Follow `<noun> <verb>` pattern. Same verb means same behavior across resources.

## 10. Structured Success Feedback

Agents need parseable results for downstream decisions.

```bash
# Bad: celebration text
🎉 Deployed successfully!

# Good: machine-readable fields
{"ok": true, "action": "deploy", "deploy_id": "d-123", "duration_ms": 4521, "url": "https://..."}
```

**Rule:** Success output includes all fields an agent needs for the next step. No emoji, no decoration on stdout.

---

## rararulab Implementation Checklist

When building a CLI in rararulab:

- [ ] All commands work without interactive prompts (flags only)
- [ ] JSON stdout, human logs stderr
- [ ] Error responses include `suggestion` field
- [ ] Create operations are idempotent (exit 0 if exists)
- [ ] Destructive commands support `--dry-run`
- [ ] Interactive confirmations have `--yes` bypass
- [ ] Commands follow `noun verb` or `verb noun` pattern consistently
- [ ] `--help` includes runnable examples per subcommand
- [ ] Non-zero exit code on failure
- [ ] Success output includes all fields for downstream use

## Rust Implementation Pattern

Using clap + serde_json + snafu (per `rust-style.md`):

```rust
// JSON output helper
fn json_ok(value: &impl serde::Serialize) -> ExitCode {
    println!("{}", serde_json::to_string_pretty(value).expect("serialize"));
    ExitCode::SUCCESS
}

fn json_err(error: &str, suggestion: &str) -> ExitCode {
    println!("{}", serde_json::json!({
        "ok": false,
        "error": error,
        "suggestion": suggestion,
    }));
    ExitCode::FAILURE
}
```
