# Hardening and malformed input

> **Status:** implemented in M7 for the 0.1.0 release.

Nvox parses hostile files during CTF triage and incident response. The goal is
not to make Python a security sandbox, but to keep malformed input from
escaping the plugin error boundary, exhausting memory or silently expanding a
decompression bomb.

## Decompression guards

`generic.archive` applies several independent limits before and after
decompression:

| Control | Default | Effect |
|---|---:|---|
| `max_entries` | 256 | Maximum archive members inspected |
| `max_file_bytes` | 32 MiB | Declared and actual per-member output cap |
| `max_total_bytes` | 96 MiB | Total extracted bytes across the archive |
| `max_ratio` | 1000 | Maximum declared/actual expansion ratio |

ZIP members are checked against their declared size and compression ratio
before reading. The actual read is capped at `max_file_bytes + 1`, and the
ratio is checked again against the produced bytes, so a forged central
directory cannot bypass the limit. gzip, bzip2 and xz streams use bounded
decompressors and the same ratio check. Recursive archives remain bounded by
`scan.max_depth`, `scan.max_artifact_bytes` and the per-archive limits.

A skipped member is recorded in `archive/summary` with a reason such as
`file exceeds max_file_bytes` or `compression ratio exceeds max_ratio`. The
scan continues and other plugins still run.

```toml
[plugins.options."generic.archive"]
max_entries = 128
max_file_bytes = 8388608
max_total_bytes = 33554432
max_ratio = 500
```

Other parsers use bounded offsets and output caps as well. The PDF stream
decoder is limited by `generic.pdf.max_stream_bytes`; PNG/TIFF/JPEG/GIF parsers
cap text and chunk sizes; emitted artifacts are rejected above
`scan.max_artifact_bytes`.

## Process isolation

Python plugins run cooperatively in worker threads by default. A plugin that
blocks in C code cannot be interrupted. Enable optional process isolation when
running untrusted local Python plugins:

```toml
[scan]
isolation = "process"
```

or per scan:

```bash
nvox scan challenge.bin --isolation process
```

Eligible plugins are discovered again in a spawned child process and run with
a pipe-based result protocol. The parent kills the child on timeout, so a
plugin that ignores `ctx` deadlines cannot hold the scan forever. Emissions
are serialised back to the parent with their provenance intact.

Limitations:

- built-in, plugin-directory and entry-point Python plugins are eligible;
- ad-hoc plugins registered directly in Python and command/bridge plugins stay
  on the thread or sandbox runner;
- each invocation pays process start-up cost, so thread mode remains the
  default;
- process isolation is not an OS sandbox: it does not restrict filesystem or
  network access. Use a container, VM or OS sandbox for hostile code.

Both modes keep the existing bound: a timeout becomes a `timeout` skip and does
not abort the rest of the scan.

## Fuzz corpus

`tests/fuzz_targets.py` contains deterministic seeds for PNG, JPEG, GIF, BMP,
WAV, DTMF, ZIP, TAR, gzip, PDF, OOXML, empty and truncated inputs. Each seed is
run unmodified and with structural mutations: bit flips, truncation, deletion,
insertion, zero runs and trailing garbage.

The pytest suite runs a short pass:

```bash
uv run pytest tests/test_fuzz_corpus.py
```

For a longer local or release-candidate pass:

```bash
uv run python scripts/fuzz-parsers.py --iterations 200
```

A failure means an input escaped the engine's plugin error boundary instead of
becoming a skip or an ordinary parser error. The release checklist requires the
short corpus to be green; the longer pass is recommended before tagging.

## Optional tools

External bridges and command plugins remain optional. Missing binaries produce
`requirement` skips, never scan failures. Commands run through the bounded
sandbox described in [Plugins](plugins.md#command-sandbox).
