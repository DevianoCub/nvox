# CLI reference

> **Status:** `doctor` and `--version` are implemented (M0). `scan`, `plugins`,
> `extract` and `report` describe the M1-M4 interface and are not available yet.

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

## `nvox plugins` (planned, M1)

```
nvox plugins list            # name, version, kind, targets, availability
nvox plugins info NAME       # manifest, options, requirements, docs
nvox plugins dirs            # discovery paths and load order
```

## `nvox extract` (planned, M1)

```
nvox extract <artifact-id> --out FILE
```

Writes a single stored artifact (bitstream, carved file, decoded payload) to
disk by id or prefix.

## `nvox report` (planned, M4)

```
nvox report out/ --format html --open
```

Re-renders reports from a previous run's `findings.json` without rescanning.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success, no flags found |
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

[tools]
disable = ["steghide"]
```

All keys are optional. Relative plugin directories are resolved against the
directory containing `nvox.toml` (or the current directory when defaults are
used). `~` is expanded.
