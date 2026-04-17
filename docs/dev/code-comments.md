# Code Comments & Docstrings

yaya follows the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html)
for Python code, with the overlay rules from
`rararulab/.github/docs/code-comments.md`. This page captures the rules an
agent must follow before committing code.

## Authoritative references

- [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html) — the style
  ([§3.8 Comments and Docstrings](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings) is non-negotiable).
- Org rule: `rararulab/.github/docs/code-comments.md`.
- [PEP 257](https://peps.python.org/pep-0257/) — docstring conventions baseline.

## Non-negotiables

- **Every module, public class, public function, and public method has a
  docstring.** "Public" = top-level name without a leading underscore.
  Enforced by ruff's `D` (pydocstyle) rules.
- **Docstrings explain _why_, not _what_.** Signature and type hints say
  what; the docstring says why the function exists, what invariant it
  preserves, and when *not* to call it.
- **Google-style sections**: `Args:`, `Returns:`, `Raises:`, `Yields:`,
  `Example:`. Do not mix in NumPy or reST styles.
- **English only** — docstrings, inline comments, commit messages, string
  literals.
- **Comment what you touch.** Do NOT retroactively add comments to
  unchanged code — drive-by comment PRs pollute blame. If a line needs a
  comment, the person editing it writes one.

## Docstring template (Google style)

```python
def atomic_swap(src: Path, dst: Path) -> None:
    """Move ``src`` onto ``dst`` atomically on POSIX filesystems.

    Self-update must never leave a half-written binary in place — a crashed
    write bricks ``yaya`` on the user's PATH. ``os.replace`` gives an atomic
    rename on the same filesystem, so we stage ``src`` next to ``dst``.

    Args:
        src: Staged binary in a temp dir on the **same** filesystem as ``dst``.
        dst: Installed path to overwrite (e.g. ``/usr/local/bin/yaya``).

    Raises:
        OSError: If ``src`` and ``dst`` live on different filesystems.
    """
```

Short helpers get a one-line summary; omit `Args:` when the signature is
self-evident (Google style allows this for obvious cases).

## Module docstrings

Every module under `src/yaya/` opens with a docstring that answers: what
does this module own, and where does it sit in the layering? Example:

```python
"""Self-update core: network + filesystem logic with no CLI dependencies.

Returns structured results. Presentation (text vs JSON, colors, progress)
lives in the CLI layer (``yaya.cli.commands.update``).
"""
```

## Inline comments — when and how

- Explain **non-obvious invariants**, hidden constraints, subtle ordering.
  Never the next line's mechanics.
- Prefix with the reason: `# Race:`, `# Perf:`, `# Compat:`,
  `# Security:`, `# Workaround(<upstream-url>):`.
- TODOs MUST reference an issue: `# TODO(#123): ...`. A TODO without a
  number fails review.

## Google style overlays that matter in yaya

- **Imports**: stdlib, third-party, first-party, each block separated;
  absolute imports only; ruff's `I` rules enforce.
- **Naming**: `snake_case` for functions/variables, `PascalCase` for
  classes, `UPPER_SNAKE_CASE` for module constants. Single-underscore
  prefix for intentionally non-public names.
- **Line length**: 120 (yaya override of Google's 80 — matches
  `pyproject.toml`). Prefer breaking long lines at logical boundaries.
- **Default arguments**: never mutable. Use `None` + `if x is None: x = []`.
- **Exceptions**: raise specific types, not `Exception`. Include actionable
  context (`raise ValueError(f"unsupported target: {target!r}")`).
- **Type hints**: required on all public signatures. `mypy --strict`
  rejects missing or `Any`-returning annotations.

## Agent-authored code — extra rules

- **Every function the agent writes has a Google-style docstring.** No
  exceptions for "obvious" utilities.
- **Prompts** (strings sent to an LLM) live in `specs/` or dedicated
  `prompts/` modules. Document the intent, variables, and expected output
  on the factory that loads them; do not embed long prose inside function
  bodies.
- **Public API changes** include a CHANGELOG entry in the same PR
  (release-please parses it). CHANGELOG entries are user-facing prose;
  docstrings are developer-facing.
