"""Shared helpers for built-in decoder plugins.

Decoders are ordinary plugins: they emit artifacts, the engine stores them and
the flag scanner searches them. Keeping decoding in the graph means a nested
chain such as ``base64 -> zlib -> flag`` is visible in provenance instead of
being hidden inside a monolithic scanner.
"""

from __future__ import annotations

from nvox.api import Artifact, ArtifactKind, Context, Decoder, PluginKind

_TEXT_BYTES = frozenset(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}
_MAGIC_PREFIXES = (
    b"\x1f\x8b",  # gzip
    b"BZh",  # bzip2
    b"\xfd7zXZ\x00",  # xz
    b"PK\x03\x04",  # zip
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",  # JPEG
    b"%PDF-",
    b"\x7fELF",
    b"7z\xbc\xaf\x27\x1c",
    b"Rar!\x1a\x07",
)


def clean_text(value: str) -> str:
    """Strip NUL padding and surrounding whitespace from a candidate."""
    return value.strip().strip("\x00").strip()


def printable_ratio(data: bytes) -> float:
    if not data:
        return 0.0
    printable = sum(1 for byte in data if byte in _TEXT_BYTES)
    return printable / len(data)


def looks_like_text(data: bytes) -> bool:
    return bool(data) and printable_ratio(data) >= 0.85


def has_known_magic(data: bytes) -> bool:
    if any(data.startswith(prefix) for prefix in _MAGIC_PREFIXES):
        return True
    return bool(len(data) >= 2 and data[0] == 0x78 and ((data[0] << 8) | data[1]) % 31 == 0)


def looks_meaningful(data: bytes) -> bool:
    """Whether decoded output is worth feeding back into the graph."""
    return looks_like_text(data) or has_known_magic(data)


def emit_decoded(
    ctx: Context,
    name: str,
    data: bytes,
    *,
    codec: str,
    **params: object,
) -> None:
    """Emit decoded bytes as text when printable, otherwise as a blob."""
    if not data:
        ctx.skip(f"{codec}: decoded payload is empty")
    if len(data) > ctx.max_artifact_bytes:
        ctx.skip(f"{codec}: decoded payload exceeds max_artifact_bytes")
    if looks_like_text(data):
        ctx.text(name, data.decode("utf-8", errors="replace"), codec=codec, **params)
    else:
        ctx.blob(name, data, media_type="application/octet-stream", codec=codec, **params)


class BaseDecoder(Decoder):
    """Decoder that accepts text, metadata and generic byte artifacts."""

    kind = PluginKind.DECODER
    targets = ("text/*", "application/json", "application/octet-stream")
    priority = 30

    def accepts(self, artifact: Artifact) -> bool:
        return artifact.kind in (ArtifactKind.TEXT, ArtifactKind.BYTES, ArtifactKind.META) and (
            super().accepts(artifact)
        )


__all__ = [
    "BaseDecoder",
    "clean_text",
    "emit_decoded",
    "has_known_magic",
    "looks_like_text",
    "looks_meaningful",
    "printable_ratio",
]
