# Nvox

Nvox is a modular forensic and steganography analysis framework for images,
audio and arbitrary files. It is built for CTF work and for forensic triage:
point it at a file and optionally give it a flag format, then it explores the
artifact graph (LSB planes, metadata, embedded files, decoded payloads, ...)
looking for matches or collecting everything it can extract.

```
nvox scan photo.png --flag-format 'ctf{}'
```

## Status

**0.1.0** is the first tagged release. M0-M7 are complete: bootstrap, core
engine, flag engine, deep image analysis, UX, external bridges and plugin
hardening, audio/generic analysis, and the M7 hardening/release work. Nvox
provides configuration,
`nvox doctor`, evidence ingestion, the artifact store, plugin discovery, the
scheduler/hook engine, `nvox scan`, `nvox plugins`, `nvox extract`, `nvox report`
and `nvox tui`. The built-in decoder chain covers base encodings, common
compression and XOR brute force. The image pack parses PNG/JPEG/GIF/BMP/TIFF
metadata and structure, extracts configurable PNG bit planes, analyses
palette/alpha/channel anomalies, carves embedded containers and decodes raw
DEFLATE. The audio pack parses WAV/MP3 metadata, extracts sample bit planes,
renders spectrograms and decodes DTMF/Morse hints. The generic pack adds entropy
maps, recursive ZIP/TAR/compression extraction, PDF and OOXML inspection.
Optional bridges add ExifTool, pngcheck, zsteg, steghide, stegseek, outguess,
StegSolve, binwalk, foremost, zbar, tesseract, ffprobe and SoX; missing tools
become recorded skips. Archive extraction is bounded by entry, size, total and
compression-ratio limits, and `scan.isolation = "process"` can hard-kill an
untrusted Python plugin on timeout. Scans write a self-contained HTML report;
the Textual TUI supports artifact triage with plugin toggles. See the
[roadmap](docs/roadmap.md), [image analysis coverage](docs/image-analysis.md),
[audio analysis](docs/audio-analysis.md),
[generic file analysis](docs/generic-analysis.md),
[hardening](docs/hardening.md),
[performance](docs/performance.md) and [HTML/TUI guide](docs/report.md).

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
  friends use the same plugin contract; missing tools are skipped with a reason,
  and `nvox doctor` reports which bridge uses each binary.

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

To install all optional external tools at once:

```bash
# NixOS / Nix (flake devshell; nix-shell also works)
nix develop

# Arch Linux (uses yay/paru for AUR packages when available)
./scripts/install-arch-tools.sh
```

See [docs/tooling.md](docs/tooling.md) for details and unavailable tools.

## Usage

```bash
nvox scan photo.png                              # artifact-only scan, no flag search
nvox scan photo.png --csv --out out/             # artifact CSV for spreadsheets
nvox scan photo.png --no-html --out out/         # skip the HTML report
nvox scan photo.png --flag-format 'ctf{}'
nvox scan ./dump --flag 'flag\{[^}]+\}' -i --depth 4
nvox scan challenge.bin --flag-format 'htb{}' --json --out out/
nvox scan challenge.wav --flag-format 'ctf{}' --out out/   # WAV LSB, tones, spectrogram
nvox scan song.mp3 --flag-format 'pico{}' --no-external     # ID3 and MPEG frames
nvox scan bundle.zip --flag-format 'ctf{}' --out out/       # recursive archives
nvox scan challenge.pdf --flag-format 'htb{}' --out out/    # PDF structure and text
nvox scan untrusted.py.bin --isolation process                # hard-kill Python plugins
nvox report out/                                 # re-render HTML without rescanning
nvox report out/ --open                          # open the report in a browser
nvox tui --report out/                           # Textual artifact browser
nvox tui challenge.png --out out/                # scan and browse
nvox doctor                      # environment, config and external tools
nvox doctor --json               # machine-readable diagnostics
nvox plugins list                # loaded plugins and availability
nvox plugins info generic.strings
nvox plugins dirs                # discovery order and paths
nvox extract <sha256> --store out/
nvox --version
```

The TUI requires the optional extra:

```bash
uv sync --extra tui
```

Flag formats are user supplied: `{}` is the only placeholder and the braces are
part of the flag syntax, so `ctf{}`, `htb{}` and `pico{}` match `ctf{...}`,
`htb{...}` and `pico{...}`. Without a format or `--flag`, `scan` runs the
artifact pipeline without flag search and writes all artifact metadata to
`out/findings.json`; `--csv` additionally writes `out/artifacts.csv`. With a
format, it exits with code `2` when at least one flag is found; `--json` emits
stdout-only JSON.

See [docs/cli.md](docs/cli.md) for the full interface,
[docs/report.md](docs/report.md) for HTML/TUI triage,
[docs/plugins.md](docs/plugins.md) for writing plugins,
[docs/image-analysis.md](docs/image-analysis.md) for the M3 technique coverage
and limitations, [docs/audio-analysis.md](docs/audio-analysis.md) for the M6
audio pack, [docs/generic-analysis.md](docs/generic-analysis.md) for entropy,
archives, PDF and Office, and [docs/tooling.md](docs/tooling.md) for optional
tools.

## Documentation

English documentation lives in [`docs/`](docs/index.md). The Russian
translation is in [`docs/ru/`](docs/ru/index.md). Hardening, performance and
release notes are in [`docs/hardening.md`](docs/hardening.md),
[`docs/performance.md`](docs/performance.md), [`docs/migration.md`](docs/migration.md)
and [`docs/release.md`](docs/release.md).

## Roadmap

| Milestone | Scope |
|---|---|
| M0 | Bootstrap, config, `doctor` (done) |
| M1 | Core engine: artifacts, registry, scheduler, hooks (done) |
| M2 | Flag engine: formats, decoders, XOR brute, provenance (done) |
| M3 | Deep image analysis and steganography (done) |
| M4 | CLI polish, HTML report, TUI (done) |
| M5 | External tool bridges, declarative plugin hardening (done) |
| M6 | Audio and generic file analysis (done) |
| M7 | Hardening, docs, 0.1 release (done) |

## Legal

Nvox is intended for analysing files you own or are authorised to examine
(CTF challenges, incident response, research). It opens evidence read-only and
writes results to a separate output directory.
