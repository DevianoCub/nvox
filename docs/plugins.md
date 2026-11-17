# Plugins

> **Status:** implemented through M5. The stable import surface is `nvox.api`
> (`API_VERSION = 1`). The built-in image pack covers PNG/JPEG/GIF/BMP/TIFF
> metadata and structure, configurable bit planes, channel analysis, carving and
> DEFLATE. Command plugins and external bridges run in bounded subprocesses;
> optional process isolation for Python plugins is planned for M7. Audio arrives
> in M6.

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
| `bridges` | `bridge.exiftool`, `bridge.pngcheck`, `bridge.zsteg`, `bridge.steghide`, `bridge.stegseek`, `bridge.outguess`, `bridge.stegsolve`, `bridge.binwalk`, `bridge.foremost`, `bridge.zbar`, `bridge.tesseract` | M5 |

The image pack is documented in detail, with options and limitations, in
[Image analysis](image-analysis.md).

The decoder pack is intentionally conservative: base encodings must validate
and produce meaningful text/magic, compression is magic-gated, and XOR keeps
only the best-scored candidates. See [Flag engine](flag-engine.md).

## External tool bridges

Bridge plugins are normal Python plugins with a binary in `requires`. The engine
checks the requirement before running them, so a missing tool is recorded as a
`requirement` skip and the scan continues. `--no-external` removes command
plugins and every plugin with a non-empty `requires`, which is useful for a
reproducible built-in-only run.

| Plugin | Tool | Typical inputs | Result |
|---|---|---|---|
| `bridge.exiftool` | `exiftool` | Images, audio, video, PDF, ZIP | Grouped JSON metadata |
| `bridge.pngcheck` | `pngcheck` | PNG | Verbose chunk/CRC report |
| `bridge.zsteg` | `zsteg` | PNG, BMP | LSB scan report |
| `bridge.steghide` | `steghide` | JPEG, BMP, WAV, AU | Probe report and extracted payload |
| `bridge.stegseek` | `stegseek` | JPEG, BMP, WAV, AU | Wordlist-cracked steghide payload |
| `bridge.outguess` | `outguess` | JPEG | Extracted payload |
| `bridge.stegsolve` | `java` | Images | Configured headless command output |
| `bridge.binwalk` | `binwalk` | Generic non-text artifacts | Signature report; optional extraction |
| `bridge.foremost` | `foremost` | Generic non-text artifacts | Carved files |
| `bridge.zbar` | `zbarimg` | Images | Decoded QR/barcode text |
| `bridge.tesseract` | `tesseract` | Images | OCR text |

Bridge options live under `[plugins.options."bridge.<tool>"]`:

```toml
[plugins.options."bridge.stegseek"]
wordlist = "/usr/share/wordlists/rockyou.txt"

[plugins.options."bridge.steghide"]
passphrase = ""

[plugins.options."bridge.outguess"]
key = ""

[plugins.options."bridge.foremost"]
types = ""                 # empty uses foremost's defaults

[plugins.options."bridge.binwalk"]
extract = false

[plugins.options."bridge.tesseract"]
lang = "eng"
psm = 6

[plugins.options."bridge.stegsolve"]
# jar = "/opt/Stegsolve.jar"
command = ["/path/to/headless-stegsolve", "{input}", "{outdir}"]
```

`bridge.stegseek` skips until a wordlist is configured. StegSolve is an
interactive Java GUI; the bridge accepts a `command` list for a headless fork or
wrapper, and otherwise reports a skip rather than opening a window during a
scan. All bridges use the same sandbox, timeout, output and file-size limits as
declarative command plugins.

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
later emissions are ignored. Command plugins and external bridges run in a
separate process group with a hard wall-clock timeout in
`nvox.core.sandbox`. Optional process isolation for arbitrary Python plugins is
planned for M7.

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
directly (no shell) through `nvox.core.sandbox.run_command`. If `requires` is
omitted, the first command token is added automatically when it looks like a
binary name, so a missing tool becomes an engine-level requirement skip.

`output` modes:

- `text` — stdout decoded as UTF-8 text;
- `bytes` — stdout stored as a binary artifact;
- `files` — every regular file written under `{outdir}` becomes an artifact;
  symlinks and files outside the output directory are ignored, and the file
  count/byte caps below are applied;
- `json` — stdout parsed as JSON; a top-level object becomes a metadata
  artifact, other values are wrapped as `{"value": ...}`.

For `text`, `bytes` and `json` output, a non-zero exit code with no stdout is
recorded as a skip, including a short stderr tail. A non-zero exit that still
produced stdout is kept as an artifact with `returncode` in its params, because
many forensic tools report warnings while still emitting useful data. In
`files` mode, regular files are retained even when the process exits non-zero;
the artifact carries the `returncode` and a short stderr tail so the partial
result is visible rather than silently lost. Requirements listed in `requires`
are checked before execution; missing tools produce a skip and never prevent
other plugins from running.

### Command sandbox

Every command plugin gets:

- a wall-clock timeout (`plugin.timeout`, or `[plugin.sandbox].timeout`);
- process-group termination on timeout, so forked children are stopped too;
- streamed stdout/stderr caps, applied while the child runs;
- best-effort POSIX `RLIMIT_CPU`, `RLIMIT_FSIZE` and `RLIMIT_CORE` limits;
- a per-command file-count, per-file and total output cap;
- symlink-safe output collection confined to `{outdir}`;
- a minimal environment: `PATH` and locale are inherited, while `HOME`,
  `TMPDIR`, `XDG_CONFIG_HOME` and `XDG_CACHE_HOME` point into the temporary
  workspace.

The optional `[plugin.sandbox]` table controls the limits:

```toml
[plugin]
name = "example.tool"
command = ["tool", "--json", "{input}"]
output = "json"

[plugin.sandbox]
timeout = 60
max_output_bytes = 1048576
max_file_bytes = 16777216
max_files = 1000
max_total_bytes = 67108864
inherit_env = false
env = { TESSDATA_PREFIX = "/custom/tessdata" }
```

`inherit_env = true` passes the whole caller environment; prefer `env` for the
few variables a tool actually needs. Flat keys such as `plugin.max_output_bytes`
are accepted for compact definitions, but the table form is recommended.

The sandbox is a cooperative containment layer for local CTF/forensics work,
not a security boundary against a hostile local user. It does not create a
network namespace, drop privileges or restrict arbitrary syscalls. Run untrusted
binaries under an OS-level sandbox if that is required.

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
