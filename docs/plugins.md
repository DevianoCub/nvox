# Plugins

> **Status:** the plugin model below is the target design for M1. In M0 only the
> stable import path (`nvox.api`, `API_VERSION = 1`) exists.

Everything that analyses, extracts or decodes is a plugin. The core provides
evidence handling, the artifact store, scheduling, flag scanning and reporting;
plugins provide techniques.

## Kinds

| Kind | Contract | Typical use |
|---|---|---|
| `Analyzer` | one artifact in, artifacts out | metadata dump, LSB planes, spectrogram |
| `Extractor` | one artifact in, files out | carve embedded ZIP, unpack archive |
| `Decoder` | text/bytes in, text/bytes out | base64, hex, rot13, gzip, XOR |

## Discovery

Plugins are loaded from, in order:

1. built-in packs under `nvox/plugins/`;
2. directories in `[plugins].dirs` (`./plugins` and
   `~/.config/nvox/plugins` by default);
3. installed distributions exposing the `nvox.plugins` entry point.

Later sources can override a built-in plugin with the same `name`, which makes
it possible to replace a core technique without forking Nvox.

## Python plugins

A plugin is a class with a manifest and an `run` method:

```python
from nvox.api import Analyzer, Artifact, Context, register


@register
class LsbPlanes(Analyzer):
    name = "image.lsb_planes"
    version = "1.0.0"
    api_version = 1
    targets = {"image/png", "image/jpeg", "image/bmp"}
    priority = 50
    requires: tuple[str, ...] = ()

    def run(self, artifact: Artifact, ctx: Context):
        image = artifact.as_image()
        for channel in (0, 1, 2, 3):
            for bits in (1, 2):
                for order in ("msb", "lsb"):
                    data = ctx.accel.lsb_bits(image, channel, bits, order)
                    yield ctx.text(
                        f"lsb ch={channel} bits={bits} order={order}",
                        bits_to_text(data),
                    )
```

Manifest fields:

| Field | Meaning |
|---|---|
| `name` | Unique dotted identifier; also used by `--plugins` |
| `version` | Plugin version (semver) |
| `api_version` | Target `nvox.api.API_VERSION`; mismatches are refused |
| `targets` | MIME patterns such as `image/*`, `audio/wav`, `*/*` |
| `priority` | Higher runs earlier; expensive plugins should be low |
| `requires` | External binaries; missing requirements cause a skip, not an error |
| `options` | Declarative option schema exposed to config and CLI |

### Context API (planned)

| Method | Purpose |
|---|---|
| `ctx.text(name, text)` | Emit a text artifact |
| `ctx.blob(name, data)` | Emit a bytes artifact |
| `ctx.image(name, image)` | Emit a derived image |
| `ctx.meta(name, mapping)` | Emit structured metadata |
| `ctx.decode(name, data)` | Run the decoder chain explicitly |
| `ctx.options(self)` | Resolved, validated plugin options |
| `ctx.log` | Structured logging that lands in the report |
| `ctx.skip(reason)` | Record a skip with a reason |

Plugins must not write to the evidence or to arbitrary paths. Use the context
to emit artifacts; the store decides where they live.

## Declarative command plugins

Any binary can be wrapped without writing Python:

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

Placeholders available in `command`: `{input}` (path to a temporary copy of the
artifact), `{outdir}`, and any declared option as `{option_name}`. Supported
`output` modes: `text`, `bytes` (stdout as an artifact), `files` (collect files
written to `{outdir}`) and `json` (map fields to artifacts with a small
template).

Command plugins run through `core/sandbox.py`: no shell, process group kill on
timeout, output size cap, inherited read-only input.

## Hooks

A plugin may additionally implement any hook:

```python
class MyPlugin(Analyzer):
    ...

    def on_finding(self, finding, ctx):
        if "test" in finding.value:
            return None  # drop obvious false positives
        return finding
```

| Hook | Contract |
|---|---|
| `pre_ingest(path, ctx)` | Return `None` to keep, a reason string to skip |
| `on_artifact(artifact, ctx)` | Side effects; runs once per stored artifact |
| `post_scan(result, ctx)` | Aggregate/annotate before reporting |
| `on_finding(finding, ctx)` | Return a (possibly modified) finding or `None` |

Hooks run in priority order. An exception in a hook is logged and isolated.

## Writing guidelines

- **Deterministic.** Same input and options produce the same artifacts, so
  dedup and reports stay stable.
- **No network.** Plugins must not reach the network by default; anything that
  does belongs behind an explicit opt-in.
- **Bounded.** Respect `ctx.limits`; do not materialise unbounded data.
- **Cheap first.** Expensive brute force should have a low priority and a
  configurable budget.
- **Explain skips.** Call `ctx.skip("reason")` rather than raising when a
  requirement is absent.
- **Name artifacts meaningfully.** The name is what users see in the artifact
  tree and in provenance chains.

## Versioning policy

`nvox.api.API_VERSION` is an integer. It changes only for breaking changes.
Additive changes keep the version. Plugins declare the API version they were
written against; Nvox refuses plugins with a newer major API and reports it in
`nvox plugins list`.

## Template

A copy-paste plugin template and a conformance test harness are part of M7.
Until then, `src/nvox/plugins/image/` will contain the reference implementations
added in M3.
