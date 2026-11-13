# Architecture

## Pipeline

```
            +-----------+      +----------------+      +------------------+
 evidence ->| ingest &  |----->| artifact store |<---->| scheduler/queue  |
 (read-only)|  hash     |      | (content       |      | depth, timeouts, |
            +-----------+      |  addressed)    |      | workers, dedup   |
                               +--------+-------+      +---------+--------+
                                        ^                        |
                                        |                        v
                               +--------+------------------------+-------+
                               |              plugin registry            |
                               |  analyzers | extractors | decoders      |
                               |  Python plugins | TOML command plugins |
                               +--------+--------------------------------+
                                        |
                                        v
                               +--------+--------+      +-----------------+
                               |  flag engine    |----->| findings +      |
                               |  patterns,      |      | provenance      |
                               |  decoding chain |      +--------+--------+
                               +-----------------+               |
                                                                 v
                                                      JSON / HTML / TUI
```

The loop is the important part: an analyzer that yields a new artifact puts it
back into the same queue. This is how a pixel plane becomes a bitstream,
decompresses to text, decodes from base64 and yields a flag without any
technique-specific glue.

## Components

| Module | Responsibility | Status |
|---|---|---|
| `core/evidence.py` | Read-only ingestion, SHA-256, size, MIME detection | M1 |
| `core/artifacts.py` | Content-addressed artifact store, dedup | M1 |
| `core/registry.py` | Plugin discovery (Python, TOML, entry points) | M1 |
| `core/scheduler.py` | Queue, priorities, timeouts, depth/size limits, workers | M1 |
| `core/pipeline.py` | Graph orchestration, visited set, budgets | M1 |
| `core/hooks.py` | `pre_ingest`, `on_artifact`, `post_scan`, `on_finding` | M1 |
| `core/flags.py` | Flag format compiler and surface scanning | M2 |
| `core/decoders.py` | Base64/base32/hex/rot/xor/zlib/... transforms | M2 |
| `core/findings.py` | Findings, context, provenance chain | M2 |
| `core/report/` | JSON and self-contained HTML reports | M2/M4 |
| `core/config.py` | `nvox.toml` discovery and validation | M0 |
| `core/tools.py` | External tool registry and detection | M0 |
| `core/doctor.py` | Environment diagnostics | M0 |
| `core/sandbox.py` | Subprocess execution with limits | M5 |
| `api.py` | Stable plugin API (`API_VERSION`) | M1 |
| `cli.py` | Typer commands | M0+ |
| `tui/` | Textual interface | M4 |

## Artifacts

An artifact is an immutable, named blob plus metadata. Kinds:

- `bytes` — raw binary payload (bitstream, carved file, decompressed data);
- `text` — decoded text;
- `image` — a derived image (bit plane, channel difference, spectrogram);
- `audio` — a derived audio stream;
- `archive` — an extracted container listing;
- `meta` — structured metadata (EXIF, chunks, tags).

Each artifact is keyed by its SHA-256 so the same payload discovered by two
plugins is analysed once. The store enforces `max_artifact_bytes`; anything
larger is recorded as a skip with a reason instead of being materialised.

## Plugin types

| Kind | Input | Output | Example |
|---|---|---|---|
| Analyzer | one artifact | zero or more artifacts | LSB plane extraction, metadata dump |
| Extractor | one artifact | files/blobs | signature carving, archive extraction |
| Decoder | text/bytes | text/bytes | base64, hex, rot13, zlib, XOR brute |

Decoders are also plugins, so adding a proprietary obfuscation scheme requires
no core changes. See [Plugins](plugins.md).

## Scheduling and limits

- **Depth** (`scan.max_depth`, default 4): how many derivation steps away from
  the evidence a technique may run. `PNG -> LSB -> zlib -> base64 -> flag` is
  four steps.
- **Timeout** (`scan.timeout`, default 120s): per plugin invocation, enforced
  for Python plugins by the scheduler and for subprocess plugins by
  `sandbox.py`.
- **Size** (`scan.max_artifact_bytes`): upper bound for materialised artifacts.
- **Workers** (`scan.jobs`, default auto): parallelism across artifacts.
- **Dedup**: content hash plus plugin identity, so a plugin never runs twice on
  the same input.
- **Priority**: high-signal cheap checks (metadata, trailing data) run before
  expensive ones (DCT analysis, brute force).

A plugin that fails, times out or misses its requirements does not abort the
scan; the skip and its reason are recorded in the report.

## Hooks

Hooks are the supported way to change core behaviour without editing the core:

| Hook | When | Use |
|---|---|---|
| `pre_ingest` | before evidence is hashed | reject/filter inputs, add context |
| `on_artifact` | when an artifact is created | attach external analysis |
| `post_scan` | after the queue drains | aggregate, re-rank, export |
| `on_finding` | for each flag match | suppress false positives, notify |

Plugins can also override services (flag scanner, reporter, artifact store)
through configuration. This is deliberately a coarser extension point: it is
powerful, so the default implementations stay dependency-free.

## Why Python, and when native code

Python gives the fastest path to broad stego coverage: Pillow/numpy for pixels,
rich ecosystem for parsing formats, and plugins that users can write in a few
lines. The architecture keeps hot loops (bit-plane extraction, entropy maps,
sliding-window scanning) behind small functions in `api.accel`, so a Rust
module (PyO3) or a C extension can be swapped in later without touching plugin
code.

The rule for native code is benchmark-driven: a native implementation is added
only when a profiled workload (for example a 12-megapixel PNG) misses its
budget and the pure-Python path cannot be optimised further.

External binaries are the other fast path. They are treated as optional
accelerators, not as the definition of a feature.

## Extension points

1. Built-in plugin packs: `src/nvox/plugins/{image,audio,generic,bridges}`.
2. Local plugin directories: `./plugins`, `~/.config/nvox/plugins`.
3. Installed packages exposing the `nvox.plugins` entry point.
4. Declarative TOML plugins wrapping any command.
5. Hooks and service overrides in `nvox.toml`.

API stability is governed by `nvox.api.API_VERSION` (currently `1`). Breaking
changes bump the major version and plugins declare the API version they target.
