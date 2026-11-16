# Tooling setup

> **Status:** optional-tool install helpers are available now. Installing a tool
> only makes it visible to `nvox doctor`; the external-tool bridges themselves
> are implemented in M5.

Nvox works on a clean Python install. External tools add coverage where a bridge
exists, and `nvox doctor` reports which ones are present:

```bash
uv run nvox doctor
uv run nvox doctor --json
```

## NixOS / Nix

The repository ships a `shell.nix` with the optional tools available in
nixpkgs:

```bash
nix-shell
uv sync --group dev
uv run nvox doctor
```

It includes `file`, `binutils`, `binwalk`, `foremost`, `p7zip`, `unzip`,
`exiftool`, `imagemagick`, `pngcheck`, `zsteg`, `steghide`, `stegseek`,
`outguess`, `stegsolve`, the JDK, `zbar`, `tesseract`, `ffmpeg`, `sox` and
`mediainfo`.

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

## Manual installation

Any tool can be installed independently. `nvox doctor` groups tools by category
and prints the missing ones with a Nix attribute hint where one is known. A
missing tool is never fatal: dependent plugins record a skip and the rest of the
scan continues.

## Doctor return codes

`nvox doctor` exits `0` even when tools are missing because they are optional.
A configuration error exits `1`. Use `--json` for machine-readable diagnostics.
