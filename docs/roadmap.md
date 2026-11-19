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
- [x] Store cleanup removes stale `*.tmp` files on open; evidence artifacts are
      never auto-deleted

Acceptance: the dummy chain `bytes -> text -> match` runs end to end;
`tests/test_engine.py` covers dedup, depth, timeout, requirement/disabled skips,
plugin errors and hooks; `tests/test_registry.py` covers discovery and TOML
plugins; `tests/test_cli_plugins.py` covers inspection and extraction.

## M2 — Flag engine (done)

- [x] Format compiler for `prefix{}` with multiple formats and `-i`
- [x] Raw regex mode (`--flag`) and `--min-length`/`--max-length`
- [x] Surfaces: whole raw payload, ASCII/UTF-16LE/BE runs, text artifacts,
      metadata artifacts and PNG LSB bitstreams
- [x] Decoders: base64/base64url/base32/base85/hex/url/rot13/rot47/reverse/
      gzip/zlib/bzip2/xz
- [x] Recursive decoding with the artifact depth budget and visited set
- [x] Single-byte XOR brute force with printable-text scoring
- [x] Findings with context, shortest-chain provenance, confidence; JSON output
- [x] Exit codes and `nvox scan` (non-TUI), including recursive directory scans
- [x] Minimal `image.exif` and `image.lsb` plugins needed by the flag fixtures
- [x] Fixtures: trailing data, base64, nested zlib, UTF-16, EXIF base64, XOR
      payload and PNG LSB chain, generated with the standard library

Acceptance: all fixtures are found end to end; report explains each chain;
`ruff`, `mypy` and the full test suite are green.

Scope note: full metadata (XMP/IPTC/ICC/PNG chunks/JPEG segments), bit planes
1-8 and arbitrary bit orders remain M3. Chunked/streaming raw scans and
overlapping windows remain M5/M7 performance work; M2 artifacts are in memory.

## M3 — Deep image analysis (done)

- [x] EXIF/XMP/IPTC/ICC; PNG chunks (`tEXt`, `zTXt`, `iTXt`, CRC, extra chunks)
- [x] JPEG segments (`APPn`, `COM`), GIF extensions, BMP/TIFF basics
- [x] Trailing/prepended data, polyglot detection
- [x] Bit planes 1-8 x channels x bit order x row/column major
- [x] Automatic zlib/deflate detection on bitstreams
- [x] Palette and alpha anomalies, channel filters, bit-plane images
- [x] Embedded file detection by magic bytes and carving
- [x] Optional QR/barcode and OCR hooks (M5 bridges: `bridge.zbar`, `bridge.tesseract`)

Acceptance: one fixture per technique in `tests/test_image_deep.py`; a 12 MP
PNG completes in 48.7 s against the default 120 s budget; the coverage table
and limitations are in [Image analysis](image-analysis.md).

## M4 — UX (done)

- [x] Rich scan output with live progress and grouped findings
- [x] Self-contained HTML report: artifact tree, previews, findings, provenance
      graph, filters
- [x] Textual TUI: artifact tree, artifact preview, findings, plugin toggles
- [x] `nvox report` re-render without rescanning

Acceptance: a real sample is triaged end-to-end from the terminal, and the HTML
report opens offline. The report and terminal workflow are documented in
[HTML reports and TUI](report.md).

## M5 — Bridges and declarative plugins (done)

- [x] `core/sandbox.py`: rlimits, process-group kill, streamed output caps for
      command plugins and bridges
- [x] Bridges: exiftool, zsteg, steghide, stegseek, outguess, pngcheck,
      binwalk, foremost, StegSolve, zbar, tesseract. StegSolve is GUI-only and
      requires a configured headless command; otherwise it records a skip.
- [x] Harden declarative TOML command plugins (sandbox, minimal environment,
      automatic executable requirements, symlink-safe file collection and
      output/file caps)
- [x] `doctor` integration (bridge names per tool) and graceful requirement
      skips when tools are missing
- [x] `shell.nix` and Arch installer script with available optional tools
      (delivered early; `jphide`, `jpseek` and `stegdetect` remain unpackaged)
- [x] `flake.nix` devshell with all optional tools

Acceptance: with tools installed everything runs; without them every bridge is
skipped with a reason and the scan still completes. Bridge tests use fake
executables on `PATH`, and a separate scan test removes every requirement to
verify that the artifact pipeline still completes.

## M6 — Audio and generic files (done)

- [x] Audio metadata (ID3, ffprobe), WAV sample LSB, MP3 frame anomalies
- [x] Spectrogram rendering, optional OCR of spectrogram text through the
      existing image/OCR bridge pipeline
- [x] DTMF and Morse hints, echo-hiding heuristics, sox bridge
- [x] Generic: entropy map, strings (multiple encodings), magic carving,
      archive recursion, PDF and Office basics

Acceptance: audio fixtures and recursive archive fixtures pass; audio pack
reuses the same plugin contracts as images.

## M7 — Hardening and 0.1 (done)

- [x] Zip-bomb and decompression-ratio guards, fuzz targets for parsers
- [x] Optional process isolation for untrusted Python plugins; the default
      remains a cooperative worker-thread timeout
- [x] Performance pass; decide on a Rust/PyO3 accelerator using benchmarks
- [x] Plugin template, conformance tests, migration notes
- [x] Full English docs review; Russian translation in `docs/ru/`
- [x] CI on Linux/macOS, signed release artifacts, version 0.1.0

Acceptance: 0.1.0 release checklist complete; no known crash on malformed input
in the fuzz corpus. See [Hardening](hardening.md), [Performance](performance.md),
[Migration](migration.md) and [Release](release.md).
