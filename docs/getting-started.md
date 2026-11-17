# Getting started

## Requirements

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)

Nvox itself is pure Python. Optional extras add image processing, the TUI and
documentation tooling.

## Install from source

```bash
git clone <your-fork> Nvox
cd Nvox
uv sync --group dev
uv run nvox doctor
```

`uv sync` creates `.venv`, installs the runtime dependencies and installs Nvox
itself in editable mode. The `nvox` console script lives in `.venv/bin`, so
either activate the environment or prefix commands with `uv run`.

### Optional extras

```bash
uv sync --group dev --extra image   # Pillow + numpy (image analysis)
uv sync --group dev --extra tui     # Textual (interactive UI, M4)
uv sync --group dev --extra docs    # MkDocs Material
```

## First run

```bash
uv run nvox --version
uv run nvox doctor
```

`doctor` is the environment health check. It reports:

- Nvox and Python versions, interpreter path, platform and the detected `uv`;
- which `nvox.toml` was loaded, the effective limits, flag formats and plugin
  directories;
- every optional external tool, grouped by category, with its path and the
  milestone that uses it;
- how many tools are available and a ready-to-paste `nix shell` line for the
  missing ones.

Add `--verbose` to probe versions of installed tools, or `--json` for a
machine-readable report:

```bash
uv run nvox doctor --json | jq '.summary'
```

## Configuration

Nvox looks for `nvox.toml` in this order:

1. the path passed with `--config`;
2. `./nvox.toml` in the current directory;
3. `$XDG_CONFIG_HOME/nvox/nvox.toml` (usually `~/.config/nvox/nvox.toml`).

No file is fine: built-in defaults apply. Start from the annotated example:

```bash
cp nvox.toml.example nvox.toml
```

```toml
[scan]
max_depth = 4            # artifact-graph depth
timeout = 120            # per-plugin timeout, seconds
max_artifact_bytes = 67108864
jobs = 0                 # 0 = one worker per CPU

[flags]
formats = ["ctf{}"]      # "{}" matches the flag body

[plugins]
dirs = ["plugins", "~/.config/nvox/plugins"]
disable = []             # plugin names that must never run
# Per-plugin option overrides; defaults are declared by the plugin.
# [plugins.options."generic.strings"]
# min_length = 6

[tools]
disable = []             # external tools that must never be invoked
```

Flag formats are only defaults; `--flag-format` on the command line always
wins.

## Optional external tools

External tools are never required. The repository includes install helpers:

```bash
# NixOS / Nix: all tools available in nixpkgs
nix-shell

# Arch Linux: official packages plus AUR via yay/paru
./scripts/install-arch-tools.sh
```

Then re-run `nvox doctor` to confirm detection. Note that `zsteg` also requires
Ruby, StegSolve is a Java jar, and `jphide`, `jpseek` and `stegdetect` are not
packaged in nixpkgs or Arch. See [Tooling setup](tooling.md).

Do not want a tool used even when installed? Add it to `[tools].disable`.

## What's next

M4 is complete: `nvox scan` walks the artifact graph, analyses PNG/JPEG/GIF/BMP/
TIFF structure and metadata, extracts configurable PNG bit planes, runs the
built-in decoders and reports findings with provenance. With no flag format it
runs artifact-only mode and writes the full report to JSON/CSV plus a
self-contained HTML report. A first run:

```bash
nvox scan photo.png                              # artifact-only, JSON + HTML
nvox scan photo.png --csv --out out/             # plus artifacts.csv
nvox scan photo.png --flag-format 'ctf{}'
nvox scan ./evidence --flag-format 'htb{}' --flag 'FLAG\{[^}]+\}' -i --json
nvox report out/                                 # re-render HTML without rescanning
nvox tui --report out/                           # interactive artifact browser
```

The report is written to `out/findings.json` and `out/report.html`; with a flag
format the command exits with code `2` when it finds a flag. `nvox extract
<sha256>` reproduces any artifact from the store. Image technique coverage is in
[Image analysis](image-analysis.md), HTML/TUI usage is in
[HTML reports and TUI](report.md), and optional tool setup is in
[Tooling setup](tooling.md). External bridges and audio arrive in M5-M7. See the
[roadmap](roadmap.md) for the order of work.
