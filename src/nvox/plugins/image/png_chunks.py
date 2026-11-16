"""PNG chunk, text and CRC analysis."""

from __future__ import annotations

import zlib
from typing import Any, ClassVar

from nvox.api import Analyzer, Artifact, Context, PluginKind, register
from nvox.plugins.image.exif import ExifError, parse_tiff_strings
from nvox.plugins.image.jpeg import JpegError, parse_icc_profile
from nvox.plugins.image.png import PngChunk, PngError, png_end, walk_png

_KNOWN_CHUNKS = frozenset(
    {
        b"IHDR",
        b"PLTE",
        b"IDAT",
        b"IEND",
        b"tEXt",
        b"zTXt",
        b"iTXt",
        b"eXIf",
        b"gAMA",
        b"pHYs",
        b"tIME",
        b"sRGB",
        b"cHRM",
        b"iCCP",
        b"tRNS",
    }
)


def _decode_text(data: bytes) -> str:
    for codec in ("utf-8", "latin-1"):
        try:
            return data.decode(codec)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def _keyword_name(keyword: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in "-_." else "_" for character in keyword
    )
    return safe[:128] or "unnamed"


def parse_text_chunk(chunk: PngChunk, *, max_text_bytes: int) -> tuple[str, str, str]:
    """Return ``(kind, keyword, text)`` for a PNG text chunk.

    Raises ``ValueError`` for malformed chunks. zTXt/iTXt output is capped so a
    decompression bomb cannot consume unbounded memory.
    """
    data = chunk.data
    if chunk.type == b"tEXt":
        keyword_raw, separator, value_raw = data.partition(b"\x00")
        if not separator:
            raise ValueError("tEXt has no NUL separator")
        return "tEXt", _decode_text(keyword_raw), _decode_text(value_raw[:max_text_bytes])
    if chunk.type == b"zTXt":
        keyword_raw, separator, rest = data.partition(b"\x00")
        if not separator or len(rest) < 1:
            raise ValueError("malformed zTXt")
        if rest[0] != 0:
            raise ValueError(f"unsupported zTXt compression method {rest[0]}")
        try:
            decompressor = zlib.decompressobj()
            value = decompressor.decompress(rest[1:], max_text_bytes + 1)
        except zlib.error as exc:
            raise ValueError(f"corrupt zTXt stream: {exc}") from exc
        if len(value) > max_text_bytes:
            raise ValueError("zTXt text exceeds max_text_bytes")
        return "zTXt", _decode_text(keyword_raw), _decode_text(value)
    if chunk.type == b"iTXt":
        keyword_raw, separator, rest = data.partition(b"\x00")
        if not separator or len(rest) < 2:
            raise ValueError("malformed iTXt")
        compressed = rest[0]
        method = rest[1]
        rest = rest[2:]
        _language_raw, separator, rest = rest.partition(b"\x00")
        if not separator:
            raise ValueError("iTXt has no language separator")
        _translated_raw, separator, value_raw = rest.partition(b"\x00")
        if not separator:
            raise ValueError("iTXt has no translated-keyword separator")
        if compressed:
            if method != 0:
                raise ValueError(f"unsupported iTXt compression method {method}")
            try:
                decompressor = zlib.decompressobj()
                value_raw = decompressor.decompress(value_raw, max_text_bytes + 1)
            except zlib.error as exc:
                raise ValueError(f"corrupt iTXt stream: {exc}") from exc
            if len(value_raw) > max_text_bytes:
                raise ValueError("iTXt text exceeds max_text_bytes")
        return (
            "iTXt",
            _decode_text(keyword_raw),
            _decode_text(value_raw[:max_text_bytes]),
        )
    raise ValueError(f"not a text chunk: {chunk.type!r}")


