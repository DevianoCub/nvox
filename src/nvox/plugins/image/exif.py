"""Focused EXIF/TIFF string extraction.

Reads ASCII, XP, XMP, GPS and UserComment values from JPEG APP1 and TIFF IFD0,
Exif, GPS, Interop and thumbnail IFDs so the flag engine can search metadata and
decode payloads hidden there. Richer container coverage (IPTC/ICC, all JPEG/GIF/
BMP/TIFF structures) lives in the M3 image plugins; this plugin deliberately
keeps its concise provenance chain.
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
    0x9003: "DateTimeOriginal",
    0x9004: "DateTimeDigitized",
    0x9286: "UserComment",
    0x9290: "SubSecTime",
    0x9291: "SubSecTimeOriginal",
    0x9292: "SubSecTimeDigitized",
    0xA420: "ImageUniqueID",
    0xA430: "CameraOwnerName",
    0xA431: "BodySerialNumber",
    0xA433: "LensMake",
    0xA434: "LensModel",
    0xA435: "LensSerialNumber",
}
_XP_TAGS = {
    0x9C9B: "XPTitle",
    0x9C9C: "XPComment",
    0x9C9D: "XPAuthor",
    0x9C9E: "XPKeywords",
    0x9C9F: "XPSubject",
}
_GPS_ASCII_TAGS = {
    0x0001: "GPSLatitudeRef",
    0x0003: "GPSLongitudeRef",
    0x0009: "GPSStatus",
    0x000A: "GPSMeasureMode",
    0x001D: "GPSDateStamp",
}
_GPS_TEXT_TAGS = {
    0x001B: "GPSProcessingMethod",
    0x001C: "GPSAreaInformation",
}
_EXIF_IFD_POINTER = 0x8769
_GPS_IFD_POINTER = 0x8825
_INTEROP_IFD_POINTER = 0xA005
_XMP_TAG = 0x02BC
_MAX_ENTRIES = 1024
_MAX_IFDS = 16
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
    """Parse string tags from IFD0, Exif, GPS, Interop and thumbnail IFDs."""
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
    pending: list[tuple[int, str]] = [(first_ifd, "ifd")]
    seen: set[int] = set()
    while pending and len(seen) < _MAX_IFDS:
        offset, kind = pending.pop(0)
        if offset < 8 or offset in seen:
            continue
        seen.add(offset)
        _read_ifd(tiff, offset, endian, values, pending, kind)
    return values


def _read_ifd(
    tiff: bytes,
    offset: int,
    endian: Endian,
    values: dict[str, str],
    pending: list[tuple[int, str]],
    kind: str,
) -> None:
    if offset + 2 > len(tiff):
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
        if tag in {_EXIF_IFD_POINTER, _GPS_IFD_POINTER, _INTEROP_IFD_POINTER}:
            if value_type in (4, 13) and item_count >= 1:
                child_kind = {
                    _EXIF_IFD_POINTER: "exif",
                    _GPS_IFD_POINTER: "gps",
                    _INTEROP_IFD_POINTER: "interop",
                }[tag]
                pending.append((_u32(tiff, value_field, endian), child_kind))
            continue
        if kind == "gps" and tag in _GPS_ASCII_TAGS and value_type == 2:
            raw = _value_bytes(tiff, value_field, item_count, endian)
            if raw is not None:
                text = raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
                if text:
                    values.setdefault(_GPS_ASCII_TAGS[tag], text)
            continue
        if kind == "gps" and tag in _GPS_TEXT_TAGS and value_type == 7:
            raw = _value_bytes(tiff, value_field, item_count, endian)
            if raw is not None:
                text = _decode_text(raw, tag, endian)
                if text:
                    values.setdefault(_GPS_TEXT_TAGS[tag], text)
            continue
        if tag in _XP_TAGS and value_type == 1:
            raw = _value_bytes(tiff, value_field, item_count, endian)
            if raw is not None:
                text = raw.decode("utf-16-le", errors="replace").split("\x00", 1)[0].strip()
                if text:
                    values.setdefault(_XP_TAGS[tag], text)
            continue
        if tag == _XMP_TAG and value_type in (1, 7):
            raw = _value_bytes(tiff, value_field, item_count, endian)
            if raw is not None:
                text = raw.decode("utf-8", errors="replace").strip("\x00 \t\r\n")
                if text:
                    values.setdefault("XMP", text)
            continue
        if tag not in _ASCII_TAGS or value_type not in (2, 7):
            continue
        raw = _value_bytes(tiff, value_field, item_count, endian)
        if raw is None:
            continue
        text = _decode_text(raw, tag, endian)
        if text:
            values.setdefault(_ASCII_TAGS[tag], text)
    next_offset = offset + 2 + count * 12
    if next_offset + 4 <= len(tiff):
        next_ifd = _u32(tiff, next_offset, endian)
        if next_ifd:
            pending.append((next_ifd, "ifd"))
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
    if tag in {0x9286, 0x001B, 0x001C}:
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
    version: ClassVar[str] = "0.2.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("image/jpeg", "image/tiff")
    priority: ClassVar[int] = 80
    description: ClassVar[str] = (
        "Extract ASCII, XP, GPS, XMP and UserComment strings from JPEG/TIFF EXIF."
    )

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
