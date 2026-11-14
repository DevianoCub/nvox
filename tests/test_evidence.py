"""Tests for evidence ingestion and media type sniffing."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from nvox.api import ArtifactKind
from nvox.core.evidence import Evidence, EvidenceError, EvidenceTooLarge, sniff_media_type


def test_evidence_hashes_and_snapshots(tmp_path: Path) -> None:
    path = tmp_path / "sample.bin"
    payload = b"hello evidence"
    path.write_bytes(payload)
    evidence = Evidence.from_path(path, max_bytes=1024)
    assert evidence.sha256 == hashlib.sha256(payload).hexdigest()
    assert evidence.size == len(payload)
    assert evidence.payload == payload
    assert evidence.to_artifact().kind is ArtifactKind.BYTES


def test_evidence_is_read_only(tmp_path: Path) -> None:
    path = tmp_path / "sample.bin"
    path.write_bytes(b"data")
    before = path.stat().st_mtime_ns
    Evidence.from_path(path, max_bytes=1024)
    assert path.read_bytes() == b"data"
    assert path.stat().st_mtime_ns == before


def test_evidence_too_large(tmp_path: Path) -> None:
    path = tmp_path / "big.bin"
    path.write_bytes(b"x" * 10)
    with pytest.raises(EvidenceTooLarge):
        Evidence.from_path(path, max_bytes=4)


def test_evidence_rejects_directories(tmp_path: Path) -> None:
    with pytest.raises(EvidenceError):
        Evidence.from_path(tmp_path, max_bytes=1024)


@pytest.mark.parametrize(
    ("name", "payload", "expected"),
    [
        ("image.png", b"\x89PNG\r\n\x1a\nrest", "image/png"),
        ("image.jpg", b"\xff\xd8\xff\xe0rest", "image/jpeg"),
        ("image.gif", b"GIF89a-rest", "image/gif"),
        ("image.bmp", b"BM" + b"\x00" * 30, "image/bmp"),
        ("doc.pdf", b"%PDF-1.7 rest", "application/pdf"),
        ("archive.zip", b"PK\x03\x04rest", "application/zip"),
        ("archive.gz", b"\x1f\x8brest", "application/gzip"),
        ("audio.wav", b"RIFF\x00\x00\x00\x00WAVErest", "audio/wav"),
        ("audio.mp3", b"ID3rest", "audio/mpeg"),
        ("sound.flac", b"fLaCrest", "audio/flac"),
        ("notes.txt", b"plain text", "text/plain"),
        ("unknown.zzz", b"random", "application/octet-stream"),
    ],
)
def test_sniff_media_type(name: str, payload: bytes, expected: str) -> None:
    assert sniff_media_type(Path(name), payload) == expected


def test_sniff_prefers_magic_over_extension(tmp_path: Path) -> None:
    path = tmp_path / "actually-png.jpg"
    payload = b"\x89PNG\r\n\x1a\n" + b"data"
    path.write_bytes(payload)
    evidence = Evidence.from_path(path, max_bytes=1024)
    assert evidence.media_type == "image/png"
    assert evidence.to_artifact().kind is ArtifactKind.IMAGE
