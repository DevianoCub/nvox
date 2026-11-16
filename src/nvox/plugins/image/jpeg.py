"""JPEG segment parsing, XMP/IPTC/ICC extraction and metadata."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import ClassVar

from nvox.api import Analyzer, Artifact, Context, PluginKind, register

_SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
_STANDALONE = frozenset({0x01, 0xD8, 0xD9}) | frozenset(range(0xD0, 0xD8))
_MAX_SEGMENTS = 4096
_APP_NAMES = {
    0xE0: "APP0",
    0xE1: "APP1",
    0xE2: "APP2",
    0xE3: "APP3",
    0xE4: "APP4",
    0xE5: "APP5",
    0xE6: "APP6",
    0xE7: "APP7",
    0xE8: "APP8",
    0xE9: "APP9",
    0xEA: "APP10",
    0xEB: "APP11",
    0xEC: "APP12",
    0xED: "APP13",
    0xEE: "APP14",
    0xEF: "APP15",
    0xFE: "COM",
    0xDA: "SOS",
    0xD9: "EOI",
}
_IPTC_NAMES = {
    (1, 5): "ObjectName",
    (1, 80): "Byline",
    (1, 85): "BylineTitle",
    (1, 90): "Caption",
    (1, 105): "Headline",
    (1, 110): "Credit",
    (1, 115): "Source",
    (1, 116): "Copyright",
    (1, 120): "CaptionWriter",
    (2, 40): "SpecialInstructions",
    (2, 80): "Keywords",
    (2, 105): "Headline",
    (2, 116): "Copyright",
}
_XMP_PREFIX = b"http://ns.adobe.com/xap/1.0/\x00"
_XMP_EXTENSION_PREFIX = b"http://ns.adobe.com/xmp/extension/\x00"
_ICC_PREFIX = b"ICC_PROFILE\x00"
_IPTC_PREFIX = b"Photoshop 3.0\x00"


class JpegError(ValueError):
    """Raised when a JPEG segment stream is malformed."""


@dataclass(frozen=True)
class JpegSegment:
    marker: int
    offset: int
    data: bytes

    @property
    def name(self) -> str:
        return _APP_NAMES.get(self.marker, f"0x{self.marker:02X}")


def walk_jpeg(payload: bytes, *, max_segments: int = _MAX_SEGMENTS) -> tuple[JpegSegment, ...]:
    """Return JPEG markers and segment payloads, including post-SOS markers.

    Entropy-coded data is skipped, but marker parsing continues after it so
    comments and application segments placed after SOS are not lost.
    """
    if not payload.startswith(b"\xff\xd8"):
        raise JpegError("missing JPEG SOI")
    offset = 2
    segments: list[JpegSegment] = [JpegSegment(0xD8, 0, b"")]
    while offset < len(payload):
        if len(segments) >= max_segments:
            raise JpegError(f"JPEG has more than {max_segments} segments")
        if payload[offset] != 0xFF:
            raise JpegError(f"expected JPEG marker at offset {offset}")
        marker_start = offset
        while offset < len(payload) and payload[offset] == 0xFF:
            offset += 1
        if offset >= len(payload):
            break
        marker = payload[offset]
        offset += 1
        if marker == 0x00:
            continue
        if marker in _STANDALONE:
            segments.append(JpegSegment(marker, marker_start, b""))
            if marker == 0xD9:
                break
            continue
        if offset + 2 > len(payload):
            raise JpegError("truncated JPEG segment length")
        length = int.from_bytes(payload[offset : offset + 2], "big")
        if length < 2 or offset + length > len(payload):
            raise JpegError(f"invalid JPEG segment length at offset {marker_start}")
        data = payload[offset + 2 : offset + length]
        segments.append(JpegSegment(marker, marker_start, data))
        offset += length
        if marker == 0xDA:
            offset = _skip_entropy(payload, offset)
    return tuple(segments)


def _skip_entropy(payload: bytes, offset: int) -> int:
    while offset < len(payload):
        if payload[offset] != 0xFF:
            offset += 1
            continue
        marker_start = offset
        offset += 1
        while offset < len(payload) and payload[offset] == 0xFF:
            offset += 1
        if offset >= len(payload):
            return offset
        marker = payload[offset]
        if marker == 0x00 or 0xD0 <= marker <= 0xD7:
            offset += 1
            continue
        return marker_start
    return offset


def jpeg_end(payload: bytes) -> int | None:
    """Return the byte offset just after the first EOI marker."""
    try:
        segments = walk_jpeg(payload)
    except JpegError:
        return None
    for segment in segments:
        if segment.marker == 0xD9:
            return segment.offset + 2
    return None


def extract_jpeg_metadata(payload: bytes, *, max_text_bytes: int = 1_000_000) -> dict[str, object]:
    """Parse APPn/COM/SOF segments and extract XMP, IPTC and ICC text."""
    segments = walk_jpeg(payload)
    summary: dict[str, object] = {
        "segments": [
            {"marker": segment.name, "offset": segment.offset, "length": len(segment.data)}
            for segment in segments[:256]
        ],
        "comments": [],
        "app_text": {},
    }
    icc_chunks: list[tuple[int, int, bytes]] = []
    for segment in segments:
        data = segment.data
        if segment.marker in _SOF_MARKERS and len(data) >= 6:
            precision = data[0]
            height = int.from_bytes(data[1:3], "big")
            width = int.from_bytes(data[3:5], "big")
            summary.update(
                {
                    "width": width,
                    "height": height,
                    "precision": precision,
                    "components": data[5],
                }
            )
        elif segment.marker == 0xE0 and data.startswith(b"JFIF\x00"):
            summary["jfif"] = _parse_jfif(data)
        elif segment.marker == 0xE1:
            if data.startswith(b"Exif\x00\x00"):
                # EXIF string extraction remains the responsibility of
                # image.exif, which also covers standalone TIFF files.
                summary["exif"] = {"present": True, "length": len(data) - 6}
            elif data.startswith(_XMP_PREFIX):
                summary["xmp"] = _decode_text(data[len(_XMP_PREFIX) :][:max_text_bytes])
            elif data.startswith(_XMP_EXTENSION_PREFIX):
                summary.setdefault("xmp_extensions", [])
                extensions = summary["xmp_extensions"]
                if isinstance(extensions, list):
                    extensions.append(_parse_xmp_extension(data[len(_XMP_EXTENSION_PREFIX) :]))
            elif _looks_text(data):
                _add_app_text(summary, segment, data, max_text_bytes)
        elif segment.marker == 0xE2 and data.startswith(_ICC_PREFIX):
            if len(data) >= 14:
                sequence = data[12]
                total = data[13]
                icc_chunks.append((sequence, total, data[14:]))
        elif segment.marker == 0xED:
            _parse_iptc_segment(data, summary, max_text_bytes)
        elif segment.marker == 0xFE:
            text = _decode_text(data[:max_text_bytes])
            comments = summary["comments"]
            if isinstance(comments, list):
                comments.append(text)
        elif 0xE0 <= segment.marker <= 0xEF and _looks_text(data):
            _add_app_text(summary, segment, data, max_text_bytes)

    if icc_chunks:
        icc_chunks.sort(key=lambda item: item[0])
        profile = b"".join(chunk[2] for chunk in icc_chunks)
        try:
            summary["icc"] = parse_icc_profile(profile)
        except JpegError as exc:
            _append_error(summary, f"ICC: {exc}")
    return summary


def extract_jpeg_texts(metadata: dict[str, object]) -> Iterable[tuple[str, str]]:
    """Yield named textual values from a JPEG metadata mapping."""
    xmp = metadata.get("xmp")
    if isinstance(xmp, str) and xmp:
        yield "xmp", xmp
    comments = metadata.get("comments")
    if isinstance(comments, list):
        for index, comment in enumerate(comments):
            if isinstance(comment, str) and comment:
                yield f"comment/{index}", comment
    iptc = metadata.get("iptc")
    if isinstance(iptc, dict):
        for name, values in iptc.items():
            if isinstance(values, list):
                for index, value in enumerate(values):
                    if isinstance(value, str):
                        yield f"iptc/{name}/{index}", value
    app_text = metadata.get("app_text")
    if isinstance(app_text, dict):
        for name, value in app_text.items():
            if isinstance(value, str):
                yield f"app/{name}", value
    icc = metadata.get("icc")
    if isinstance(icc, dict):
        for key in ("description", "copyright"):
            value = icc.get(key)
            if isinstance(value, str) and value:
                yield f"icc/{key}", value


def parse_icc_profile(profile: bytes) -> dict[str, object]:
    """Parse the ICC header and common text tags with strict bounds."""
    if len(profile) < 132:
        raise JpegError("ICC profile is shorter than its header")
    declared = int.from_bytes(profile[0:4], "big")
    size = min(len(profile), declared) if declared >= 132 else len(profile)
    header = {
        "size": declared,
        "version": profile[8:12].hex(),
        "class": profile[12:16].decode("latin-1", errors="replace"),
        "color_space": profile[16:20].decode("latin-1", errors="replace"),
        "pcs": profile[20:24].decode("latin-1", errors="replace"),
        "signature": profile[36:40].decode("latin-1", errors="replace"),
    }
    tags: dict[str, object] = {"header": header}
    tag_count = int.from_bytes(profile[128:132], "big")
    if tag_count > 4096:
        raise JpegError("ICC profile has too many tags")
    for index in range(tag_count):
        entry = 132 + index * 12
        if entry + 12 > size:
            break
        signature = profile[entry : entry + 4]
        offset = int.from_bytes(profile[entry + 4 : entry + 8], "big")
        length = int.from_bytes(profile[entry + 8 : entry + 12], "big")
        if offset < 128 or offset + length > size or length == 0:
            continue
        if signature in {b"desc", b"cprt", b"dmnd", b"dmdd"}:
            text = _icc_text(profile, offset, length)
            if text:
                name = signature.decode("ascii")
                tags[name] = text
                if name == "desc":
                    tags["description"] = text
                elif name == "cprt":
                    tags["copyright"] = text
    return tags


def _icc_text(profile: bytes, offset: int, length: int) -> str:
    data = profile[offset : offset + length]
    if len(data) < 12:
        return ""
    kind = data[0:4]
    if kind == b"desc":
        count = int.from_bytes(data[8:12], "big")
        return _decode_text(data[12 : 12 + min(count, len(data) - 12, 4096)]).strip("\x00")
    if kind in {b"mluc", b"text"}:
        if kind == b"text":
            return _decode_text(data[8:][:4096]).strip("\x00")
        count = int.from_bytes(data[8:12], "big")
        record_size = int.from_bytes(data[12:16], "big")
        if count > 256 or record_size < 12:
            return ""
        texts: list[str] = []
        for index in range(count):
            record = 16 + index * record_size
            if record + 12 > len(data):
                break
            text_length = int.from_bytes(data[record + 4 : record + 8], "big")
            text_offset = int.from_bytes(data[record + 8 : record + 12], "big")
            raw = data[text_offset : text_offset + text_length]
            texts.append(raw.decode("utf-16-be", errors="replace").strip("\x00"))
        return " / ".join(text for text in texts if text)
    return ""


def _parse_iptc_segment(data: bytes, summary: dict[str, object], max_text: int) -> None:
    try:
        resources = parse_photoshop_resources(data)
    except JpegError as exc:
        _append_error(summary, f"IPTC: {exc}")
        return
    values: dict[str, list[str]] = {}
    for resource_id, payload in resources:
        if resource_id != 0x0404:
            continue
        for name, value in parse_iptc_datasets(payload, max_text=max_text):
            values.setdefault(name, []).append(value)
    if values:
        summary["iptc"] = values


def parse_photoshop_resources(data: bytes) -> list[tuple[int, bytes]]:
    """Parse 8BIM resources from an APP13 payload."""
    if not data.startswith(_IPTC_PREFIX):
        raise JpegError("missing Photoshop APP13 header")
    offset = len(_IPTC_PREFIX)
    resources: list[tuple[int, bytes]] = []
    while offset + 12 <= len(data):
        if data[offset : offset + 4] != b"8BIM":
            raise JpegError(f"invalid 8BIM signature at offset {offset}")
        resource_id = int.from_bytes(data[offset + 4 : offset + 6], "big")
        name_length = data[offset + 6]
        offset += 7 + name_length
        if (1 + name_length) % 2:
            offset += 1
        if offset + 4 > len(data):
            raise JpegError("truncated 8BIM resource size")
        size = int.from_bytes(data[offset : offset + 4], "big")
        offset += 4
        if offset + size > len(data):
            raise JpegError("truncated 8BIM resource data")
        resources.append((resource_id, data[offset : offset + size]))
        offset += size
        if size % 2:
            offset += 1
    return resources


def parse_iptc_datasets(data: bytes, *, max_text: int = 1_000_000) -> Iterable[tuple[str, str]]:
    """Yield ``(name, text)`` for textual IPTC IIM datasets."""
    offset = 0
    while offset + 5 <= len(data):
        if data[offset] != 0x1C:
            offset += 1
            continue
        record = data[offset + 1]
        dataset = data[offset + 2]
        length = int.from_bytes(data[offset + 3 : offset + 5], "big")
        offset += 5
        if length & 0x8000:
            length_bytes = length & 0x7FFF
            if length_bytes > 4 or offset + length_bytes > len(data):
                break
            length = int.from_bytes(data[offset : offset + length_bytes], "big")
            offset += length_bytes
        if offset + length > len(data):
            break
        value = data[offset : offset + min(length, max_text)]
        offset += length
        if not value:
            continue
        name = _IPTC_NAMES.get((record, dataset), f"{record}:{dataset}")
        yield name, _decode_text(value).strip("\x00")


def _parse_jfif(data: bytes) -> dict[str, object]:
    if len(data) < 14:
        return {"length": len(data)}
    return {
        "version": f"{data[5]}.{data[6]:02d}",
        "units": data[7],
        "x_density": int.from_bytes(data[8:10], "big"),
        "y_density": int.from_bytes(data[10:12], "big"),
        "thumbnail": f"{data[12]}x{data[13]}",
    }


def _parse_xmp_extension(data: bytes) -> dict[str, object]:
    if len(data) < 40:
        return {"length": len(data)}
    guid = data[:32].decode("ascii", errors="replace")
    full_length = int.from_bytes(data[32:36], "big")
    chunk_offset = int.from_bytes(data[36:40], "big")
    return {
        "guid": guid,
        "full_length": full_length,
        "offset": chunk_offset,
        "text": _decode_text(data[40:][:1_000_000]),
    }


def _add_app_text(
    summary: dict[str, object], segment: JpegSegment, data: bytes, max_text: int
) -> None:
    app_text = summary["app_text"]
    if isinstance(app_text, dict):
        app_text[segment.name] = _decode_text(data[:max_text]).strip("\x00")


def _append_error(summary: dict[str, object], message: str) -> None:
    errors = summary.setdefault("errors", [])
    if isinstance(errors, list):
        errors.append(message)


def _looks_text(data: bytes) -> bool:
    if not data:
        return False
    sample = data[:4096]
    printable = sum(1 for byte in sample if 0x20 <= byte < 0x7F or byte in (9, 10, 13))
    return printable / len(sample) >= 0.85


def _decode_text(data: bytes) -> str:
    for codec in ("utf-8", "latin-1"):
        try:
            return data.decode(codec)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


@register
class JpegMetadataPlugin(Analyzer):
    """Parse JPEG APPn/COM segments and embedded XMP/IPTC/ICC text."""

    name: ClassVar[str] = "image.jpeg"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("image/jpeg",)
    priority: ClassVar[int] = 82
    description: ClassVar[str] = "Parse JPEG segments, XMP, IPTC and ICC metadata."
    options: ClassVar[dict[str, object]] = {"max_text_bytes": 1_000_000}

    def run(self, artifact: Artifact, ctx: Context) -> None:
        max_text = max(1, int(ctx.option("max_text_bytes", 1_000_000)))
        try:
            metadata = extract_jpeg_metadata(artifact.payload, max_text_bytes=max_text)
        except JpegError as exc:
            ctx.skip(f"jpeg: {exc}")
        ctx.meta("jpeg/metadata", metadata, source="jpeg")
        for name, value in extract_jpeg_texts(metadata):
            ctx.text(f"jpeg/{name}", value, source="jpeg")


__all__ = [
    "JpegError",
    "JpegMetadataPlugin",
    "JpegSegment",
    "extract_jpeg_metadata",
    "extract_jpeg_texts",
    "jpeg_end",
    "parse_icc_profile",
    "parse_iptc_datasets",
    "parse_photoshop_resources",
    "walk_jpeg",
]
