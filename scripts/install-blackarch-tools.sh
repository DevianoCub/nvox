#!/usr/bin/env bash
#
# Add the BlackArch repository and install tools that are not available in the
# official Arch repositories or the AUR.
#
# BlackArch is a large security-tool repository. This helper is deliberately
# separate from scripts/install-arch-tools.sh because it changes the system
# pacman configuration and trusts a third-party package repository. It refuses
# to continue without explicit confirmation, backs up /etc/pacman.conf, and
# uses the official BlackArch bootstrap script rather than reimplementing its
# keyring verification.
#
# Usage:
#   ./scripts/install-blackarch-tools.sh --dry-run
#   ./scripts/install-blackarch-tools.sh
#   ./scripts/install-blackarch-tools.sh --yes
#   ./scripts/install-blackarch-tools.sh --packages "stegdetect stegsolve"

set -euo pipefail

STRAP_URL="https://blackarch.org/strap.sh"
DEFAULT_PACKAGES=(stegdetect stegsolve)
DRY_RUN=0
ASSUME_YES=0
REPO_ONLY=0
PACKAGES=("${DEFAULT_PACKAGES[@]}")
STRAP_PATH=""

usage() {
  cat <<'EOF'
Add the BlackArch repository and install optional Nvox tools.

Options:
  --packages LIST  Space-separated BlackArch packages (default: stegdetect stegsolve).
  --repo-only      Add/update the repository but install no packages.
  --yes            Do not ask for confirmation (EXPERIMENTAL: repository trust changes).
  --dry-run        Print the actions without changing the system.
  -h, --help       Show this help.

Notes:
  * The official BlackArch strap.sh script is used to install and verify the
    keyring. It may ask whether to run a full system upgrade.
  * /etc/pacman.conf is backed up before the repository is added.
  * jphide and jpseek are not in BlackArch either; they still need a manual
    build. stegdetect and stegsolve are provided by BlackArch.
  * Remove the [blackarch] section from /etc/pacman.conf to undo the repository
    configuration. The backup path is printed by this script.
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

download() {
  local url="$1" destination="$2"
  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --retry 3 --output "$destination" "$url"
  elif command -v wget >/dev/null 2>&1; then
    wget --https-only --tries=3 --output-document="$destination" "$url"
  else
    die "curl or wget is required"
  fi
}

while (($#)); do
  case "$1" in
    --packages)
      [[ $# -ge 2 ]] || die "--packages requires a value"
      read -r -a PACKAGES <<<"$2"
      shift
      ;;
    --repo-only)
      REPO_ONLY=1
      ;;
    --yes)
      ASSUME_YES=1
      ;;
    --dry-run)
      DRY_RUN=1
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

if [[ -n "${NVOX_BLACKARCH_STRAP:-}" ]]; then
  STRAP_PATH="$NVOX_BLACKARCH_STRAP"
  [[ -r "$STRAP_PATH" ]] || die "NVOX_BLACKARCH_STRAP is not readable: $STRAP_PATH"
fi

if ((EUID == 0)); then
  SUDO=()
else
  command -v sudo >/dev/null 2>&1 || die "sudo is required (or run as root)"
  SUDO=(sudo)
fi

has_repo() {
  grep -Eq '^\[blackarch\]' /etc/pacman.conf
}

if ! has_repo; then
  warn "this will trust the third-party BlackArch package repository"
  warn "review https://blackarch.org and the upstream bootstrap script before continuing"
  if ((ASSUME_YES == 0)); then
    [[ -t 0 ]] || die "no terminal for confirmation; rerun with --yes after reviewing the script"
    printf 'Continue? [y/N] '
    read -r answer
    case "$answer" in
      [yY] | [yY][eE][sS]) ;;
      *) die "cancelled" ;;
    esac
  fi

  if ((DRY_RUN)); then
    log "Would back up /etc/pacman.conf"
    log "Would download and run $STRAP_URL"
  else
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    backup="/etc/pacman.conf.nvox.${timestamp}.bak"
    log "Backing up /etc/pacman.conf to $backup"
    run "${SUDO[@]}" cp -a /etc/pacman.conf "$backup"

    tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/nvox-blackarch.XXXXXXXX")"
    trap 'rm -rf "$tmpdir"' EXIT
    if [[ -z "$STRAP_PATH" ]]; then
      STRAP_PATH="$tmpdir/strap.sh"
      log "Downloading $STRAP_URL"
      download "$STRAP_URL" "$STRAP_PATH"
    fi
    [[ -s "$STRAP_PATH" ]] || die "BlackArch bootstrap script is empty"
    log "BlackArch bootstrap SHA-256: $(sha256sum "$STRAP_PATH" | awk '{print $1}')"
    log "Running the official BlackArch bootstrap (it may ask about a full upgrade)"
    run "${SUDO[@]}" sh "$STRAP_PATH"
  fi
else
  log "BlackArch repository is already configured"
fi

if ((REPO_ONLY)); then
  log "Repository-only mode; not installing packages"
  exit 0
fi

if ((${#PACKAGES[@]} == 0)); then
  die "--packages must contain at least one package name"
fi

log "Refreshing package databases"
run "${SUDO[@]}" pacman -Syy

log "Installing BlackArch packages: ${PACKAGES[*]}"
run "${SUDO[@]}" pacman -S --needed --noconfirm "${PACKAGES[@]}"

printf '\n'
log "Installed packages: ${PACKAGES[*]}"
warn "jphide and jpseek are not packaged in BlackArch; build them manually if needed"
if command -v nvox >/dev/null 2>&1; then
  log "Next: nvox doctor"
else
  log "Next: uv run nvox doctor"
fi
