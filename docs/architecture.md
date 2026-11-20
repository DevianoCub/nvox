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
                                  JSON/CSV, HTML report, TUI
```

The loop is the important part: an analyzer that yields a new artifact puts it
back into the same queue. This is how a pixel plane becomes a bitstream,
decompresses to text, decodes from base64 and yields a flag without any
technique-specific glue.

## Components

| Module | Responsibility | Status |
|---|---|---|
| `core/evidence.py` | Read-only snapshot, SHA-256, size, magic/extension MIME detection | M1 |
| `core/store.py` | Content-addressed artifact store, disk mirror, `index.jsonl` | M1 |
| `core/registry.py` | Plugin discovery and validation (built-ins, dirs, entry points, declared) | M1 |
| `core/engine.py` | Queue, priorities, timeouts, depth/size budgets, dedup, workers | M1 |
| `core/hooks.py` | `pre_ingest`, `on_artifact`, `post_scan`, `on_finding` dispatch | M1 |
| `core/command_plugin.py` | Declarative TOML command plugins, sandbox and output caps | M1/M5 |
| `core/surfaces.py` | ASCII/UTF-16LE/BE runs with byte offsets | M2 |
| `core/flags.py` | Flag format compiler, raw regex, surface scan | M2 |
| `core/findings.py` | Findings, confidence, provenance chain | M2 |
| `core/magic.py` | Shared format signatures and embedded-container hit detection | M3 |
| `plugins/decoders/` | Base64/base32/hex/rot/xor/gzip/... transform plugins, raw DEFLATE | M2-M3 |
| `plugins/image/` | PNG/JPEG/GIF/BMP/TIFF structure and metadata, bit planes, channels | M2-M3 |
| `plugins/generic/` | Strings plus bounded magic-based container carving | M1-M3 |
| `plugins/bridges/` | Optional external-tool bridges with requirement skips | M5 |
| `core/report/` | Self-contained HTML report | M4 |
| `core/config.py` | `nvox.toml` discovery and validation | M0 |
| `core/tools.py` | External tool registry and detection | M0 |
| `core/doctor.py` | Environment diagnostics | M0 |
| `core/sandbox.py` | Bounded subprocess execution: streamed output caps, rlimits and process-group kill | M5 |
| `core/external.py` | Shared child environment and symlink-safe output collection | M5 |
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
plugins is stored once and analysed once. An artifact also carries its kind,
media type, parent hashes, producing plugin, option parameters and derivation
depth. Identical byte payloads therefore collapse to one node even if two
plugins named them differently; plugins should emit derived content rather than
a byte-identical reinterpretation of their input.

The store enforces `max_artifact_bytes`; anything larger is recorded as a skip
with a reason instead of being materialised. `nvox scan --out DIR` and
`nvox extract --store DIR` mirror payloads and `index.jsonl` to disk.

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
  the evidence a technique may run. The evidence artifact is depth 0; a plugin
  runs while its input depth is below the limit. A chain such as
  `PNG -> LSB -> zlib -> base64 -> flag` uses three derivations plus the flag
  match, and fits within the default limit of four.
- **Timeout** (`scan.timeout`, default 120s): per plugin invocation. Python
  plugins run in worker threads and check a cooperative deadline on emission;
  a hung plugin is abandoned at its timeout and late emissions are ignored.
  Command plugins and external bridges additionally receive a hard subprocess
  timeout, process-group termination and streamed output caps. With
  `scan.isolation = "process"`, eligible Python plugins also run in a spawned
  child that is killed on timeout.
- **Size** (`scan.max_artifact_bytes`): upper bound for materialised artifacts;
  oversized emissions become skips.
- **Workers** (`scan.jobs`, default auto): up to this many plugin invocations
  run concurrently, in priority order. The artifact queue itself advances in a
  deterministic breadth-first order.
- **Dedup**: `(artifact hash, plugin name, options JSON)` for scheduling, and
  payload SHA-256 for storage, so a plugin never runs twice on the same input
  with the same options.
- **Priority**: high-signal cheap checks (metadata, trailing data) run before
  expensive ones (DCT analysis, brute force).
- **Run budget**: a scan stops scheduling new plugin invocations after 10,000
  runs and records the reason.

A plugin that fails, times out or misses its requirements does not abort the
scan; the skip and its reason are recorded in the result. Skips are sorted by
kind, plugin and artifact for stable reports.

## Hooks

Hooks are the supported way to change core behaviour without editing the core:

| Hook | When | Use |
|---|---|---|
| `pre_ingest(path)` | before evidence is hashed | reject/filter inputs; return a reason string |
| `on_artifact(artifact, ctx)` | when an artifact is stored | attach external analysis, emit extra artifacts |
| `post_scan(result)` | after the queue drains | aggregate, re-rank, export |
| `on_finding(finding)` | for each flag match | suppress false positives, notify |

The flag scanner is passed to the engine as an optional component
(`Engine(flag_scanner=...)`), so it can be replaced programmatically by API
users. Configuration-level service overrides are not implemented yet.

## Why Python, and when native code

Python gives the fastest path to broad stego coverage: Pillow/numpy for pixels,
rich ecosystem for parsing formats, and plugins that users can write in a few
lines. The 0.1.0 benchmark did not justify a Rust/PyO3 accelerator: the measured
cost is distributed across plugin dispatch, artifact creation and speculative
decoders rather than one isolated pixel loop. The decision and baseline are
recorded in [Performance](performance.md); it is revisited only when a profile
shows a specific pure-Python parser dominating realistic workloads after its
budgets are applied.

External binaries are the other fast path. They are treated as optional
accelerators, not as the definition of a feature.

## Extension points

1. Built-in plugin packs: `src/nvox/plugins/{generic,decoders,image,audio,bridges}`.
2. Local plugin directories: `./plugins`, `~/.config/nvox/plugins`.
3. Installed packages exposing the `nvox.plugins` entry point.
4. Declarative TOML plugins wrapping any command.
5. Hooks in plugins.

API stability is governed by `nvox.api.API_VERSION` (currently `1`). Breaking
changes bump the major version and plugins declare the API version they target.
