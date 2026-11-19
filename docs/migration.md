# Migrating to 0.1.0

> **Status:** 0.1.0 is the first tagged release. `nvox.api.API_VERSION` remains
> `1`; the plugin import surface and artifact model are unchanged from the M6
> development snapshots.

Most users can upgrade a development checkout without code changes. The notes
below cover configuration, behaviour and plugin-author details that differ from
pre-0.1 snapshots.

## Configuration

| Setting | 0.1.0 behaviour |
|---|---|
| `scan.isolation` | New. `"thread"` is the default; `"process"` opt-in for eligible Python plugins |
| `scan.max_depth` | Unchanged |
| `scan.timeout` | Unchanged cooperative default, now also the parent deadline for process-isolated plugins |
| `scan.max_artifact_bytes` | Unchanged |
| `scan.jobs` | Unchanged; `0` means one worker per CPU |
| `generic.archive.max_ratio` | Enforced for ZIP members and single-stream gzip/bzip2/xz |
| `generic.archive.max_file_bytes` | Also used for the actual stream expansion cap |

Example:

```toml
[scan]
isolation = "thread"
max_depth = 4
timeout = 120
max_artifact_bytes = 67108864
jobs = 0
```

`nvox doctor --json` now includes `config.limits.isolation`. Existing JSON
consumers that read named keys are unaffected.

## CLI

`nvox scan` accepts:

```bash
nvox scan evidence.bin --isolation thread   # default
nvox scan evidence.bin --isolation process  # kill runaway Python plugins
```

The default output, report schema, exit codes and `--json` shape are unchanged.
Process mode can add a small start-up cost per plugin invocation and changes
only how an eligible Python plugin is executed.

## Archives

Highly compressible members above the `max_ratio` threshold are now skipped
instead of expanded. A legitimate archive that trips the guard can raise the
limit explicitly:

```toml
[plugins.options."generic.archive"]
max_ratio = 5000
```

The ratio check applies after a bounded read, so it also catches archives with
incorrect size metadata. Skipped entries remain visible in `archive/summary`.

## Plugins

The plugin contract is unchanged:

- `nvox.api` remains the stable import surface;
- `API_VERSION` is still `1`;
- emitted artifacts keep their parent hashes, producer and depth;
- a missing external requirement is still a `requirement` skip;
- command plugins keep the same sandbox and output modes.

New in 0.1.0:

- copy-paste templates in `examples/plugins/template.py` and
  `examples/plugins/command-template.toml`;
- conformance tests in `tests/test_plugin_conformance.py`;
- optional process isolation for built-in, plugin-directory and entry-point
  Python plugins.

Plugin authors should check:

1. `name` is unique and valid, and `api_version` is `1` or lower;
2. `targets` is non-empty and `run` respects `ctx.max_artifact_bytes`;
3. absent requirements call `ctx.skip("reason")` or declare `requires`;
4. the plugin is deterministic, does not use the network and does not write
   outside emitted artifacts;
5. a short smoke test runs through the registry, as shown by the template test.

## Documentation

English pages are the source of truth. A Russian translation now lives under
[`docs/ru/`](ru/index.md). Historical milestones remain in the
[roadmap](roadmap.md); the current release checklist is in
[Release](release.md).
