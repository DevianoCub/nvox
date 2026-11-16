# CLI reference

> **Status:** `scan`, `doctor`, `plugins list|info|dirs`, `extract` and
> `--version` are implemented. `report` and the `--format`/`--tui` options are
> M4 work and are not available yet.

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

## `nvox scan`

Scans a file or a directory recursively and writes a machine-readable report.
Flag search is optional: without a format or raw pattern, Nvox runs the artifact
pipeline and writes all artifact metadata to JSON/CSV without searching for flags.

```bash
nvox scan <path> [--flag-format 'ctf{}' | --flag REGEX] [options]
```

Examples:

```bash
# Artifact-only scan: no flag search, full report on disk
nvox scan photo.png --out out/

# Also write a spreadsheet-friendly artifact CSV
nvox scan photo.png --csv --out out/

# One file, one format
nvox scan photo.png --flag-format 'ctf{}'

# Several formats plus a custom regex, case-insensitive
nvox scan evidence/ \
  --flag-format 'htb{}' --flag-format 'pico{}' \
  --flag 'FLAG\{[A-Za-z0-9_]{8,64}\}' -i

# CI mode: JSON on stdout, exit code 2 when something is found
nvox scan challenge.bin --flag-format 'ctf{}' --json --out out/

# Skip expensive XOR brute force and external plugins
nvox scan dump.bin --flag-format 'ctf{}' --no-xor-brute --no-external
```

| Option | Description |
|---|---|
| `--flag-format`, `-f FORMAT` | Format with one `{}` placeholder, e.g. `ctf{}`; repeatable. Falls back to `[flags].formats` |
| `--flag REGEX` | Raw flag regular expression; repeatable |
| `-i`, `--ignore-case` | Case-insensitive matching |
| `--min-length N` | Minimum flag body length (default `4`) |
| `--max-length N` | Maximum flag body length (default `200`) |
| `--depth N` | Override `scan.max_depth` |
| `--timeout SECONDS` | Override the per-plugin timeout |
| `--jobs`, `-j N` | Worker count; `0` means auto |
| `--plugins NAME,...` | Restrict the run to selected plugin names |
| `--no-external` | Disable command plugins and plugins with external `requires` |
| `--no-xor-brute` | Disable single-byte XOR brute force |
| `--out`, `-o DIR` | Output directory for artifacts, `findings.json` and optional CSV (default `out/`) |
| `--json` | Print the complete report as JSON on stdout |
| `--csv` | Write `out/artifacts.csv`, one row per artifact |
| `--verbose`, `-v` | Show plugin skips and their reasons |

In artifact-only mode the terminal shows only a summary and the report paths;
the artifact list stays in `findings.json` (and `artifacts.csv` with `--csv`).
This avoids dumping thousands of metadata values into a terminal.

Input may be a single file or a directory. Directory scans are recursive; the
output directory is excluded from traversal, so `nvox scan . --out out` does not
scan its own artifacts. Unreadable or oversized inputs are recorded as skips and
do not stop the rest of the run.

Exit codes are meaningful for automation:

| Code | Meaning |
|---|---|
| `0` | Scan completed and found no flags, or artifact-only mode completed |
| `1` | Nvox configuration, format, option or I/O error |
| `2` | Flag search completed and found at least one flag |

Typer/Click itself also exits with `2` for malformed command-line usage (for
example an unknown option). Nvox validation errors raised after parsing use
`1`; CI should therefore check both the exit code and `summary.findings` when
distinguishing usage failures from findings.

### Output layout

```
out/
  artifacts/<xx>/<sha256>   # evidence, decoded layers and bitstreams
  index.jsonl               # append-only artifact metadata log
  findings.json             # run summary, evidence records and findings
  artifacts.csv             # optional, with --csv
```

`findings.json` contains the effective mode/formats/options, one entry per input
(hashes, MIME type, artifacts and skips), the deduplicated findings with
provenance chains, and a summary:

```json
{
  "nvox": "0.1.0.dev0",
  "mode": "flags",
  "formats": ["ctf{}"],
  "summary": {"files": 1, "artifacts": 42, "findings": 1, "skips": 7},
  "findings": [
    {
      "value": "lsb_1s_fun",
      "surface": "bitstream",
      "artifact": "5f0c…",
      "confidence": "medium",
      "chain": [{"plugin": "image.lsb", "params": {"channel": 1}}]
    }
  ]
}
```

With a flag format and without `--json`, the command prints a Rich table of
findings plus a summary line and the report path. In artifact-only mode it prints
only the summary and paths. With `--json`, stdout contains only the JSON report;
warnings go to stderr.

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
formats = ["ctf{}"]      # defaults used when --flag-format is absent

[plugins]
dirs = ["plugins", "~/.config/nvox/plugins"]
disable = []             # plugin names that must never run

[plugins.options."generic.strings"]
min_length = 6
utf16 = false

[plugins.options."decoder.xor"]
max_input_bytes = 16384
max_candidates = 5

[plugins.options."image.lsb"]
max_pixels = 2000000

[tools]
disable = ["steghide"]
```

Plugin option defaults are declared by the plugin; values under
`[plugins.options."<plugin-name>"]` override them. They are part of the dedup
key, so changing an option re-runs the plugin on the same artifact.

All keys are optional. Relative plugin directories are resolved against the
directory containing `nvox.toml` (or the current directory when defaults are
used). `~` is expanded.
