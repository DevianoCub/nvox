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

Early development. **M0 (bootstrap)**, **M1 (core engine)** and **M2 (flag
engine)** are complete: configuration, `nvox doctor`, evidence ingestion, the
artifact store, plugin discovery, the scheduler/hook engine, `nvox scan`,
`nvox plugins` and `nvox extract`. The built-in decoder chain covers base
encodings, common compression and XOR brute force; the image pack currently
provides minimal EXIF strings and PNG LSB extraction. Deep image analysis,
external bridges, audio and the HTML/TUI reports land in M3-M7; see the
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
- **Hooks.** `pre_ingest`, `on_artifact`, `post_scan` and `on_finding` let
  plugins observe or veto inputs, add artifacts and filter findings.
- **External tools are optional.** Built-in implementations work on a clean
  Python install. Bridges for `steghide`, `zsteg`, `binwalk`, `exiftool` and
  friends are planned for M5; `nvox doctor` already reports what is available.

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
nvox scan photo.png --flag-format 'ctf{}'
nvox scan ./dump --flag 'flag\{[^}]+\}' -i --depth 4
nvox scan challenge.bin --flag-format 'htb{}' --json --out out/
nvox doctor                      # environment, config and external tools
nvox doctor --json               # machine-readable diagnostics
nvox plugins list                # loaded plugins and availability
nvox plugins info generic.strings
nvox plugins dirs                # discovery order and paths
nvox extract <sha256> --store out/
nvox --version
```

Flag formats are user supplied: `{}` is the only placeholder and the braces are
part of the flag syntax, so `ctf{}`, `htb{}` and `pico{}` match `ctf{...}`,
`htb{...}` and `pico{...}`. `scan` writes `out/findings.json` and exits with
code `2` when at least one flag is found; add `--json` for stdout-only JSON.

See [docs/cli.md](docs/cli.md) for the full interface and
[docs/plugins.md](docs/plugins.md) for writing plugins.

## Documentation

English documentation lives in [`docs/`](docs/index.md).
[`docs/ru/`](docs/ru/) is reserved for the Russian translation and is
intentionally empty for now.

## Roadmap

| Milestone | Scope |
|---|---|
| M0 | Bootstrap, config, `doctor` (done) |
| M1 | Core engine: artifacts, registry, scheduler, hooks (done) |
| M2 | Flag engine: formats, decoders, XOR brute, provenance (done) |
| M3 | Deep image analysis and steganography (next) |
| M4 | CLI polish, HTML report, TUI |
| M5 | External tool bridges, declarative plugin hardening |
| M6 | Audio and generic file analysis |
| M7 | Hardening, docs, 0.1 release |

## Legal

Nvox is intended for analysing files you own or are authorised to examine
(CTF challenges, incident response, research). It opens evidence read-only and
writes results to a separate output directory.
