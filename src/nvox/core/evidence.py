"""Read-only evidence ingestion and media type detection."""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path

from nvox.api import Artifact, infer_kind, make_artifact


class EvidenceError(Exception):
    """Raised when an input cannot be ingested."""


class EvidenceTooLarge(EvidenceError):
    """Raised when an input exceeds the configured artifact size limit."""

    def __init__(self, path: Path, size: int, limit: int) -> None:
        self.path = path
        self.size = size
        self.limit = limit
        super().__init__(f"{path}: file is {size} bytes, limit is {limit}")


@dataclass(frozen=True)
class Evidence:
    """An immutable snapshot of an input file.

    The payload is captured once, at ingestion time; all later analysis works
    on this snapshot, so an evidence file that changes on disk mid-scan cannot
    affect the result.
    """

    path: Path
    sha256: str
    size: int
    media_type: str
    payload: bytes

    @classmethod
    def from_path(cls, path: Path, *, max_bytes: int) -> Evidence:
        """Read ``path`` read-only and capture its bytes and media type."""
        try:
            stat = path.stat()
        except OSError as exc:
            raise EvidenceError(f"cannot stat {path}: {exc}") from exc
        if not path.is_file():
            raise EvidenceError(f"not a regular file: {path}")
        if stat.st_size > max_bytes:
            raise EvidenceTooLarge(path, stat.st_size, max_bytes)
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise EvidenceError(f"cannot read {path}: {exc}") from exc
        if len(payload) > max_bytes:
            raise EvidenceTooLarge(path, len(payload), max_bytes)
        return cls(
            path=path,
            sha256=hashlib.sha256(payload).hexdigest(),
            size=len(payload),
            media_type=sniff_media_type(path, payload),
            payload=payload,
        )

    def to_artifact(self) -> Artifact:
        """Build the depth-0 artifact for this evidence."""
        return make_artifact(
            kind=infer_kind(self.media_type),
            name=self.path.name,
            payload=self.payload,
            media_type=self.media_type,
            producer="evidence",
            depth=0,
        )


_MAGIC_PREFIXES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"%PDF-", "application/pdf"),
    (b"PK\x03\x04", "application/zip"),
    (b"PK\x05\x06", "application/zip"),
    (b"PK\x07\x08", "application/zip"),
    (b"\x1f\x8b", "application/gzip"),
    (b"BZh", "application/x-bzip2"),
    (b"\xfd7zXZ\x00", "application/x-xz"),
    (b"7z\xbc\xaf\x27\x1c", "application/x-7z-compressed"),
    (b"Rar!\x1a\x07", "application/vnd.rar"),
    (b"ID3", "audio/mpeg"),
    (b"fLaC", "audio/flac"),
    (b"OggS", "audio/ogg"),
    (b"\x7fELF", "application/x-elf"),
    (b"MZ", "application/x-dosexec"),
    (b"SQLite format 3\x00", "application/vnd.sqlite3"),
)


def sniff_media_type(path: Path, payload: bytes) -> str:
    """Detect a media type from magic bytes, falling back to the extension."""
    head = payload[:4096]
    if head.startswith(b"BM") and len(head) >= 26:
        return "image/bmp"
    for magic, media_type in _MAGIC_PREFIXES:
        if head.startswith(magic):
            return media_type
    if head.startswith(b"RIFF") and len(head) >= 12:
        if head[8:12] == b"WAVE":
            return "audio/wav"
        if head[8:12] == b"AVI ":
            return "video/x-msvideo"
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed is not None:
        return guessed
    return "application/octet-stream"


__all__ = [
    "Evidence",
    "EvidenceError",
    "EvidenceTooLarge",
    "sniff_media_type",
]
