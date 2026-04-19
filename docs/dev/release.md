# Release

Fully automated via [release-please](https://github.com/googleapis/release-please).
A single workflow (`.github/workflows/release-please.yml`) owns the end-to-end flow:

1. Land commits on `main` using [Conventional Commits](https://www.conventionalcommits.org/)
   (`feat:`, `fix:`, `feat!:` for breaking, …).
2. release-please opens/updates a `chore(main): release X.Y.Z` PR that bumps
   `pyproject.toml`, updates `CHANGELOG.md`, and advances
   `.release-please-manifest.json`.
3. Merging that PR creates the git tag and the GitHub Release in the same
   workflow run.
4. Downstream jobs in the same run build PyInstaller binaries for 4 targets,
   build the wheel + sdist, generate SHA256 sums, and upload everything as
   release assets.

No manual tagging or separate tag-triggered workflow is needed.

## Post-install smoke

Unit + integration tests run against the source tree under `uv sync`, so
they never see release regressions that only show up after install: missing
entry-points in `pyproject.toml`, wrong package layout, sdist `MANIFEST`
gaps, or a wheel built without the web adapter's bundled Vite assets
(`src/yaya/plugins/web/static/`).

The post-install smoke (`tests/e2e/`) closes that gap. Every PR's CI runs
the matrix `E2E smoke (ubuntu/macos/windows)` job which:

1. Downloads the built wheel + sdist from the `Build wheel + sdist` job.
2. Creates a fresh venv (no project source on `PYTHONPATH`).
3. `pip install dist/*.whl` into the venv.
4. Runs `pytest tests/e2e -v`.
5. On Ubuntu only, repeats steps 2–4 for `dist/*.tar.gz` (sdist parity).

What the smoke covers:

- `yaya version` / `yaya --json version` — entry-point wiring, version
  string shape.
- `yaya hello` / `yaya --json hello` — kernel bus boot round-trip.
- `yaya --json plugin list` — asserts every bundled plugin in the 0.1
  catalog loads with the declared category
  (`tests/e2e/test_plugin_list_smoke.py`).
- `yaya --help` — every kernel subcommand advertised.
- `tests/e2e/test_broken_binary_gate.py` — AC-03 self-test that proves
  the gate actually fails on a broken binary.

### Reproducing the smoke locally

Requires `uv` installed (provides `uvx`).

```bash
# Wheel + sdist, two fresh venvs:
scripts/post_install_smoke.sh

# Wheel only:
scripts/post_install_smoke.sh wheel

# Against the PyInstaller onefile binary:
just build-bin
YAYA_BIN=$(pwd)/dist/yaya pytest tests/e2e -v
```

The `tests/e2e/test_binary_smoke.py` module skips automatically unless
`YAYA_BIN` points at an executable, so the standard `pytest tests/e2e`
invocation stays focused on the installed wheel.