@register
class PngChunksPlugin(Analyzer):
    """Inspect PNG chunks, text fields, CRCs and basic palette data."""

    name: ClassVar[str] = "image.png_chunks"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("image/png",)
    priority: ClassVar[int] = 85
    description: ClassVar[str] = "Parse PNG chunks, tEXt/zTXt/iTXt, CRCs and palette data."
    options: ClassVar[dict[str, object]] = {
        "max_text_bytes": 1_000_000,
        "max_chunks_in_meta": 256,
        "emit_unknown_chunks": True,
        "max_chunk_bytes": 1_000_000,
    }

    def run(self, artifact: Artifact, ctx: Context) -> None:
        try:
            chunks = walk_png(artifact.payload)
        except PngError as exc:
            ctx.skip(f"png chunks: {exc}")
        max_text = max(1, int(ctx.option("max_text_bytes", 1_000_000)))
        max_chunks = max(1, int(ctx.option("max_chunks_in_meta", 256)))
        emit_unknown = bool(ctx.option("emit_unknown_chunks", True))
        max_chunk_bytes = max(1, int(ctx.option("max_chunk_bytes", 1_000_000)))

        summary: dict[str, Any] = {
            "chunks": [],
            "crc_errors": [],
            "text": {},
            "errors": [],
            "unknown_chunks": [],
        }
        listing: list[dict[str, object]] = []
        for chunk in chunks:
            name = chunk.type.decode("ascii", errors="replace")
            if len(listing) < max_chunks:
                listing.append(
                    {
                        "type": name,
                        "length": chunk.length,
                        "offset": chunk.offset,
                        "crc_ok": chunk.crc_ok,
                    }
                )
            if not chunk.crc_ok:
                summary["crc_errors"].append(  # type: ignore[union-attr]
                    {"type": name, "offset": chunk.offset}
                )
            if chunk.type == b"IHDR" and len(chunk.data) == 13:
                width, height, depth, color_type, compression, filtering, interlace = (
                    chunk.data[0:4],
                    chunk.data[4:8],
                    chunk.data[8],
                    chunk.data[9],
                    chunk.data[10],
                    chunk.data[11],
                    chunk.data[12],
                )
                summary.update(
                    {
                        "width": int.from_bytes(width, "big"),
                        "height": int.from_bytes(height, "big"),
                        "bit_depth": depth,
                        "color_type": color_type,
                        "compression": compression,
                        "filtering": filtering,
                        "interlace": interlace,
                    }
                )
            elif chunk.type == b"PLTE":
                if len(chunk.data) % 3:
                    summary["errors"].append("PLTE length is not a multiple of three")  # type: ignore[union-attr]
                entries = [chunk.data[index : index + 3] for index in range(0, len(chunk.data), 3)]
                unique = len(set(entries))
                summary["palette"] = {
                    "entries": len(entries),
                    "unique_entries": unique,
                    "duplicate_entries": len(entries) - unique,
                }
            elif chunk.type == b"tRNS":
                summary["transparency"] = {"bytes": chunk.length, "values": list(chunk.data[:256])}
            elif chunk.type in {b"tEXt", b"zTXt", b"iTXt"}:
                try:
                    kind, keyword, value = parse_text_chunk(chunk, max_text_bytes=max_text)
                except ValueError as exc:
                    summary["errors"].append(f"{name}: {exc}")  # type: ignore[union-attr]
                    continue
                texts = summary["text"]
                if isinstance(texts, dict):
                    texts[f"{kind}:{keyword}"] = value
                ctx.text(
                    f"png/text/{_keyword_name(keyword)}",
                    value,
                    chunk=kind,
                    keyword=keyword,
                    offset=chunk.offset,
                )
            elif chunk.type == b"eXIf":
                try:
                    values = parse_tiff_strings(chunk.data)
                except ExifError as exc:
                    summary["errors"].append(f"eXIf: {exc}")  # type: ignore[union-attr]
                else:
                    if values:
                        summary["exif"] = values
                        for tag, value in values.items():
                            ctx.text(f"png/exif/{tag}", value, tag=tag, source="eXIf")
            elif chunk.type == b"iCCP":
                try:
                    keyword, profile = _parse_iccp(chunk, max_text_bytes=max_text)
                except (ValueError, zlib.error, JpegError) as exc:
                    summary["errors"].append(f"iCCP: {exc}")
                else:
                    summary["icc"] = profile
                    for key in ("description", "copyright"):
                        profile_value = profile.get(key)
                        if isinstance(profile_value, str) and profile_value:
                            ctx.text(
                                f"png/icc/{key}",
                                profile_value,
                                keyword=keyword,
                                source="iCCP",
                            )
            elif chunk.type in {b"gAMA", b"pHYs", b"tIME", b"sRGB", b"cHRM"}:
                summary[name] = _describe_auxiliary_chunk(chunk)
            elif chunk.type not in _KNOWN_CHUNKS and emit_unknown:
                summary["unknown_chunks"].append({"type": name, "length": chunk.length})
                if 0 < chunk.length <= max_chunk_bytes:
                    ctx.blob(
                        f"png/chunk/{_keyword_name(name)}",
                        chunk.data,
                        chunk=name,
                        offset=chunk.offset,
                    )
        summary["chunks"] = listing
        end = png_end(artifact.payload)
        summary["end_offset"] = end
        summary["trailing_bytes"] = None if end is None else max(0, len(artifact.payload) - end)
        ctx.meta("png/chunks", summary, source="png")


def _parse_iccp(chunk: PngChunk, *, max_text_bytes: int) -> tuple[str, dict[str, object]]:
    keyword_raw, separator, rest = chunk.data.partition(b"\x00")
    if not separator or len(rest) < 1:
        raise ValueError("malformed iCCP chunk")
    if rest[0] != 0:
        raise ValueError(f"unsupported iCCP compression method {rest[0]}")
    decompressor = zlib.decompressobj()
    profile = decompressor.decompress(rest[1:], max_text_bytes + 1)
    if len(profile) > max_text_bytes or not decompressor.eof:
        raise ValueError("iCCP profile exceeds max_text_bytes or is truncated")
    return _decode_text(keyword_raw), parse_icc_profile(profile)


def _describe_auxiliary_chunk(chunk: PngChunk) -> object:
    if chunk.type == b"gAMA" and len(chunk.data) == 4:
        return {"gamma": int.from_bytes(chunk.data, "big") / 100000}
    if chunk.type == b"pHYs" and len(chunk.data) == 9:
        x, y, unit = (
            int.from_bytes(chunk.data[0:4], "big"),
            int.from_bytes(chunk.data[4:8], "big"),
            chunk.data[8],
        )
        return {"x": x, "y": y, "unit": unit}
    if chunk.type == b"tIME" and len(chunk.data) == 7:
        return {
            "year": int.from_bytes(chunk.data[0:2], "big"),
            "month": chunk.data[2],
            "day": chunk.data[3],
            "hour": chunk.data[4],
            "minute": chunk.data[5],
            "second": chunk.data[6],
        }
    if chunk.type == b"sRGB" and chunk.data:
        return {"intent": chunk.data[0]}
    return {"length": chunk.length, "hex": chunk.data[:256].hex()}


__all__ = ["PngChunksPlugin", "parse_text_chunk"]
