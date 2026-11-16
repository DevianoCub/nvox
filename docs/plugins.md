# Plugins

> **Status:** implemented through M3. The stable import surface is `nvox.api`
> (`API_VERSION = 1`). The built-in image pack covers PNG/JPEG/GIF/BMP/TIFF
> metadata and structure, configurable bit planes, channel analysis, carving and
> DEFLATE; audio arrives in M6. Command plugins gain hard sandboxing in M5 and
> optional process isolation for Python plugins is planned for M7.

Everything that analyses, extracts or decodes is a plugin. The core provides
evidence handling, the artifact store, scheduling, flag scanning and reporting;
plugins provide techniques.

## Kinds

| Kind | Contract | Typical use |
|---|---|---|
| `Analyzer` | one artifact in, artifacts out | metadata dump, LSB planes, spectrogram |
| `Extractor` | one artifact in, files out | carve embedded ZIP, unpack archive |
| `Decoder` | text/bytes in, text/bytes out | base64, hex, rot13, gzip, XOR |

All three are subclasses of `Plugin` and share the same scheduling contract.
Decoders are normal plugins too: their outputs return to the queue, and the flag
scanner searches them with full provenance.

## Built-in packs

| Pack | Plugins | Milestone |
|---|---|---|
| `generic` | `generic.strings` (ASCII + UTF-16LE/BE runs), `generic.carve` (embedded containers) | M1-M3 |
| `decoders` | `decoder.base64`, `base64url`, `base32`, `base85`, `hex`, `url`, `rot13`, `rot47`, `reverse`, `gzip`, `zlib`, `bzip2`, `xz`, `deflate`, `xor` | M2-M3 |
| `image` | `image.structure`, `image.png_chunks`, `image.jpeg`, `image.exif`, `image.gif`, `image.bmp`, `image.tiff`, `image.channels`, `image.lsb` | M2-M3 |

The image pack is documented in detail, with options and limitations, in
[Image analysis](image-analysis.md).

The decoder pack is intentionally conservative: base encodings must validate
and produce meaningful text/magic, compression is magic-gated, and XOR keeps
only the best-scored candidates. See [Flag engine](flag-engine.md).

## Discovery

Plugins are loaded from, in order:

1. built-in packs under `nvox/plugins/`;
2. directories in `[plugins].dirs` (`./plugins` and
   `~/.config/nvox/plugins` by default);
3. installed distributions exposing the `nvox.plugins` entry point group;
4. classes declared with `@register` and already imported.

Later sources override earlier ones by `name`, which makes it possible to
replace a core technique without forking Nvox. Every override is reported as a
warning. Broken plugin files and invalid manifests are reported and skipped;
they never abort a scan.

`nvox plugins dirs` prints the effective order and paths.

## Python plugins

A plugin is a class with a manifest and a `run` method:

```python
from nvox.api import Analyzer, Artifact, Context, register


@register
class TrailingData(Analyzer):
    name = "example.trailing_data"
    version = "0.1.0"
    api_version = 1
    targets = ("image/png", "image/jpeg")
    priority = 80
    requires = ()
    options = {"min_size": 16}
    description = "Report bytes after the logical end of an image."

    def run(self, artifact: Artifact, ctx: Context) -> None:
        trailer = find_trailer(artifact.payload, artifact.media_type)
        if trailer is None or len(trailer) < int(ctx.option("min_size", 16)):
            ctx.skip("no trailing data above threshold")
        ctx.blob("trailing-data", trailer)
```

Manifest fields:

| Field | Meaning |
|---|---|
| `name` | Unique dotted identifier; used by `plugins info` and config |
| `version` | Plugin version (semver) |
| `api_version` | Target `nvox.api.API_VERSION`; newer APIs are refused |
| `kind` | Set by the base class (`analyzer`, `extractor`, `decoder`) |
| `targets` | MIME patterns such as `image/*`, `audio/wav`, `*/*` |
| `priority` | Higher runs earlier; expensive plugins should be low |
| `requires` | External binaries; missing requirements cause a skip, not an error |
| `options` | Default option values, overridable in `[plugins.options."name"]` |
| `timeout` | Optional per-plugin timeout in seconds; defaults to `scan.timeout` |
| `description` | One-line summary shown by `nvox plugins list/info` |

Matching ignores MIME parameters, so `text/plain` matches
`text/plain; charset=utf-8`. Override `accepts(artifact)` to narrow further, for
example to skip text artifacts after extracting strings.

### Context API

| Method | Purpose |
|---|---|
| `ctx.text(name, text, **params)` | Emit a UTF-8 text artifact |
| `ctx.blob(name, data, media_type=..., **params)` | Emit a bytes artifact |
| `ctx.image(name, data, media_type=..., **params)` | Emit an image artifact |
| `ctx.audio(...)`, `ctx.archive(...)` | Emit audio/archive artifacts |
| `ctx.meta(name, mapping, **params)` | Emit canonical JSON metadata |
| `ctx.adopt(artifact)` | Normalise an artifact the plugin constructed itself |
| `ctx.option(name, default)` | Read a resolved option value |
| `ctx.options` | Read-only mapping of all resolved options |
| `ctx.max_artifact_bytes` | Maximum size for an emitted artifact |
| `ctx.artifacts` | Artifacts emitted so far (mainly useful in tests) |
| `ctx.skip(reason)` | Abort and record a skip with `kind="plugin"` |

