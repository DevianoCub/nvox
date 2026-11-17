# Tooling setup

> **Status:** optional-tool bridges are implemented through M5. Each bridge is
> a normal plugin with a binary in `requires`; a missing tool is recorded as a
> requirement skip and never aborts a scan.

Nvox works on a clean Python install. External tools add coverage where a bridge
exists, and `nvox doctor` reports which ones are present and which bridge uses
them:

```bash
uv run nvox doctor
uv run nvox doctor --json
```

## NixOS / Nix

The repository ships both a `shell.nix` and a `flake.nix` with the optional
tools available in nixpkgs. The flake is the reproducible entry point:

```bash
nix develop
uv sync --group dev
uv run nvox doctor
```

The older `nix-shell` workflow still works:

```bash
nix-shell
uv sync --group dev
uv run nvox doctor
```

The devshell includes `file`, `binutils`, `binwalk`, `foremost`, `p7zip`,
`unzip`, `exiftool`, `imagemagick`, `pngcheck`, `zsteg`, `steghide`,
`stegseek`, `outguess`, `stegsolve` (Linux), the JDK, `zbar`, `tesseract`,
`ffmpeg`, `sox` and `mediainfo`.

Not packaged in nixpkgs: `jphide`, `jpseek` and `stegdetect`. `nvox doctor`
will report them as missing unless they are built manually.

## Arch Linux

The helper installs everything available from the official repositories and,
when an AUR helper is present, the rest:

```bash
./scripts/install-arch-tools.sh
```

Useful options:

```bash
./scripts/install-arch-tools.sh --dry-run     # show the commands
./scripts/install-arch-tools.sh --no-aur      # official repositories only
AUR_HELPER=paru ./scripts/install-arch-tools.sh
```

Official packages include `file`, `binutils`, `binwalk`, `foremost`, `7zip`,
`unzip`, `perl-image-exiftool`, `imagemagick`, `zbar`, `tesseract`, `ffmpeg`,
`sox`, `mediainfo`, `ruby` and `jdk-openjdk`. AUR packages are `pngcheck`,
`zsteg`, `stegseek`, `steghide`, `outguess` and `stegsolve`.

If no AUR helper is installed, the script installs the official packages and
prints the AUR package list; it does not bootstrap an AUR helper itself.
`jphide`, `jpseek` and `stegdetect` are not available from the official repos or
the AUR and must be built manually if needed.

## Bridge coverage

| Bridge | Tool | Notes |
|---|---|---|
| `bridge.exiftool` | `exiftool` | Broad grouped JSON metadata |
| `bridge.pngcheck` | `pngcheck` | PNG chunk/CRC validation report |
| `bridge.zsteg` | `zsteg` | PNG/BMP LSB scan |
| `bridge.steghide` | `steghide` | `info` probe plus extraction with the optional passphrase |
| `bridge.stegseek` | `stegseek` | Requires a wordlist in plugin options |
| `bridge.outguess` | `outguess` | JPEG extraction; optional key |
| `bridge.stegsolve` | `java` | GUI-only; needs a configured headless command |
| `bridge.binwalk` | `binwalk` | Signature report; opt-in extraction |
| `bridge.foremost` | `foremost` | Carved files |
| `bridge.zbar` | `zbarimg` | QR and barcode text |
| `bridge.tesseract` | `tesseract` | OCR text |

`bridge.stegseek` is skipped until `[plugins.options."bridge.stegseek"].wordlist`
points at a readable wordlist. StegSolve cannot be driven interactively from a
scan; set `[plugins.options."bridge.stegsolve"].command` to a headless wrapper or
use the GUI manually. See [Plugins](plugins.md) for all bridge options.

## Manual installation

Any tool can be installed independently. `nvox doctor` groups tools by category
and prints the missing ones with a Nix attribute hint where one is known. A
missing tool is never fatal: dependent bridges record a `requirement` skip and
the rest of the scan continues. Installed tools can still be excluded with
`[tools].disable`, which produces a `disabled` skip instead.

## Doctor return codes

`nvox doctor` exits `0` even when tools are missing because they are optional.
A configuration error exits `1`. Use `--json` for machine-readable diagnostics.
