#!/usr/bin/env bash
#
# Install the optional external tools used by Nvox and reported by
# `nvox doctor` on Arch Linux.
#
# Official repositories are installed with pacman. Packages that only exist in
# the AUR are installed through yay/paru (or the helper named by AUR_HELPER).
# jphide and jpseek are not packaged anywhere in Arch, AUR or BlackArch and
# remain manual tools. stegdetect and stegsolve are available from BlackArch;
# run scripts/install-blackarch-tools.sh to add that repository.
#
# Usage:
#   ./scripts/install-arch-tools.sh
#   ./scripts/install-arch-tools.sh --dry-run
#   ./scripts/install-arch-tools.sh --no-aur
#   AUR_HELPER=paru ./scripts/install-arch-tools.sh
#   ./scripts/install-blackarch-tools.sh

set -euo pipefail

DRY_RUN=0
INSTALL_AUR=1

PACMAN_PACKAGES=(
  file
  binutils
  binwalk
  foremost
  7zip
  unzip
  perl-image-exiftool
  imagemagick
  zbar
  tesseract
  ffmpeg
  sox
  mediainfo
  ruby
  jdk-openjdk
)

AUR_PACKAGES=(
  pngcheck
  zsteg
  stegseek
  steghide
  outguess
  stegsolve
)

UNPACKAGED_TOOLS=(
  jphide
  jpseek
)

usage() {
  cat <<'EOF'
Install the external tools used by Nvox on Arch Linux.

Options:
  --dry-run    Print the commands without running them.
  --no-aur     Install only packages available in the official repositories.
  -h, --help   Show this help.

Environment:
  AUR_HELPER   AUR helper to use (default: yay, then paru).
EOF
}

log() {
  printf '==> %s\n' "$*"
}

warn() {
  printf 'warning: %s\n' "$*" >&2
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

run() {
  if ((DRY_RUN)); then
    printf '  '
    printf '%q ' "$@"
    printf '\n'
  else
    "$@"
  fi
}

while (($#)); do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      ;;
    --no-aur)
      INSTALL_AUR=0
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "unknown option: $1"
      ;;
  esac
  shift
done

[[ -r /etc/arch-release ]] || die "this script is intended for Arch Linux"

if ((EUID == 0)); then
  SUDO=()
else
  command -v sudo >/dev/null 2>&1 || die "sudo is required (or run as root)"
  SUDO=(sudo)
fi

log "Installing official repository packages"
run "${SUDO[@]}" pacman -S --needed --noconfirm "${PACMAN_PACKAGES[@]}"

if ((INSTALL_AUR)); then
  if ((EUID == 0)); then
    warn "running as root; skipping AUR packages"
    warn "run this script as a normal user with sudo for the AUR step"
  else
    helper=""
    if [[ -n "${AUR_HELPER:-}" ]]; then
      helper="${AUR_HELPER}"
    elif command -v yay >/dev/null 2>&1; then
      helper="yay"
    elif command -v paru >/dev/null 2>&1; then
      helper="paru"
    fi

    if [[ -n "$helper" ]]; then
      log "Installing AUR packages with $helper"
      run "$helper" -S --needed --noconfirm "${AUR_PACKAGES[@]}"
    else
      warn "no AUR helper found; skipping: ${AUR_PACKAGES[*]}"
      warn "install yay/paru, or rerun with --no-aur to install official packages only"
    fi
  fi
else
  log "Skipping AUR packages (--no-aur)"
fi

log "Unavailable in the official repos or AUR"
printf '  %s\n' "${UNPACKAGED_TOOLS[@]}"
printf '  stegdetect and stegsolve are available from BlackArch:\n'
printf '    ./scripts/install-blackarch-tools.sh\n'
printf '  jphide and jpseek are not packaged and must be built manually.\n'

printf '\n'
if command -v nvox >/dev/null 2>&1; then
  log "Next: nvox doctor"
elif [[ -f pyproject.toml ]]; then
  log "Next: uv sync --group dev && uv run nvox doctor"
else
  log "Next: run nvox doctor from the Nvox checkout"
fi