`**params` are stored as artifact metadata and appear in the provenance record.
Every emission records its parent hash and plugin name, so the artifact graph is
traceable.

Plugins may also `yield` artifacts instead of calling `ctx` methods; the engine
consumes the generator and adopts any `Artifact` values. The explicit `ctx`
style is preferred because it keeps the parent/producer metadata consistent.

Emissions are transactional: artifacts from a plugin that later times out,
raises or calls `ctx.skip` are discarded. Identical payloads are stored once by
SHA-256, even when produced by different plugins. A plugin should therefore
emit derived *content*, not a byte-identical reinterpretation of its input.

The engine enforces `scan.max_artifact_bytes` on every emission and checks the
plugin deadline cooperatively. Python plugins run in worker threads, which
cannot be force-killed; a runaway plugin is abandoned at its timeout and its
later emissions are ignored. Hard process isolation is part of M5 for command
plugins and is planned as an optional mode for Python plugins in M7.

Plugins must not write to the evidence or to arbitrary paths. Use the context
to emit artifacts; the store decides where they live.

## Declarative command plugins

Any binary can be wrapped without writing Python. Put a `.toml` file into a
plugin directory:

```toml
[plugin]
name = "zsteg"
kind = "analyzer"
targets = ["image/png", "image/bmp"]
requires = ["zsteg"]
command = ["zsteg", "-a", "{input}"]
output = "text"
timeout = 60

[plugin.options]
all = { type = "bool", default = true }
```

Placeholders available in `command`:

| Placeholder | Value |
|---|---|
| `{input}` | Path to a temporary copy of the artifact |
| `{input_name}` | Basename of that temporary file |
| `{outdir}` | Writable output directory |
| `{option_name}` | Any declared option or config override |

Unknown `{name}` placeholders are left untouched, so shell snippets such as
`awk '{print}'` and JSON literals work as written. Commands are executed
directly (no shell) with a per-plugin timeout.

`output` modes:

- `text` — stdout decoded as UTF-8 text;
- `bytes` — stdout stored as a binary artifact;
- `files` — every file written under `{outdir}` becomes an artifact (up to
  1000 files);
- `json` — stdout parsed as JSON; a top-level object becomes a metadata
  artifact, other values are wrapped as `{"value": ...}`.

A non-zero exit code with no stdout is recorded as a skip, including a short
stderr tail. Requirements listed in `requires` are checked before execution;
missing tools produce a skip and never prevent other plugins from running.

Current limits: stdout is buffered by the child process before the artifact
size cap is applied, and command plugins run with the invoking user's
permissions. Process-group kill, output streaming caps and rlimits are added
with `core/sandbox.py` in M5.

## Hooks

A plugin may additionally implement any hook:

```python
class PolicyPlugin(Analyzer):
    name = "example.policy"

    def pre_ingest(self, path):
        if path.name.startswith("."):
            return "hidden files are excluded by policy"
        return None
```

| Hook | Contract |
|---|---|
| `pre_ingest(path)` | Return `None` to keep the input, a reason string to refuse it |
| `on_artifact(artifact, ctx)` | Observe a stored artifact; may emit new artifacts |
| `post_scan(result)` | Observe the completed `ScanResult` |
| `on_finding(finding)` | Return a (possibly modified) finding or `None`; called by the flag engine |

Hooks run in plugin priority order. An exception in a hook is isolated and
recorded as a skip with `kind="hook"`.

## Writing guidelines

- **Deterministic.** Same input and options produce the same artifacts, so
  dedup and reports stay stable.
- **No network.** Plugins must not reach the network by default; anything that
  does belongs behind an explicit opt-in.
- **Bounded.** Respect `scan.max_artifact_bytes`; do not materialise unbounded
  data.
- **Cheap first.** Expensive brute force should have a low priority and a
  configurable budget.
- **Explain skips.** Call `ctx.skip("reason")` rather than raising when a
  requirement is absent.
- **Name artifacts meaningfully.** The name is what users see in the artifact
  tree and in provenance chains.

## Versioning policy

`nvox.api.API_VERSION` is an integer. It changes only for breaking changes.
Additive changes keep the version. Nvox refuses plugins that declare a newer
API and reports them in `nvox plugins list`; plugins targeting an older API are
loaded with a warning.

## Reference implementation

`src/nvox/plugins/generic/strings.py` is the reference plugin for options,
MIME targets, `accepts` narrowing and `ctx.skip`. The image pack shows structural
metadata analyzers (`image.png_chunks`, `image.jpeg`), bounded extraction and
bit-plane emission; the decoder pack shows transform/gating patterns. A
copy-paste template and a conformance test harness are part of M7.
