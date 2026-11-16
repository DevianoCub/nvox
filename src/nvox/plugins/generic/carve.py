"""Carve embedded containers using magic bytes and bounded format end markers."""

from __future__ import annotations

import bz2
import lzma
import zlib
from typing import ClassVar

from nvox.api import Analyzer, Artifact, ArtifactKind, Context, PluginKind, register
from nvox.core.magic import MagicHit, find_hits
from nvox.plugins.image.bmp import bmp_end
from nvox.plugins.image.gif import gif_end
from nvox.plugins.image.jpeg import jpeg_end
from nvox.plugins.image.png import png_end


def zip_end(data: bytes) -> int | None:
    """Return the end of a ZIP archive using its EOCD record."""
    position = 0
    while True:
        position = data.find(b"PK\x05\x06", position)
        if position < 0:
            return None
        if position + 22 <= len(data):
            comment = int.from_bytes(data[position + 20 : position + 22], "little")
            end = position + 22 + comment
            if end <= len(data):
                return end
        position += 1


def pdf_end(data: bytes) -> int | None:
    """Return the end offset after the first PDF EOF marker."""
    position = data.find(b"%%EOF", 1)
    if position < 0:
        return None
    return position + 5


def wav_end(data: bytes) -> int | None:
    """Return the declared RIFF/WAVE end, if plausible."""
    if len(data) < 12 or not data.startswith(b"RIFF") or data[8:12] != b"WAVE":
        return None
    size = int.from_bytes(data[4:8], "little") + 8
    return size if 12 <= size <= len(data) else None


def compressed_end(data: bytes, *, max_output: int = 64 * 1024 * 1024) -> int | None:
    """Return the end of a gzip/bzip2/xz stream when one is complete."""
    try:
        if data.startswith(b"\x1f\x8b"):
            decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
            decompressor.decompress(data, max_output)
            if decompressor.eof:
                return len(data) - len(decompressor.unused_data)
        elif data.startswith(b"BZh"):
            bz = bz2.BZ2Decompressor()
            bz.decompress(data, max_length=max_output)
            if bz.eof:
                return len(data) - len(bz.unused_data)
        elif data.startswith(b"\xfd7zXZ\x00"):
            xz = lzma.LZMADecompressor()
            xz.decompress(data, max_length=max_output)
            if xz.eof:
                return len(data) - len(xz.unused_data)
    except (OSError, EOFError, lzma.LZMAError, zlib.error):
        return None
    return None


def _end_for(hit: MagicHit, data: bytes) -> int | None:
    name = hit.signature.name
    if name == "png":
        return png_end(data)
    if name == "jpeg":
        return jpeg_end(data)
    if name in {"gif87a", "gif89a"}:
        return gif_end(data)
    if name == "bmp":
        return bmp_end(data)
    if name == "zip":
        return zip_end(data)
    if name in {"gzip", "bzip2", "xz"}:
        return compressed_end(data)
    if name == "pdf":
        return pdf_end(data)
    if name == "wav":
        return wav_end(data)
    return None


@register
class EmbeddedCarver(Analyzer):
    """Find signatures inside an artifact and emit complete containers."""

    name: ClassVar[str] = "generic.carve"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("*/*",)
    priority: ClassVar[int] = 92
    description: ClassVar[str] = "Carve complete PNG/JPEG/GIF/BMP/ZIP/PDF/compressed containers."
    options: ClassVar[dict[str, object]] = {
        "max_input_bytes": 16_000_000,
        "max_scan_offset": 1_000_000,
        "max_hits": 32,
        "max_carved": 16,
    }

    def accepts(self, artifact: Artifact) -> bool:
        if artifact.kind in (ArtifactKind.TEXT, ArtifactKind.META):
            return False
        return super().accepts(artifact)

    def run(self, artifact: Artifact, ctx: Context) -> None:
        data = artifact.payload
        max_input = max(1, int(ctx.option("max_input_bytes", 16_000_000)))
        if len(data) > max_input:
            ctx.skip(f"carve: input exceeds max_input_bytes ({max_input})")
        max_offset = max(1, int(ctx.option("max_scan_offset", 1_000_000)))
        max_hits = max(1, int(ctx.option("max_hits", 32)))
        max_carved = max(1, int(ctx.option("max_carved", 16)))
        start = 1 if artifact.depth == 0 else 0
        hits = find_hits(
            data,
            start=start,
            max_offset=min(len(data), max_offset),
            max_hits=max_hits,
        )
        carved = 0
        seen: set[tuple[int, str]] = set()
        for hit in hits:
            if carved >= max_carved:
                break
            if (hit.offset, hit.signature.name) in seen:
                continue
            seen.add((hit.offset, hit.signature.name))
            end = _end_for(hit, data[hit.offset :])
            if end is None or end <= 0:
                continue
            chunk = data[hit.offset : hit.offset + end]
            if len(chunk) > ctx.max_artifact_bytes:
                continue
            name = f"carved/{hit.offset:08x}.{hit.signature.extension}"
            params = {
                "format": hit.signature.name,
                "offset": hit.offset,
                "size": len(chunk),
            }
            if hit.signature.kind == "image":
                ctx.image(
                    name,
                    chunk,
                    media_type=hit.signature.media_type,
                    **params,
                )
            elif hit.signature.kind == "archive":
                ctx.archive(
                    name,
                    chunk,
                    media_type=hit.signature.media_type,
                    **params,
                )
            elif hit.signature.kind == "audio":
                ctx.audio(
                    name,
                    chunk,
                    media_type=hit.signature.media_type,
                    **params,
                )
            else:
                ctx.blob(
                    name,
                    chunk,
                    media_type=hit.signature.media_type,
                    **params,
                )
            carved += 1
        if not carved:
            ctx.skip("carve: no complete embedded container found")


__all__ = [
    "EmbeddedCarver",
    "compressed_end",
    "pdf_end",
    "wav_end",
    "zip_end",
]
