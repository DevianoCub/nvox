"""Minimal EXIF/TIFF string extraction.

This is intentionally narrower than the M3 metadata milestone: it reads ASCII
and UserComment values from JPEG APP1 and TIFF IFD0/Exif-IFD so the flag engine
can search metadata and decode base64 hidden there. Richer EXIF/XMP/IPTC support
lands in M3.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from nvox.api import Analyzer, Artifact, Context, PluginKind, register

_ASCII_TAGS = {
    0x010D: "DocumentName",
    0x010E: "ImageDescription",
    0x010F: "Make",
    0x0110: "Model",
    0x0131: "Software",
    0x0132: "DateTime",
    0x013B: "Artist",
    0x8298: "Copyright",
    0x9286: "UserComment",
}
_XP_TAGS = {
    0x9C9B: "XPTitle",
    0x9C9C: "XPComment",
    0x9C9D: "XPAuthor",
    0x9C9E: "XPKeywords",
    0x9C9F: "XPSubject",
}
_EXIF_IFD_POINTER = 0x8769
_MAX_ENTRIES = 1024
Endian = Literal["little", "big"]


class ExifError(ValueError):
    """Raised when an EXIF block cannot be parsed."""


def extract_exif(payload: bytes, media_type: str) -> dict[str, str]:
    """Return string metadata values from a JPEG or TIFF payload."""
    tiff: bytes | None
    if media_type.split(";", 1)[0].strip().lower() == "image/tiff":
        tiff = payload
    else:
        tiff = jpeg_exif_block(payload)
    if tiff is None:
        raise ExifError("no EXIF block found")
    return parse_tiff_strings(tiff)


def jpeg_exif_block(payload: bytes) -> bytes | None:
    """Return the TIFF payload of the first APP1/Exif segment in a JPEG."""
    if not payload.startswith(b"\xff\xd8"):
        return None
    offset = 2
    while offset + 4 <= len(payload):
        if payload[offset] != 0xFF:
            return None
        marker = payload[offset + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            offset += 2
            continue
        if marker == 0xD9:
            return None
        length = int.from_bytes(payload[offset + 2 : offset + 4], "big")
        if length < 2 or offset + 2 + length > len(payload):
            return None
        segment = payload[offset + 4 : offset + 2 + length]
        if marker == 0xE1 and segment.startswith(b"Exif\x00\x00"):
            return segment[6:]
        if marker == 0xDA:
            return None
        offset += 2 + length
    return None


def parse_tiff_strings(tiff: bytes) -> dict[str, str]:
    """Parse ASCII/UserComment/XP strings from TIFF IFD0 and the Exif IFD."""
    if len(tiff) < 8:
        raise ExifError("truncated TIFF header")
    byte_order = tiff[:2]
    endian: Endian
    if byte_order == b"II":
        endian = "little"
    elif byte_order == b"MM":
        endian = "big"
    else:
        raise ExifError("unknown TIFF byte order")
    if _u16(tiff, 2, endian) != 42:
        raise ExifError("invalid TIFF magic")
    first_ifd = _u32(tiff, 4, endian)
    values: dict[str, str] = {}
    pending: list[int] = []
    _read_ifd(tiff, first_ifd, endian, values, pending)
    for offset in pending[:8]:
        if offset and offset not in {first_ifd}:
            _read_ifd(tiff, offset, endian, values, [])
    return values


def _read_ifd(
    tiff: bytes,
    offset: int,
    endian: Endian,
    values: dict[str, str],
    pending: list[int],
) -> None:
    if offset < 8 or offset + 2 > len(tiff):
        return
    count = _u16(tiff, offset, endian)
    if count > _MAX_ENTRIES:
        raise ExifError(f"IFD has too many entries: {count}")
    for index in range(count):
        entry = offset + 2 + index * 12
        if entry + 12 > len(tiff):
            break
        tag = _u16(tiff, entry, endian)
        value_type = _u16(tiff, entry + 2, endian)
        item_count = _u32(tiff, entry + 4, endian)
        value_field = entry + 8
        if tag == _EXIF_IFD_POINTER and value_type in (4, 13) and item_count >= 1:
            pending.append(_u32(tiff, value_field, endian))
            continue
        if tag in _XP_TAGS and value_type == 1:
            raw = _value_bytes(tiff, value_field, item_count, endian)
            if raw is not None:
                text = raw.decode("utf-16-le", errors="replace").split("\x00", 1)[0].strip()
                if text:
                    values[_XP_TAGS[tag]] = text
            continue
        if tag not in _ASCII_TAGS or value_type not in (2, 7):
            continue
        raw = _value_bytes(tiff, value_field, item_count, endian)
        if raw is None:
            continue
        text = _decode_text(raw, tag, endian)
        if text:
            values[_ASCII_TAGS[tag]] = text
    return


def _value_bytes(tiff: bytes, value_field: int, count: int, endian: Endian) -> bytes | None:
    if count == 0 or count > 1024 * 1024:
        return None
    if count <= 4:
        return tiff[value_field : value_field + count]
    offset = _u32(tiff, value_field, endian)
    if offset >= len(tiff) or offset + count > len(tiff):
        return None
    return tiff[offset : offset + count]


def _decode_text(raw: bytes, tag: int, endian: Endian) -> str:
    if tag == 0x9286:
        for prefix in (b"ASCII\x00\x00\x00", b"UNICODE\x00", b"JIS\x00\x00\x00\x00\x00"):
            if raw.startswith(prefix):
                raw = raw[len(prefix) :]
                if prefix.startswith(b"UNICODE"):
                    return _decode_unicode(raw, endian).split("\x00", 1)[0].strip()
                break
    return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()


def _decode_unicode(raw: bytes, endian: Endian) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    codec = "utf-16-le" if endian == "little" else "utf-16-be"
    return raw.decode(codec, errors="replace")


def _u16(data: bytes, offset: int, endian: Endian) -> int:
    return int.from_bytes(data[offset : offset + 2], endian)


def _u32(data: bytes, offset: int, endian: Endian) -> int:
    return int.from_bytes(data[offset : offset + 4], endian)


@register
class ExifPlugin(Analyzer):
    name: ClassVar[str] = "image.exif"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("image/jpeg", "image/tiff")
    priority: ClassVar[int] = 80
    description: ClassVar[str] = "Extract ASCII and UserComment strings from JPEG/TIFF EXIF."

    def run(self, artifact: Artifact, ctx: Context) -> None:
        try:
            values = extract_exif(artifact.payload, artifact.media_type)
        except ExifError as exc:
            ctx.skip(f"exif: {exc}")
        if not values:
            ctx.skip("exif: no string tags found")
        ctx.meta("exif", values, source="exif")
        for tag, value in values.items():
            ctx.text(f"exif/{tag}", value, tag=tag)


__all__ = ["ExifError", "ExifPlugin", "extract_exif", "jpeg_exif_block", "parse_tiff_strings"]
