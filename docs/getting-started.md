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

External tools are never required. Install them system-wide or use a Nix shell
on NixOS:

```bash
nix shell nixpkgs#exiftool nixpkgs#binwalk nixpkgs#steghide \
           nixpkgs#zsteg nixpkgs#pngcheck nixpkgs#imagemagick \
           nixpkgs#ffmpeg nixpkgs#sox nixpkgs#tesseract nixpkgs#zbar
```

Then re-run `nvox doctor` to confirm detection. Note that `zsteg` also requires
Ruby, and StegSolve is a Java jar rather than a standalone binary.

Do not want a tool used even when installed? Add it to `[tools].disable`.

## What's next

M3 is implemented: `nvox scan` compiles user formats, walks the artifact graph,
analyses PNG/JPEG/GIF/BMP/TIFF structure and metadata, extracts configurable PNG
bit planes, runs the built-in decoders and reports findings with provenance. A
first run:

```bash
nvox scan photo.png --flag-format 'ctf{}'
nvox scan ./evidence --flag-format 'htb{}' --flag 'FLAG\{[^}]+\}' -i --json
```

Findings are written to `out/findings.json`; the command exits with code `2`
when it finds a flag. `nvox extract <sha256>` reproduces any artifact from the
store. Image technique coverage is in [Image analysis](image-analysis.md);
external bridges, audio and the HTML/TUI reports arrive in M4-M7. See the
[roadmap](roadmap.md) for the order of work.
