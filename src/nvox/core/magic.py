"""Format signatures shared by carving and structure analysis.

The table is intentionally small: it covers common CTF containers and image
formats, not every file type known to ``file(1)``. Plugins can import this
module to keep detection consistent without depending on an external tool.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MagicSignature:
    """A known file signature and the metadata needed to carve it."""

    name: str
    magic: bytes
    media_type: str
    extension: str
    kind: str


@dataclass(frozen=True)
class MagicHit:
    """One signature occurrence in a byte string."""

    signature: MagicSignature
    offset: int


SIGNATURES: tuple[MagicSignature, ...] = (
    MagicSignature("png", b"\x89PNG\r\n\x1a\n", "image/png", "png", "image"),
    MagicSignature("jpeg", b"\xff\xd8\xff", "image/jpeg", "jpg", "image"),
    MagicSignature("gif87a", b"GIF87a", "image/gif", "gif", "image"),
    MagicSignature("gif89a", b"GIF89a", "image/gif", "gif", "image"),
    MagicSignature("bmp", b"BM", "image/bmp", "bmp", "image"),
    MagicSignature("tiff-le", b"II*\x00", "image/tiff", "tif", "image"),
    MagicSignature("tiff-be", b"MM\x00*", "image/tiff", "tif", "image"),
    MagicSignature("zip", b"PK\x03\x04", "application/zip", "zip", "archive"),
    MagicSignature("gzip", b"\x1f\x8b", "application/gzip", "gz", "archive"),
    MagicSignature("bzip2", b"BZh", "application/x-bzip2", "bz2", "archive"),
    MagicSignature("xz", b"\xfd7zXZ\x00", "application/x-xz", "xz", "archive"),
    MagicSignature("7z", b"7z\xbc\xaf\x27\x1c", "application/x-7z-compressed", "7z", "archive"),
    MagicSignature("rar", b"Rar!\x1a\x07", "application/vnd.rar", "rar", "archive"),
    MagicSignature("pdf", b"%PDF-", "application/pdf", "pdf", "bytes"),
    MagicSignature("elf", b"\x7fELF", "application/x-elf", "elf", "bytes"),
    MagicSignature("wav", b"RIFF", "audio/wav", "wav", "audio"),
)

IMAGE_FORMATS = frozenset(signature.name for signature in SIGNATURES if signature.kind == "image")
ARCHIVE_FORMATS = frozenset(
    signature.name for signature in SIGNATURES if signature.kind == "archive"
)


def detect(data: bytes) -> MagicSignature | None:
    """Return the signature matching the start of ``data``, if any."""
    for signature in SIGNATURES:
        if data.startswith(signature.magic):
            return signature
    return None


def find_hits(
    data: bytes,
    *,
    start: int = 0,
    max_offset: int | None = None,
    max_hits: int = 64,
) -> tuple[MagicHit, ...]:
    """Find known signatures at or after ``start``.

    ``max_offset`` caps the scan to a prefix. Results are sorted by offset and
    then by signature name so callers get deterministic artifact names.
    """
    if start < 0:
        start = 0
    end = len(data) if max_offset is None else min(len(data), max_offset)
    hits: list[MagicHit] = []
    for signature in SIGNATURES:
        position = start
        while len(hits) < max_hits:
            found = data.find(signature.magic, position, end)
            if found < 0:
                break
            hits.append(MagicHit(signature, found))
            position = found + 1
    hits.sort(key=lambda hit: (hit.offset, hit.signature.name))
    return tuple(hits[:max_hits])


def signature_for_name(name: str) -> MagicSignature | None:
    """Look up a signature by its table name."""
    for signature in SIGNATURES:
        if signature.name == name:
            return signature
    return None


__all__ = [
    "ARCHIVE_FORMATS",
    "IMAGE_FORMATS",
    "SIGNATURES",
    "MagicHit",
    "MagicSignature",
    "detect",
    "find_hits",
    "signature_for_name",
]
