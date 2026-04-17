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
