# Roadmap

Milestones are sequential: the next one starts only when the current one is
green (`pytest`, `ruff check`, `ruff format --check`, `mypy`) and its acceptance
criteria are met.

## M0 — Bootstrap (done)

- [x] `uv` project, `src/` layout, hatchling build, `nvox` console script
- [x] Python 3.12+ support, dev group, optional `image`/`tui`/`docs` extras
- [x] `nvox --version`, `--config`, Typer + Rich CLI shell
- [x] `nvox.toml` discovery (`--config`, cwd, XDG) with validation
- [x] External tool registry with categories, Nix attributes and milestones
- [x] `nvox doctor` (human, `--verbose`, `--json`)
- [x] Test suite for config and CLI, ruff/mypy configuration
- [x] English documentation tree and reserved `docs/ru/`

Acceptance: `uv run nvox doctor` works on a clean machine; config errors are
clean messages, not tracebacks.

## M1 — Core engine (done)

- [x] Evidence ingestion: read-only snapshot, SHA-256, size, MIME sniffing
- [x] Content-addressed artifact store with size caps and disk mirroring
- [x] Artifact kinds: `bytes`, `text`, `image`, `audio`, `archive`, `meta`
- [x] Plugin base classes in `nvox.api`; registration decorator
- [x] Registry: built-ins, local directories, `nvox.plugins` entry points,
      declarative TOML plugins
- [x] Scheduler: priorities, per-plugin timeout, depth/size budgets, workers
- [x] Dedup by (content hash, plugin, options) and visited set
- [x] Hooks: `pre_ingest`, `on_artifact`, `post_scan`, `on_finding`
- [x] `nvox plugins list|info|dirs`, `nvox extract`
- [x] Skip records with reasons; structured engine events (`logging` plus an
      `on_event` sink)

Acceptance: the dummy chain `bytes -> text -> match` runs end to end;
`tests/test_engine.py` covers dedup, depth, timeout, requirement/disabled skips,
plugin errors and hooks; `tests/test_registry.py` covers discovery and TOML
plugins; `tests/test_cli_plugins.py` covers inspection and extraction.

## M2 — Flag engine

- [ ] Format compiler for `prefix{}` with multiple formats and `-i`
- [ ] Raw regex mode (`--flag`) and `--min-length`
- [ ] Surfaces: raw bytes with overlap, ASCII/UTF-16 strings, metadata, text
      artifacts, bitstreams
- [ ] Decoders: base64/base64url/base32/base85/hex/url/rot13/rot47/reverse/
      gzip/zlib/bzip2/xz
- [ ] Recursive decoding with depth budget and visited set
- [ ] Single-byte XOR brute force with scoring
- [ ] Findings with context, chain, confidence; JSON output
- [ ] Exit codes and `nvox scan` (non-TUI)
- [ ] Fixtures: flag in EXIF base64, trailing data, XOR payload, LSB chain

Acceptance: all fixtures found, no false negatives; report explains each chain.

## M3 — Deep image analysis

- [ ] EXIF/XMP/IPTC/ICC; PNG chunks (`tEXt`, `zTXt`, `iTXt`, CRC, extra chunks)
- [ ] JPEG segments (`APPn`, `COM`), GIF extensions, BMP/TIFF basics
- [ ] Trailing/prepended data, polyglot detection
- [ ] Bit planes 1-8 x channels x bit order x row/column major
- [ ] Automatic zlib/deflate detection on bitstreams
- [ ] Palette and alpha anomalies, channel filters, bit-plane images
- [ ] Embedded file detection by magic bytes and carving
- [ ] Optional: QR/barcode and OCR hooks

Acceptance: one fixture per technique; 12 MP PNG stays within the time budget;
coverage table in the docs.

## M4 — UX

- [ ] Rich scan output with live progress and grouped findings
- [ ] Self-contained HTML report: artifact tree, previews, findings, provenance
      graph, filters
- [ ] Textual TUI: artifact tree, artifact preview, findings, plugin toggles
- [ ] `nvox report` re-render without rescanning

Acceptance: a real sample is triaged end-to-end from the terminal, and the HTML
report opens offline.

## M5 — Bridges and declarative plugins

- [ ] `core/sandbox.py`: rlimits, process-group kill, streamed output caps
- [ ] Bridges: exiftool, zsteg, steghide, stegseek, outguess, pngcheck,
      binwalk, foremost, StegSolve, zbar, tesseract
- [ ] Harden declarative TOML command plugins (basic runner landed in M1)
- [ ] `doctor` integration and graceful skips when tools are missing
- [ ] `flake.nix` devshell with all optional tools

Acceptance: with tools installed everything runs; without them every bridge is
skipped with a reason and the scan still completes.

## M6 — Audio and generic files

- [ ] Audio metadata (ID3, ffprobe), WAV sample LSB, MP3 frame anomalies
- [ ] Spectrogram rendering, optional OCR of spectrogram text
- [ ] DTMF and Morse hints, echo-hiding heuristics, sox bridge
- [ ] Generic: entropy map, strings (multiple encodings), magic carving,
      archive recursion, PDF and Office basics

Acceptance: audio fixtures and recursive archive fixtures pass; audio pack
reuses the same plugin contracts as images.

## M7 — Hardening and 0.1

- [ ] Zip-bomb and decompression-ratio guards, fuzz targets for parsers
- [ ] Performance pass; decide on a Rust/PyO3 accelerator using benchmarks
- [ ] Plugin template, conformance tests, migration notes
- [ ] Full English docs review; Russian translation in `docs/ru/`
- [ ] CI on Linux/macOS, signed release artifacts, version 0.1.0

Acceptance: 0.1.0 release checklist complete; no known crash on malformed input
in the fuzz corpus.
