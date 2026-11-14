# CLI reference

> **Status:** `doctor`, `plugins list|info|dirs`, `extract` and `--version` are
> implemented. `scan` and `report` describe the M2/M4 interface and are not
> available yet.

## Synopsis

```
nvox [--config PATH] [--version] COMMAND [OPTIONS]
```

Global options:

| Option | Description |
|---|---|
| `--config`, `-c PATH` | Use an explicit `nvox.toml` instead of discovery |
| `--version`, `-V` | Print the version and exit |
| `--help` | Show help for a command |

## `nvox doctor`

Environment, configuration and optional-tool diagnostics.

```bash
nvox doctor
nvox doctor --verbose   # also probe versions of installed tools
nvox doctor --json      # machine-readable output
```

Output sections:

1. **Environment** — Nvox, Python, interpreter path, platform, `uv`, cwd.
2. **Configuration** — loaded file or defaults, flag formats, limits, plugin
   directories (with existence), disabled tools.
3. **Tools** — every known external tool grouped by category, with status, path
   and the milestone that uses it. `*` means found, `o` means not installed.
4. **Summary** — available/total count and a `nix shell` hint for missing tools.

The JSON form is stable enough for CI:

```json
{
  "environment": {"nvox": "0.1.0.dev0", "python": "3.14.8", "...": "..."},
  "config": {
    "path": null,
    "flag_formats": [],
    "plugin_dirs": [{"path": "...", "exists": false}],
    "limits": {"max_depth": 4, "timeout": 120.0, "jobs": 0, "effective_jobs": 8}
  },
  "tools": [
    {"name": "exiftool", "category": "metadata", "found": true,
     "path": "/run/current-system/sw/bin/exiftool", "stage": "M3"}
  ],
  "summary": {"available": 2, "missing": 20, "total": 22}
}
```

Exit code is `0` even when tools are missing, because they are optional. A
malformed configuration file exits with `1`.

## `nvox scan` (planned, M2+)

```bash
nvox scan <path> --flag-format 'ctf{}' [options]
```

| Option | Description |
|---|---|
| `--flag-format FORMAT` | Flag format such as `ctf{}`, `htb{}`, `pico{}`; repeatable |
| `--flag REGEX` | Raw regular expression instead of a format; repeatable |
| `-i`, `--ignore-case` | Case-insensitive matching |
| `--min-length N` | Minimum flag body length |
| `--depth N` | Override artifact-graph depth |
| `--timeout SECONDS` | Override per-plugin timeout |
| `--jobs N` | Worker count |
| `--plugins NAME,...` | Restrict to selected plugins |
| `--no-external` | Disable all external-tool bridges |
| `--no-xor-brute` | Skip single-byte XOR brute force |
| `--out DIR` | Output directory (default `out/`) |
| `--format json,html` | Report formats to write |
| `--tui` | Open the interactive artifact browser when the scan finishes |

Input may be a single file or a directory (recursive).

## `nvox plugins`

```
nvox plugins list [--json]   # name, version, kind, targets, availability
nvox plugins info NAME       # manifest, options, requirements, hooks
nvox plugins dirs            # discovery paths and load order
```

`plugins list` shows a status per plugin:

| Status | Meaning |
|---|---|
| `ok` | Ready to run |
| `missing` | A binary listed in `requires` is not installed |
| `disabled` | Disabled in `[plugins].disable` or `[tools].disable` |

The `Origin` column is `builtin`, `directory`, `entry-point` or `declared`.
`--json` emits the same data as a list of objects, suitable for CI; warnings go
to stderr so stdout stays valid JSON.

`plugins info NAME` prints the manifest, source path, requirement availability,
effectively merged option values and any hooks the plugin implements.

## `nvox extract`

```
nvox extract <artifact-id> [--store DIR] [--out FILE]
```

Writes a stored artifact (bitstream, carved file, decoded payload) to disk by
full SHA-256 or an unambiguous prefix. The store defaults to `out/` and uses the
layout `out/artifacts/<first two hex characters>/<sha256>`.

The payload is re-hashed before writing. A mismatch, an unknown id, an
ambiguous prefix or an existing output file exits with code `1` instead of
silently producing bad evidence. Without `--out`, the command prints the
on-disk path and hash.

## `nvox report` (planned, M4)

```
nvox report out/ --format html --open
```

Re-renders reports from a previous run's `findings.json` without rescanning.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success (for scans: no flags found) |
| `1` | Error (bad config, unreadable input) |
| `2` | Success, at least one flag found (scan only; useful in CI) |
| `130` | Interrupted |

## Environment variables

| Variable | Effect |
|---|---|
| `XDG_CONFIG_HOME` | Base for `nvox/nvox.toml` and the user plugin directory |
| `NO_COLOR` | Disable ANSI styling |
| `NVOX_DEBUG` | Planned: verbose engine logging |

## Configuration reference

```toml
[scan]
max_depth = 4
timeout = 120
max_artifact_bytes = 67108864
jobs = 0

[flags]
formats = ["ctf{}"]

[plugins]
dirs = ["plugins", "~/.config/nvox/plugins"]
disable = []             # plugin names that must never run

[plugins.options."generic.strings"]
min_length = 6
utf16 = false

[tools]
disable = ["steghide"]
```

Plugin option defaults are declared by the plugin; values under
`[plugins.options."<plugin-name>"]` override them. They are part of the dedup
key, so changing an option re-runs the plugin on the same artifact.

All keys are optional. Relative plugin directories are resolved against the
directory containing `nvox.toml` (or the current directory when defaults are
used). `~` is expanded.
