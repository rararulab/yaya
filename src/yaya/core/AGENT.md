# src/yaya/core — Agent Guidelines

<!-- Prompt-system layers. Philosophy / Style / Anti-sycophancy inherit root. -->

## Philosophy
Domain logic. Agents, flows, updater, pure computation. Zero CLI dependencies.
Behavior is contract-first: every non-trivial module ships with a
`specs/<slug>.spec` whose BDD scenarios bind to the tests in `tests/core/`.

## External Reality
- `tests/core/` is ground truth — unit tests per module, no network/disk/time without a seam.
- `specs/*.spec` define observable behavior; `agent-spec lifecycle` locally and `agent-spec guard` in CI verify compliance.
- `mypy --strict` + ruff import rules enforce the "no `yaya.cli.*` import" boundary.
- Google Python Style Guide governs docstrings, naming, and layout.

## Constraints
- `updater.py` — self-update: version resolution, asset download, checksum, atomic swap. Returns `UpdateStatus` dataclass; **no printing**.
- New feature modules require a `specs/<slug>.spec` contract before the PR can merge (see [`../../../docs/dev/agent-spec.md`](../../../docs/dev/agent-spec.md)).
- All I/O (network, disk) is behind an injectable seam (function arg or module attr) so tests can substitute. `tests/conftest.py` monkeypatches `STATE_DIR` — follow the same pattern for new I/O.
- Functions return structured results (dataclasses, enums) — never print, never `sys.exit`.
- No module-level side effects at import time.
- Public functions and classes carry Google-style docstrings (`Args:`, `Returns:`, `Raises:`).

## Interaction (patterns)
- Do NOT import `yaya.cli.*` — violates layering; `just check` fails.
- Do NOT hard-code config defaults in Python — use env + config file.
- Do NOT swallow exceptions — propagate or wrap with context.
- New public function/class ⇒ Google-style docstring explaining **why** + a test bound to a BDD scenario in the relevant `.spec`.
- Changing observable behavior ⇒ update the `.spec` in the same commit; `agent-spec guard` will otherwise flag the PR.

## Budget & Loading
- Layering: [../../../docs/dev/architecture.md](../../../docs/dev/architecture.md).
- Contract workflow (`plan` / `lifecycle` / `guard` / `explain`): [../../../docs/dev/agent-spec.md](../../../docs/dev/agent-spec.md).
- Docstring + comment rules: [../../../docs/dev/code-comments.md](../../../docs/dev/code-comments.md).
