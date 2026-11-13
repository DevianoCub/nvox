"""Registry of optional external tools used by Nvox bridges.

Nvox never requires these binaries: every technique has a built-in Python
implementation where practical, and external tools are used as an additional,
higher-coverage pass when they are installed.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExternalTool:
    """Description of an optional command line tool."""

    name: str
    binary: str
    category: str
    purpose: str
    stage: str
    nix: str | None = None
    version_args: tuple[str, ...] = field(default=("--version",))


def _tool(
    name: str,
    binary: str,
    category: str,
    purpose: str,
    stage: str,
    nix: str | None = None,
    version_args: tuple[str, ...] = ("--version",),
) -> ExternalTool:
    return ExternalTool(
        name=name,
        binary=binary,
        category=category,
        purpose=purpose,
        stage=stage,
        nix=nix,
        version_args=version_args,
    )


TOOLS: tuple[ExternalTool, ...] = (
    # Generic / carving
    _tool("file", "file", "generic", "Content type detection", "M5", "nixpkgs#file"),
    _tool(
        "strings",
        "strings",
        "generic",
        "ASCII/UTF-16 string extraction",
        "M5",
        "nixpkgs#binutils",
    ),
    _tool(
        "binwalk",
        "binwalk",
        "generic",
        "Signature scan and embedded file carving",
        "M5",
        "nixpkgs#binwalk",
    ),
    _tool(
        "foremost",
        "foremost",
        "generic",
        "File carving from blobs",
        "M5",
        "nixpkgs#foremost",
    ),
    _tool("7z", "7z", "archives", "Archive listing and extraction", "M6", "nixpkgs#p7zip"),
    _tool("unzip", "unzip", "archives", "ZIP extraction", "M6", "nixpkgs#unzip"),
    # Metadata
    _tool(
        "exiftool",
        "exiftool",
        "metadata",
        "EXIF/XMP/IPTC/ICC metadata dump",
        "M3",
        "nixpkgs#exiftool",
        ("-ver",),
    ),
    # Images / steganography
    _tool(
        "ImageMagick",
        "identify",
        "image",
        "Image format and geometry inspection",
        "M3",
        "nixpkgs#imagemagick",
        ("-version",),
    ),
    _tool(
        "pngcheck",
        "pngcheck",
        "image",
        "PNG chunk and CRC validation",
        "M3",
        "nixpkgs#pngcheck",
    ),
    _tool(
        "zsteg",
        "zsteg",
        "stego",
        "PNG/BMP LSB steganography detection",
        "M5",
        "nixpkgs#zsteg",
    ),
    _tool(
        "steghide",
        "steghide",
        "stego",
        "JPEG/BMP/WAV/au steganography",
        "M5",
        "nixpkgs#steghide",
    ),
    _tool(
        "stegseek",
        "stegseek",
        "stego",
        "Steghide passphrase cracking",
        "M5",
        "nixpkgs#stegseek",
    ),
    _tool("outguess", "outguess", "stego", "JPEG steganography", "M5", "nixpkgs#outguess"),
    _tool("jphide", "jphide", "stego", "JPEG steganography (hide)", "M5"),
    _tool("jpseek", "jpseek", "stego", "JPEG steganography (seek)", "M5"),
    _tool(
        "stegdetect",
        "stegdetect",
        "stego",
        "Statistical stego detection for JPEG",
        "M5",
    ),
    _tool(
        "StegSolve",
        "java",
        "stego",
        "Bit-plane and channel analysis (jar tool)",
        "M5",
        "nixpkgs#jdk",
        ("-version",),
    ),
    # Barcodes / OCR
    _tool("zbar", "zbarimg", "ocr", "QR and barcode decoding", "M5", "nixpkgs#zbar"),
    _tool(
        "tesseract",
        "tesseract",
        "ocr",
        "Optical character recognition",
        "M5",
        "nixpkgs#tesseract",
        ("--version",),
    ),
    # Audio / video
    _tool(
        "ffmpeg",
        "ffmpeg",
        "audio",
        "Audio/video decoding and spectrograms",
        "M6",
        "nixpkgs#ffmpeg",
        ("-version",),
    ),
    _tool(
        "ffprobe",
        "ffprobe",
        "audio",
        "Audio/video stream inspection",
        "M6",
        "nixpkgs#ffmpeg",
        ("-version",),
    ),
    _tool(
        "sox",
        "sox",
        "audio",
        "Audio format conversion and effects",
        "M6",
        "nixpkgs#sox",
        ("--version",),
    ),
    _tool(
        "mediainfo",
        "mediainfo",
        "audio",
        "Container and stream metadata",
        "M6",
        "nixpkgs#mediainfo",
        ("--version",),
    ),
)


def probe_version(path: str, args: tuple[str, ...], *, timeout: float = 2.0) -> str | None:
    """Run ``path args`` briefly and return the first line of output.

    Returns ``None`` when the tool does not support the probe or does not
    finish in time. Version probing is best-effort and never raises.
    """
    try:
        completed = subprocess.run(
            [path, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (completed.stdout or completed.stderr or "").strip()
    if not output:
        return None
    first_line = output.splitlines()[0].strip()
    return first_line[:100] or None
