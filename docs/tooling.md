# Tooling setup

> **Status:** optional-tool bridges are implemented through M6. Each bridge is
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
`jphide` and `jpseek` are not packaged in the official repos, the AUR or
BlackArch and must be built manually if needed.

## BlackArch

`stegdetect` and `stegsolve` are available from the BlackArch repository, and
BlackArch also packages many additional forensic tools. Adding a third-party
repository changes the system trust configuration, so it is a separate,
explicit step:

```bash
./scripts/install-blackarch-tools.sh --dry-run   # show what would happen
./scripts/install-blackarch-tools.sh             # confirm, back up, install
./scripts/install-blackarch-tools.sh --repo-only # repository only
./scripts/install-blackarch-tools.sh --packages "stegdetect stegsolve"
```

The helper targets Arch Linux only, backs up `/etc/pacman.conf`, and uses the
official BlackArch bootstrap script so the BlackArch keyring is verified. The
upstream bootstrap script may ask whether to perform a full system upgrade.
`jphide` and `jpseek` are not in BlackArch either; build them manually from
source if a challenge requires them. Remove the `[blackarch]` section from
`/etc/pacman.conf` (or restore the printed backup) to undo the repository
configuration.

## Wordlists

`bridge.stegseek` needs a password list. Nvox does not bundle one because the
usual `rockyou.txt` corpus is large and is a leaked-password dataset. Download
it explicitly with the helper, then use it either through auto-detection or an
explicit option:

```bash
./scripts/install-wordlist.sh
./scripts/install-wordlist.sh --dry-run
./scripts/install-wordlist.sh --output /usr/share/wordlists/rockyou.txt
```

The default destination is
`~/.local/share/nvox/wordlists/rockyou.txt` (respecting `XDG_DATA_HOME`). The
script downloads the SecLists `rockyou.txt` tarball with `curl` or `wget`,
checks the extracted file, and writes it atomically. Review the source and the
applicable licence before using a leaked-password corpus.

Nvox auto-detects the helper's destination, `/usr/share/wordlists/rockyou.txt`
and the common SecLists paths. To be explicit, configure:

```toml
[plugins.options."bridge.stegseek"]
wordlist = "~/.local/share/nvox/wordlists/rockyou.txt"
```

If no wordlist is found, `bridge.stegseek` records a skip that points back to
`scripts/install-wordlist.sh`; the rest of the scan continues.

## Bridge coverage

| Bridge | Tool | Notes |
|---|---|---|
| `bridge.exiftool` | `exiftool` | Broad grouped JSON metadata |
| `bridge.pngcheck` | `pngcheck` | PNG chunk/CRC validation report |
| `bridge.zsteg` | `zsteg` | PNG/BMP LSB scan |
| `bridge.steghide` | `steghide` | `info` probe plus extraction with the optional passphrase |
| `bridge.stegseek` | `stegseek` | Requires a wordlist; auto-detects the helper path |
| `bridge.outguess` | `outguess` | JPEG extraction; optional key |
| `bridge.stegsolve` | `java` | GUI-only; needs a configured headless command |
| `bridge.binwalk` | `binwalk` | Signature report; opt-in extraction |
| `bridge.foremost` | `foremost` | Carved files |
| `bridge.zbar` | `zbarimg` | QR and barcode text |
| `bridge.tesseract` | `tesseract` | OCR text |
| `bridge.ffprobe` | `ffprobe` | JSON audio/video metadata |
| `bridge.sox` | `sox` | Audio statistics report |

`bridge.stegseek` is skipped until a readable wordlist is configured or found
at the helper's default location. Run `scripts/install-wordlist.sh` to fetch
`rockyou.txt`; the skip reason includes this hint.

## Manual installation

Any tool can be installed independently. `nvox doctor` groups tools by category
and prints the missing ones with a Nix attribute hint where one is known. A
missing tool is never fatal: dependent bridges record a `requirement` skip and
the rest of the scan continues. Installed tools can still be excluded with
`[tools].disable`, which produces a `disabled` skip instead.

## Doctor return codes

`nvox doctor` exits `0` even when tools are missing because they are optional.
A configuration error exits `1`. Use `--json` for machine-readable diagnostics.
