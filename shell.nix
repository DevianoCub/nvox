{
  pkgs ? import <nixpkgs> { },
}:

# Optional tools used by Nvox bridges and reported by `nvox doctor`.
#
# Usage:
#   nix-shell
#   uv sync --group dev
#   uv run nvox doctor
#
# `jphide`, `jpseek` and `stegdetect` are not packaged in nixpkgs. They are
# intentionally left out; `nvox doctor` will report them as missing until they
# are built manually. StegSolve is a Java jar, so the JDK is included as well.
let
  optionalTools = with pkgs; [
    # Generic / archives
    file
    binutils
    binwalk
    foremost
    p7zip
    unzip

    # Metadata / images
    exiftool
    imagemagick
    pngcheck
    jdk
    stegsolve

    # Steganography
    zsteg
    steghide
    stegseek
    outguess

    # Barcodes / OCR
    zbar
    tesseract

    # Audio / video
    ffmpeg
    sox
    mediainfo
  ];
in
pkgs.mkShell {
  packages = [
    pkgs.uv
    pkgs.python3
  ] ++ optionalTools;

  shellHook = ''
    echo "Nvox tool shell"
    echo "  uv sync --group dev"
    echo "  uv run nvox doctor"
    echo ""
    echo "Not packaged in nixpkgs: jphide, jpseek, stegdetect."
  '';
}
