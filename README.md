# Nvox

Nvox is a modular forensic and steganography analysis framework for images,
audio and arbitrary files. It is built for CTF work and for forensic triage:
point it at a file, give it the flag format, and it explores the artifact graph
(LSB planes, metadata, embedded files, decoded payloads, ...) looking for
matches.

```
nvox scan photo.png --flag-format 'ctf{}'
```

## Status

Early development. The current milestone is **M0 (bootstrap)**: project
scaffolding, configuration discovery, plugin API surface and `nvox doctor`.
The scanning engine, flag engine and plugins land in M1-M3; see the
[roadmap](docs/roadmap.md).

## Why it is modular

- **Artifact graph, not a checklist.** Every analysis result becomes a new
  artifact (text, bytes, image, bitstream, archive) that is fed back into the
  pipeline. Chains such as `PNG -> LSB bitstream -> zlib -> base64 -> flag`
  work without special-casing.
- **Three plugin kinds.** Analyzers turn an artifact into new artifacts,
  extractors carve files, decoders transform payloads and participate in flag
  search.
- **Drop-in plugins.** Ships with Python plugins and declarative TOML plugins
  that wrap any external binary, so adding coverage rarely requires touching
  the core.
- **Hooks and overrides.** `pre_ingest`, `on_artifact`, `post_scan` and
  `on_finding` let plugins extend or replace core behaviour.
- **External tools are optional.** Built-in implementations work on a clean
  Python install; `steghide`, `zsteg`, `binwalk`, `exiftool` and friends are
  used automatically when present. `nvox doctor` reports what is available.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

Optional extras: `image` (Pillow, numpy), `tui` (Textual), `docs`
(MkDocs Material).

## Install

```bash
git clone <repo> Nvox && cd Nvox
uv sync --group dev
uv run nvox doctor
```

To try optional image dependencies:

```bash
uv sync --group dev --extra image
```

## Usage

```bash
nvox doctor                      # environment, config and external tools
nvox doctor --json               # machine-readable diagnostics
nvox --version
```

Flag formats are user supplied: `{}` is the only placeholder and matches the
flag body, so `ctf{}`, `htb{}` and `pico{}` all work without configuration.

```bash
nvox scan challenge.png --flag-format 'htb{}'
nvox scan ./dump --flag 'flag\{[^}]+\}' -i --depth 4
```

See [docs/cli.md](docs/cli.md) for the full interface (scan lands in M1/M2).

## Documentation

English documentation lives in [`docs/`](docs/index.md).
[`docs/ru/`](docs/ru/) is reserved for the Russian translation and is
intentionally empty for now.

## Roadmap

| Milestone | Scope |
|---|---|
| M0 | Bootstrap, config, `doctor` (current) |
| M1 | Core engine: artifacts, registry, scheduler, hooks |
| M2 | Flag engine: formats, decoders, XOR brute, provenance |
| M3 | Deep image analysis and steganography |
| M4 | CLI polish, HTML report, TUI |
| M5 | External tool bridges, declarative plugins |
| M6 | Audio and generic file analysis |
| M7 | Hardening, docs, 0.1 release |

## Legal

Nvox is intended for analysing files you own or are authorised to examine
(CTF challenges, incident response, research). It opens evidence read-only and
writes results to a separate output directory.
