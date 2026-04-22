spec: task
name: "e2e-post-install-smoke"
tags: [e2e, harness, release, ci]
---

## Intent

Product-level proof that the built artifact — wheel, sdist, or
PyInstaller binary — actually works end-to-end after install.
Existing CI runs `just test` against the source tree via `uv sync`,
which hides whole classes of release regressions that only appear
post-install:

- missing entry-points in `pyproject.toml` (surfaces as `yaya:
  command not found`);
- wrong package layout (surfaces as `ImportError` in site-packages);
- sdist `MANIFEST` / `only-include` omissions (source builds lose
  bundled web assets or entry-point metadata);
- a bundled plugin entry-point not registered (surfaces as an empty
  `plugin list`);
- a wheel built without the web adapter's pre-built Vite bundle
  under `src/yaya/plugins/web/static/` (surfaces as a 404 when
  `yaya serve` tries to mount the static dir — caught here by
  asserting the `web` adapter plugin is `loaded` post-install).

The smoke installs the built artifact into a fresh venv with no
project source on `PYTHONPATH`, then drives the minimal CLI surface
(`version`, `doctor`, `plugin list`) through both text and `--json`
output. A broken-binary negative scenario proves the gate actually
fails a merge when the CLI regresses.

## Decisions

- Test files live under `tests/e2e/` alongside the existing
  `test_cli_smoke.py`. The new files are:
  - `tests/e2e/test_plugin_list_smoke.py` — asserts the bundled
    plugin catalog is present post-install, with every expected
    name and category listed.
  - `tests/e2e/test_binary_smoke.py` — runs the same CLI surface
    when `YAYA_BIN` points at a PyInstaller binary. Skipped when
    `YAYA_BIN` is unset.
  - `tests/e2e/test_broken_binary_gate.py` — an AC-03 self-test
    that scripts a deliberately-broken stand-in for `yaya`, drives
    the same helpers through it, and asserts the assertions fail.
    Proves the smoke is not a rubber-stamp.
- The existing `YAYA_BIN` convention is reused unchanged: when
  set, tests run against that binary; otherwise they resolve
  `shutil.which("yaya")`. The broken-gate test uses its own
  temporary executable and never leaks env state to siblings.
- Expected bundled plugin set is asserted as a superset of the 0.1
  catalog: `agent-tool`, `llm-echo`, `llm-openai`, `mcp-bridge`,
  `memory-sqlite`, `strategy-react`, `tool-bash`, `web`. Extra
  plugins are allowed so future bundling does not break the gate.
- A local reproduction script lives at
  `scripts/post_install_smoke.sh`. It builds wheel + sdist, creates
  two fresh venvs, installs each artifact, and runs `pytest
  tests/e2e -v` against each. Devs can debug wheel/sdist parity
  locally without pushing to CI.
- CI wiring reuses the existing `E2E smoke` matrix job
  (`.github/workflows/main.yml`). The job already installs
  `dist/*.whl` and runs `pytest tests/e2e`. We add one extra step
  (ubuntu only) that installs the sdist into a second fresh venv
  and re-runs the same pytest command — the cheapest guard against
  `MANIFEST` regressions without tripling the matrix.
- We deliberately do NOT add binary matrices to the PR CI. The
  PyInstaller binary is only built on release via
  `release-please.yml`; that workflow already runs a minimal
  `yaya version` smoke on the just-built binary. This issue
  extends the binary smoke **in code**, so when release-please
  runs it against the built binary the same assertions kick in —
  but we do not rebuild the four-target matrix on every PR.
- Coverage floor is unaffected: the new tests are marked
  `integration` and excluded from the unit suite via
  `--ignore=tests/e2e` in `pyproject.toml`.

## Boundaries

### Allowed Changes
- tests/e2e/test_plugin_list_smoke.py
- tests/e2e/test_binary_smoke.py
- tests/e2e/test_broken_binary_gate.py
- tests/e2e/conftest.py
- scripts/post_install_smoke.sh
- specs/e2e-post-install-smoke.spec
- tests/bdd/features/e2e-post-install-smoke.feature
- .github/workflows/main.yml
- justfile
- docs/dev/release.md
- tests/AGENT.md

### Forbidden
- src/yaya/kernel/
- src/yaya/cli/
- src/yaya/core/
- src/yaya/plugins/
- pyproject.toml
- GOAL.md
- AGENT.md
- docs/dev/plugin-protocol.md

## Completion Criteria

Scenario: AC-01 bundled plugins list post-install
  Test:
    Package: yaya
    Filter: tests/e2e/test_plugin_list_smoke.py::test_plugin_list_includes_bundled_plugins
  Level: e2e
  Given the wheel has been installed into a fresh venv
  When the test runs yaya --json plugin list
  Then every bundled plugin name appears with status loaded and its declared category

Scenario: AC-02 binary smoke honours YAYA_BIN when set
  Test:
    Package: yaya
    Filter: tests/e2e/test_binary_smoke.py::test_binary_runs_core_cli_endpoints
  Level: e2e
  Given YAYA_BIN points at a working yaya executable
  When the test runs version doctor and plugin list through that binary
  Then all four invocations exit zero with the expected JSON shapes

Scenario: AC-03 broken binary fails the gate
  Test:
    Package: yaya
    Filter: tests/e2e/test_broken_binary_gate.py::test_broken_binary_fails_assertions
  Level: e2e
  Given a stand-in executable that always exits one
  When the smoke helpers drive it through version and plugin list
  Then the helpers raise AssertionError proving the gate blocks the merge

## Out of Scope

- PyInstaller binary matrix on every PR — release-please already
  builds the four targets on tag and runs a binary smoke there.
- Browser / WebSocket round-trips — covered by
  `specs/e2e-serve-roundtrip.spec`.
- Plugin install / remove round-trip against a real PyPI package —
  waits on the plugin registry epic.
- Signed plugin / sandbox checks — deferred to 2.0.
