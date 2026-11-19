#!/usr/bin/env bash
#
# Download a small, optional wordlist for wordlist-based CTF tools such as
# stegseek. The default source is the rockyou list distributed by SecLists.
#
# Nvox never bundles or downloads a wordlist automatically: the list is large,
# it is a leaked-password corpus, and many challenges do not need it. This
# helper makes the manual step reproducible.
#
# Usage:
#   ./scripts/install-wordlist.sh
#   ./scripts/install-wordlist.sh --dry-run
#   ./scripts/install-wordlist.sh --output /usr/share/wordlists/rockyou.txt
#
# Environment:
#   XDG_DATA_HOME  Base for the default destination
#                  (default: ~/.local/share)
#   WGET/CURL      Override the downloader executable path

set -euo pipefail

DEFAULT_URL="https://raw.githubusercontent.com/danielmiessler/SecLists/master/Passwords/Leaked-Databases/rockyou.txt.tar.gz"
DEFAULT_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/nvox/wordlists"
DRY_RUN=0
FORCE=0
URL="$DEFAULT_URL"
OUTPUT=""

usage() {
  cat <<'EOF'
Download a wordlist for stegseek and other optional tools.

Options:
  --url URL       Download from URL instead of the SecLists rockyou tarball.
  --output FILE   Destination path (default: ~/.local/share/nvox/wordlists/rockyou.txt).
  --force         Replace an existing wordlist.
  --dry-run       Print what would be downloaded without changing anything.
  -h, --help      Show this help.

The default source is a 53 MB tarball from the SecLists repository. Review the
source and licence before using a leaked-password corpus. The list is written
atomically and is never executed.
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

while (($#)); do
  case "$1" in
    --url)
      [[ $# -ge 2 ]] || die "--url requires a value"
      URL="$2"
      shift
      ;;
    --output)
      [[ $# -ge 2 ]] || die "--output requires a value"
      OUTPUT="$2"
      shift
      ;;
    --force)
      FORCE=1
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

if [[ -z "$OUTPUT" ]]; then
  OUTPUT="$DEFAULT_DIR/rockyou.txt"
fi

case "$OUTPUT" in
  "~" | "~/"*) OUTPUT="${HOME}/${OUTPUT#\~/}" ;;
esac

if [[ -e "$OUTPUT" && $FORCE -eq 0 ]]; then
  die "$OUTPUT already exists; use --force to replace it"
fi

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

is_tar_gz() {
  case "$1" in
    *.tar.gz | *.tgz) return 0 ;;
    *) return 1 ;;
  esac
}

if ((DRY_RUN)); then
  log "Would download: $URL"
  log "Would install:  $OUTPUT"
  if is_tar_gz "$URL"; then
    log "Would extract the tarball and keep its rockyou.txt member"
  fi
  log "Configure with:"
  printf '  [plugins.options."bridge.stegseek"]\n  wordlist = "%s"\n' "$OUTPUT"
  exit 0
fi

command -v tar >/dev/null 2>&1 || die "tar is required"

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/nvox-wordlist.XXXXXXXX")"
trap 'rm -rf "$tmpdir"' EXIT

archive="$tmpdir/download"
log "Downloading $URL"
download "$URL" "$archive"
[[ -s "$archive" ]] || die "downloaded file is empty"

if is_tar_gz "$URL"; then
  log "Extracting wordlist"
  tar -xzf "$archive" -C "$tmpdir"
  source_file="$tmpdir/rockyou.txt"
  if [[ ! -f "$source_file" ]]; then
    source_file="$(find "$tmpdir" -type f -name '*.txt' -size +1M -print -quit)"
  fi
  [[ -n "${source_file:-}" && -f "$source_file" ]] ||
    die "archive does not contain a usable .txt wordlist"
else
  source_file="$archive"
fi

[[ $(wc -c <"$source_file") -gt 0 ]] || die "wordlist is empty"

mkdir -p "$(dirname "$OUTPUT")"
temporary="$OUTPUT.tmp.$$"
cp "$source_file" "$temporary"
chmod 0644 "$temporary"
mv -f "$temporary" "$OUTPUT"

log "Installed $(wc -l <"$OUTPUT") lines at $OUTPUT"
printf '\nAdd this to nvox.toml if the path is not auto-detected:\n'
printf '  [plugins.options."bridge.stegseek"]\n  wordlist = "%s"\n' "$OUTPUT"
