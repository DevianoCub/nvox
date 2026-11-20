# Release 0.1.0

> **Status:** release checklist for the first tagged version.

Nvox 0.1.0 is a source and wheel release built by
`.github/workflows/release.yml`. Pushing a `v*` tag whose version matches
`pyproject.toml` triggers verification, tests, docs, distribution builds,
checksums, keyless Sigstore signatures and a GitHub release.

## Checklist

- [x] `pyproject.toml` version is `0.1.0` and `uv.lock` agrees
- [x] M7 hardening is implemented: archive ratio guards and malformed-input
      fuzz corpus
- [x] Optional process isolation is documented and tested
- [x] Performance benchmark is recorded and the accelerator decision is made
- [x] Python and command plugin templates, conformance tests and migration notes
      are present
- [x] English docs are reviewed; Russian translation is in `docs/ru/`
- [x] CI covers Linux and macOS; docs build is strict
- [x] Release workflow builds, smoke-tests, checksums and signs artifacts
- [x] Final local gate: `pytest`, `ruff`, `ruff format --check`, `mypy`,
      `mkdocs build --strict`, fuzz corpus and `git diff --check`
- [ ] Create and push the `v0.1.0` tag only after the local gate is green

The tag item is completed by the release operator after the repository is
committed and pushed; it is not performed by the coding session.

## Local gate

```bash
uv sync --group dev --extra docs
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
uv run mkdocs build --strict
uv run python scripts/fuzz-parsers.py --iterations 50
git diff --check
```

## Tag and publish

```bash
git tag -a v0.1.0 -m "Nvox 0.1.0"
git push origin v0.1.0
```

The release workflow then:

1. verifies that the tag matches `pyproject.toml`;
2. runs lint, format, types, tests and the strict docs build;
3. builds the sdist and wheel with `uv build`;
4. installs the wheel in a clean venv and runs `nvox --version` and
   `nvox doctor --json`;
5. writes `dist/SHA256SUMS`;
6. signs every distribution file and the checksum file with keyless
   `cosign sign-blob`, producing `.sigstore.json` bundles;
7. publishes the files and signatures to the GitHub release.

Verification example:

```bash
sha256sum -c SHA256SUMS
cosign verify-blob \
  --bundle nvox-0.1.0-py3-none-any.whl.sigstore.json \
  --certificate-identity-regexp 'https://github.com/.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  nvox-0.1.0-py3-none-any.whl
```

## Compatibility

- Python 3.12, 3.13 and 3.14 on Linux and macOS are tested in CI.
- Runtime dependencies remain `typer` and `rich`; image and TUI extras are
  optional.
- External tools remain optional and missing tools produce skips.
- Plugin `API_VERSION` stays `1`.
