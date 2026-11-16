"""GIF block and extension parsing."""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from nvox.api import Analyzer, Artifact, Context, PluginKind, register

_MAX_BLOCKS = 4096
_MAX_BLOCK_BYTES = 1_000_000


class GifError(ValueError):
    """Raised when a GIF block stream is malformed."""


def gif_end(payload: bytes) -> int | None:
    """Return the offset just after the GIF trailer, if present."""
    try:
        end = parse_gif(payload).get("end_offset")
    except GifError:
        return None
    return end if isinstance(end, int) else None


def parse_gif(payload: bytes, *, max_text_bytes: int = 1_000_000) -> dict[str, object]:
    """Parse a GIF stream without decoding LZW image data."""
    if len(payload) < 13:
        raise GifError("truncated GIF header")
    header = payload[:6]
    if header not in {b"GIF87a", b"GIF89a"}:
        raise GifError("invalid GIF signature")
    width = int.from_bytes(payload[6:8], "little")
    height = int.from_bytes(payload[8:10], "little")
    packed = payload[10]
    background = payload[11]
    aspect = payload[12]
    global_entries = 2 ** ((packed & 0x07) + 1) if packed & 0x80 else 0
    offset = 13 + global_entries * 3
    if offset > len(payload):
        raise GifError("truncated global colour table")

    summary: dict[str, object] = {
        "version": header.decode("ascii"),
        "width": width,
        "height": height,
        "background": background,
        "aspect": aspect,
        "global_palette_entries": global_entries,
        "frames": 0,
        "comments": [],
        "applications": [],
        "plain_text": [],
        "extensions": [],
    }
    frames = 0
    blocks = 0
    while offset < len(payload):
        blocks += 1
        if blocks > _MAX_BLOCKS:
            raise GifError(f"GIF has more than {_MAX_BLOCKS} blocks")
        marker = payload[offset]
        offset += 1
        if marker == 0x3B:
            summary["frames"] = frames
            summary["end_offset"] = offset
            return summary
        if marker == 0x2C:
            if offset + 9 > len(payload):
                raise GifError("truncated GIF image descriptor")
            frame_packed = payload[offset + 8]
            offset += 9
            if frame_packed & 0x80:
                offset += 3 * (2 ** ((frame_packed & 0x07) + 1))
                if offset > len(payload):
                    raise GifError("truncated local colour table")
            if offset >= len(payload):
                raise GifError("missing LZW minimum code size")
            offset += 1
            _, offset = _sub_blocks(payload, offset, max_text_bytes)
            frames += 1
            continue
        if marker != 0x21:
            raise GifError(f"invalid GIF block marker 0x{marker:02X}")
        if offset >= len(payload):
            raise GifError("truncated GIF extension")
        label = payload[offset]
        offset += 1
        if label == 0xFE:
            data, offset = _sub_blocks(payload, offset, max_text_bytes)
            comments = summary["comments"]
            if isinstance(comments, list):
                comments.append(_decode_text(data))
        elif label == 0xFF:
            if offset >= len(payload):
                raise GifError("truncated GIF application block size")
            block_size = payload[offset]
            offset += 1
            if block_size != 11 or offset + block_size > len(payload):
                raise GifError("invalid GIF application identifier block")
            application = payload[offset : offset + block_size]
            offset += block_size
            data, offset = _sub_blocks(payload, offset, max_text_bytes)
            apps = summary["applications"]
            if isinstance(apps, list):
                apps.append(
                    {
                        "identifier": _decode_text(application).rstrip(),
                        "text": _decode_text(data),
                        "length": len(data),
                    }
                )
        elif label == 0x01:
            if offset >= len(payload) or payload[offset] != 12:
                raise GifError("invalid GIF plain-text block size")
            offset += 1
            if offset + 12 > len(payload):
                raise GifError("truncated GIF plain-text header")
            header_data = payload[offset : offset + 12]
            offset += 12
            data, offset = _sub_blocks(payload, offset, max_text_bytes)
            texts = summary["plain_text"]
            if isinstance(texts, list):
                texts.append(
                    {
                        "grid": list(header_data[:8]),
                        "cell": list(header_data[8:12]),
                        "text": _decode_text(data),
                    }
                )
        elif label == 0xF9:
            data, offset = _sub_blocks(payload, offset, max_text_bytes)
            extensions = summary["extensions"]
            if isinstance(extensions, list) and len(data) >= 4:
                extensions.append(
                    {
                        "label": "graphic-control",
                        "disposal": (data[0] >> 2) & 0x07,
                        "transparent": bool(data[0] & 1),
                        "delay": int.from_bytes(data[1:3], "little"),
                        "transparent_index": data[3],
                    }
                )
        else:
            _, offset = _sub_blocks(payload, offset, max_text_bytes)
    return summary


def _sub_blocks(payload: bytes, offset: int, max_bytes: int) -> tuple[bytes, int]:
    output = bytearray()
    while offset < len(payload):
        size = payload[offset]
        offset += 1
        if size == 0:
            return bytes(output), offset
        if offset + size > len(payload):
            raise GifError("truncated GIF sub-block")
        if len(output) < max_bytes:
            output.extend(payload[offset : offset + min(size, max_bytes - len(output))])
        offset += size
    raise GifError("unterminated GIF sub-block")


def gif_texts(metadata: dict[str, object]) -> Iterable[tuple[str, str]]:
    """Yield textual GIF extension values."""
    comments = metadata.get("comments")
    if isinstance(comments, list):
        for index, comment in enumerate(comments):
            if isinstance(comment, str) and comment:
                yield f"comment/{index}", comment
    applications = metadata.get("applications")
    if isinstance(applications, list):
        for index, application in enumerate(applications):
            if isinstance(application, dict):
                text = application.get("text")
                if isinstance(text, str) and text:
                    yield f"application/{index}", text
    texts = metadata.get("plain_text")
    if isinstance(texts, list):
        for index, entry in enumerate(texts):
            if isinstance(entry, dict):
                text = entry.get("text")
                if isinstance(text, str) and text:
                    yield f"plain-text/{index}", text


def _decode_text(data: bytes) -> str:
    for codec in ("utf-8", "latin-1"):
        try:
            return data.decode(codec)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


@register
class GifMetadataPlugin(Analyzer):
    """Parse GIF comments, application/plain-text extensions and palettes."""

    name: ClassVar[str] = "image.gif"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("image/gif",)
    priority: ClassVar[int] = 82
    description: ClassVar[str] = "Parse GIF extensions, comments, frames and palette metadata."
    options: ClassVar[dict[str, object]] = {"max_text_bytes": 1_000_000}

    def run(self, artifact: Artifact, ctx: Context) -> None:
        max_text = max(1, int(ctx.option("max_text_bytes", 1_000_000)))
        try:
            metadata = parse_gif(artifact.payload, max_text_bytes=max_text)
        except GifError as exc:
            ctx.skip(f"gif: {exc}")
        ctx.meta("gif/metadata", metadata, source="gif")
        for name, value in gif_texts(metadata):
            ctx.text(f"gif/{name}", value, source="gif")


__all__ = [
    "GifError",
    "GifMetadataPlugin",
    "gif_end",
    "gif_texts",
    "parse_gif",
]
