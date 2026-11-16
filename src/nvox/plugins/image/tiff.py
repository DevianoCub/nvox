"""TIFF header and IFD inspection."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, ClassVar, Literal

from nvox.api import Analyzer, Artifact, Context, PluginKind, register

Endian = Literal["little", "big"]
_MAX_ENTRIES = 1024
_MAX_IFDS = 16
_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}
_TAG_NAMES = {
    0x0100: "ImageWidth",
    0x0101: "ImageLength",
    0x0102: "BitsPerSample",
    0x0103: "Compression",
    0x0106: "PhotometricInterpretation",
    0x010D: "DocumentName",
    0x010E: "ImageDescription",
    0x010F: "Make",
    0x0110: "Model",
    0x0111: "StripOffsets",
    0x0112: "Orientation",
    0x0115: "SamplesPerPixel",
    0x0116: "RowsPerStrip",
    0x0117: "StripByteCounts",
    0x011A: "XResolution",
    0x011B: "YResolution",
    0x011C: "PlanarConfiguration",
    0x0128: "ResolutionUnit",
    0x02BC: "XMP",
    0x0131: "Software",
    0x0132: "DateTime",
    0x013B: "Artist",
    0x8298: "Copyright",
    0x8769: "ExifIFD",
    0x8825: "GPSIFD",
    0x9286: "UserComment",
    0x9C9B: "XPTitle",
    0x9C9C: "XPComment",
    0x9C9D: "XPAuthor",
    0x9C9E: "XPKeywords",
    0x9C9F: "XPSubject",
}


class TiffError(ValueError):
    """Raised when a TIFF header or IFD is malformed."""


def parse_tiff_metadata(tiff: bytes, *, max_text_bytes: int = 1_000_000) -> dict[str, object]:
    """Parse TIFF header and IFD entries without decoding image pixels."""
    if len(tiff) < 8:
        raise TiffError("truncated TIFF header")
    if tiff[:2] == b"II":
        endian: Endian = "little"
    elif tiff[:2] == b"MM":
        endian = "big"
    else:
        raise TiffError("unknown TIFF byte order")
    if _u16(tiff, 2, endian) != 42:
        raise TiffError("invalid TIFF magic")
    first_ifd = _u32(tiff, 4, endian)
    summary: dict[str, Any] = {
        "byte_order": endian,
        "first_ifd": first_ifd,
        "ifds": [],
        "strings": {},
    }
    pending: list[int] = [first_ifd]
    seen: set[int] = set()
    while pending and len(seen) < _MAX_IFDS:
        offset = pending.pop(0)
        if offset in seen or offset < 8:
            continue
        seen.add(offset)
        ifd = _parse_ifd(tiff, offset, endian, max_text_bytes)
        if ifd is None:
            continue
        ifds = summary["ifds"]
        if isinstance(ifds, list):
            ifds.append(ifd)
        ifd_strings = ifd.get("strings")
        if isinstance(ifd_strings, dict):
            summary["strings"].update(ifd_strings)
        next_ifd = ifd.get("next_ifd")
        if isinstance(next_ifd, int) and next_ifd:
            pending.append(next_ifd)
        pointers = ifd.get("pointers")
        if isinstance(pointers, list):
            for pointer in pointers:
                if isinstance(pointer, int) and pointer not in seen:
                    pending.append(pointer)
    if not summary["ifds"]:
        raise TiffError("TIFF has no readable IFD")
    return summary


def _parse_ifd(
    tiff: bytes, offset: int, endian: Endian, max_text_bytes: int
) -> dict[str, object] | None:
    if offset + 2 > len(tiff):
        return None
    count = _u16(tiff, offset, endian)
    if count > _MAX_ENTRIES:
        raise TiffError(f"IFD has too many entries: {count}")
    entries: list[dict[str, object]] = []
    strings: dict[str, str] = {}
    pointers: list[int] = []
    for index in range(count):
        entry = offset + 2 + index * 12
        if entry + 12 > len(tiff):
            break
        tag = _u16(tiff, entry, endian)
        value_type = _u16(tiff, entry + 2, endian)
        item_count = _u32(tiff, entry + 4, endian)
        name = _TAG_NAMES.get(tag, f"0x{tag:04X}")
        item: dict[str, object] = {
            "tag": f"0x{tag:04X}",
            "name": name,
            "type": value_type,
            "count": item_count,
        }
        if tag in {0x8769, 0x8825} and value_type in {4, 13} and item_count >= 1:
            pointer = _u32(tiff, entry + 8, endian)
            pointers.append(pointer)
            item["value"] = pointer
        else:
            raw = _value_bytes(tiff, entry + 8, value_type, item_count, endian)
            if raw is None:
                item["error"] = "value outside file"
            else:
                text = _text_value(raw, tag, value_type)
                if text is not None:
                    item["value"] = text
                    strings[name] = text
                else:
                    item["value"] = _numeric_value(raw, value_type, endian)
                    item["hex"] = raw[:64].hex()
        entries.append(item)
    next_offset = offset + 2 + count * 12
    next_ifd = _u32(tiff, next_offset, endian) if next_offset + 4 <= len(tiff) else 0
    return {
        "offset": offset,
        "entries": entries,
        "strings": strings,
        "pointers": pointers,
        "next_ifd": next_ifd,
    }


def _value_bytes(
    tiff: bytes, field: int, value_type: int, count: int, endian: Endian
) -> bytes | None:
    if count == 0 or count > 16_000_000:
        return None
    size = _TYPE_SIZES.get(value_type)
    if size is None:
        return None
    total = size * count
    if total <= 4:
        return tiff[field : field + total]
    offset = _u32(tiff, field, endian)
    if offset >= len(tiff) or offset + total > len(tiff):
        return None
    return tiff[offset : offset + total]


def _text_value(raw: bytes, tag: int, value_type: int) -> str | None:
    if value_type == 2:
        text = raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
        if tag in {0x9C9B, 0x9C9C, 0x9C9D, 0x9C9E, 0x9C9F}:
            text = raw.decode("utf-16-le", errors="replace").split("\x00", 1)[0].strip()
        return text or None
    if tag == 0x02BC and value_type in {1, 7}:
        text = raw.decode("utf-8", errors="replace").strip("\x00 \t\r\n")
        return text or None
    if value_type == 7 and tag == 0x9286:
        for prefix in (b"ASCII\x00\x00\x00", b"UNICODE\x00", b"JIS\x00\x00\x00\x00\x00"):
            if raw.startswith(prefix):
                raw = raw[len(prefix) :]
                break
        return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip() or None
    return None


def _numeric_value(raw: bytes, value_type: int, endian: Endian) -> object:
    if value_type == 1 or value_type == 6 or value_type == 7:
        return list(raw[:64])
    if value_type == 2:
        return raw.decode("latin-1", errors="replace")
    size = _TYPE_SIZES.get(value_type)
    if size is None or len(raw) % size:
        return raw.hex()
    values = [
        int.from_bytes(raw[index : index + size], endian)
        for index in range(0, min(len(raw), size * 16), size)
    ]
    return values


def tiff_texts(metadata: dict[str, object]) -> Iterable[tuple[str, str]]:
    strings = metadata.get("strings")
    if isinstance(strings, dict):
        for name, value in strings.items():
            if isinstance(value, str) and value:
                yield name, value


def _u16(data: bytes, offset: int, endian: Endian) -> int:
    return int.from_bytes(data[offset : offset + 2], endian)


def _u32(data: bytes, offset: int, endian: Endian) -> int:
    return int.from_bytes(data[offset : offset + 4], endian)


@register
class TiffMetadataPlugin(Analyzer):
    """Inspect TIFF header, IFDs, tags and embedded strings."""

    name: ClassVar[str] = "image.tiff"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("image/tiff", "image/x-tiff")
    priority: ClassVar[int] = 82
    description: ClassVar[str] = "Parse TIFF headers, IFDs, tags and string values."
    options: ClassVar[dict[str, object]] = {"max_text_bytes": 1_000_000}

    def run(self, artifact: Artifact, ctx: Context) -> None:
        max_text = max(1, int(ctx.option("max_text_bytes", 1_000_000)))
        try:
            metadata = parse_tiff_metadata(artifact.payload, max_text_bytes=max_text)
        except TiffError as exc:
            ctx.skip(f"tiff: {exc}")
        ctx.meta("tiff/metadata", metadata, source="tiff")
        for name, value in tiff_texts(metadata):
            ctx.text(f"tiff/{name}", value, source="tiff")


__all__ = ["TiffError", "TiffMetadataPlugin", "parse_tiff_metadata", "tiff_texts"]
