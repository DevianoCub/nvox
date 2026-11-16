"""Trailing, prepended and polyglot structure analysis for image containers."""

from __future__ import annotations

from typing import ClassVar

from nvox.api import Analyzer, Artifact, Context, PluginKind, register
from nvox.core.magic import find_hits
from nvox.plugins.image.bmp import bmp_end
from nvox.plugins.image.gif import gif_end
from nvox.plugins.image.jpeg import jpeg_end
from nvox.plugins.image.png import png_end

_IMAGE_SIGNATURES = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/bmp": (b"BM",),
    "image/x-ms-bmp": (b"BM",),
    "image/tiff": (b"II*\x00", b"MM\x00*"),
}


def find_trailing(payload: bytes, media_type: str) -> int | None:
    """Return the logical end offset for a supported image container."""
    base = media_type.split(";", 1)[0].strip().lower()
    if base == "image/png":
        return png_end(payload)
    if base == "image/jpeg":
        return jpeg_end(payload)
    if base == "image/gif":
        return gif_end(payload)
    if base in {"image/bmp", "image/x-ms-bmp"}:
        return bmp_end(payload)
    return None


def find_prepended(payload: bytes, media_type: str, *, max_offset: int = 1_048_576) -> int | None:
    """Return the offset of the expected signature when bytes precede it."""
    base = media_type.split(";", 1)[0].strip().lower()
    signatures = _IMAGE_SIGNATURES.get(base)
    if signatures is None:
        return None
    best: int | None = None
    limit = min(len(payload), max_offset)
    for signature in signatures:
        found = payload.find(signature, 0, limit)
        if found > 0 and (best is None or found < best):
            best = found
    return best


def _hits(data: bytes) -> list[dict[str, object]]:
    return [
        {
            "format": hit.signature.name,
            "offset": hit.offset,
            "media_type": hit.signature.media_type,
        }
        for hit in find_hits(data, max_hits=16)
    ]


@register
class ImageStructurePlugin(Analyzer):
    """Report trailing/prepended bytes and likely polyglots."""

    name: ClassVar[str] = "image.structure"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = (
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/bmp",
        "image/x-ms-bmp",
        "image/tiff",
    )
    priority: ClassVar[int] = 95
    description: ClassVar[str] = "Detect trailing/prepended bytes and polyglot containers."
    options: ClassVar[dict[str, object]] = {"min_bytes": 1, "max_prepend_scan": 1_048_576}

    def run(self, artifact: Artifact, ctx: Context) -> None:
        payload = artifact.payload
        media_type = artifact.media_type
        end = find_trailing(payload, media_type)
        trailing = b"" if end is None else payload[end:]
        prepend_offset = find_prepended(
            payload,
            media_type,
            max_offset=max(1, int(ctx.option("max_prepend_scan", 1_048_576))),
        )
        prepended = b"" if prepend_offset is None else payload[:prepend_offset]
        min_bytes = max(1, int(ctx.option("min_bytes", 1)))
        if len(trailing) < min_bytes:
            trailing = b""
        if len(prepended) < min_bytes:
            prepended = b""
        if not trailing and not prepended:
            ctx.skip("structure: no trailing or prepended data")

        formats: list[str] = []
        summary: dict[str, object] = {}
        if trailing:
            hits = _hits(trailing)
            summary["trailing"] = {
                "offset": len(payload) - len(trailing),
                "size": len(trailing),
                "magic": hits,
            }
            formats.extend(str(hit["format"]) for hit in hits)
            detected = _first_image_type(trailing)
            ctx.blob(
                "structure/trailing",
                trailing,
                media_type=detected or "application/octet-stream",
                offset=len(payload) - len(trailing),
            )
        if prepended:
            hits = _hits(prepended)
            summary["prepended"] = {
                "offset": prepend_offset,
                "size": len(prepended),
                "magic": hits,
            }
            formats.extend(f"prepended:{hit['format']}" for hit in hits)
            ctx.blob(
                "structure/prepended",
                prepended,
                media_type="application/octet-stream",
                offset=0,
            )
        summary["formats"] = formats
        summary["polyglot"] = bool(formats)
        ctx.meta("image/structure", summary, source="structure")


def _first_image_type(data: bytes) -> str | None:
    for hit in find_hits(data, max_hits=16):
        if hit.signature.kind == "image":
            return hit.signature.media_type
    return None


__all__ = ["ImageStructurePlugin", "find_prepended", "find_trailing"]
