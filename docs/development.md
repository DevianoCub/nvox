# Development

## Setup

```bash
uv sync --group dev
uv run nvox doctor
```

Optional extras:

```bash
uv sync --group dev --extra image --extra tui
```

## Project layout

```
Nvox/
  pyproject.toml          # package metadata, deps, tool config
  nvox.toml.example       # annotated configuration
  src/nvox/
    __init__.py           # __version__ from package metadata
    __main__.py           # python -m nvox
    api.py                # stable plugin API (API_VERSION)
    cli.py                # Typer commands
    core/
      config.py           # nvox.toml discovery/validation
      evidence.py         # read-only evidence snapshot + MIME sniffing
      store.py            # content-addressed artifact store
      registry.py         # plugin discovery and validation
      engine.py           # queue, limits, dedup, hooks dispatch
      hooks.py            # hook dispatch
      command_plugin.py   # declarative TOML command plugins
      surfaces.py         # ASCII/UTF-16 run extraction
      flags.py            # flag formats, raw regex, surface scanner
      findings.py         # findings, confidence, provenance chains
      tools.py            # external tool registry + detection
      doctor.py           # environment diagnostics
    plugins/              # built-in plugin packs
      generic/strings.py  # reference plugin
      decoders/           # base encodings, compression, XOR brute force
      image/              # minimal EXIF and PNG LSB (deep analysis in M3)
    tui/                  # Textual UI (M4)
  tests/
  docs/
    ru/                   # reserved for the Russian translation
```

New core modules belong in `src/nvox/core/`; new techniques belong in
`src/nvox/plugins/<pack>/`. Core modules must not import plugin packs.

## Checks

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

The same commands gate every milestone. A milestone is complete only when all
four pass and its acceptance criteria in the [roadmap](roadmap.md) are met.

`uv run ruff format .` and `uv run ruff format --check .` are the canonical
formatter commands; do not hand-format around them.

## Conventions

- Python 3.12+ syntax; `from __future__ import annotations` in every module.
- Full type annotations, including test functions.
- Public functions and classes have docstrings; internal helpers may be concise.
- User-facing text and documentation are English; `docs/ru/` is filled later.
- No network calls in plugins or core by default.
- Errors that a user can fix (bad config, missing file) are reported as clean
  messages with exit code 1, not tracebacks.

## Adding a built-in plugin

1. Pick the pack (`image`, `audio`, `generic`).
2. Subclass `Analyzer`, `Extractor` or `Decoder` from `nvox.api`.
3. Give it a unique dotted `name`, a semver `version`, `api_version = 1`,
   `targets`, `priority` and `requires`.
4. Emit artifacts through `Context`; never write to the evidence.
5. Add a fixture and a test under `tests/`.
6. Run the checks above.

## External tools

Register new binaries in `src/nvox/core/tools.py` with category, purpose,
milestone and the matching Nix attribute. `nvox doctor` picks them up
automatically, and the M5 bridge layer uses the same registry to decide whether
a bridge can run.

Version probes are best-effort: `probe_version` swallows errors and timeouts,
and a tool without a version flag still counts as installed.

## Adding a core feature

Core changes need a design note in the pull request covering:

- the new artifact or finding type, if any;
- limits (depth, size, timeout) and how they are enforced;
- interaction with dedup and provenance;
- what happens when an optional dependency is missing.

## Releases

1. Update the version in `pyproject.toml`.
2. Update the roadmap status and `docs/` for anything user-visible.
3. Run all checks on a clean `uv sync`.
4. Tag `vX.Y.Z` and publish the wheel.
