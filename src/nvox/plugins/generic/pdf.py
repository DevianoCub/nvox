"""Basic PDF structure, metadata, embedded-file and text extraction."""

from __future__ import annotations

import re
import zlib
from typing import Any, ClassVar

from nvox.api import Analyzer, Artifact, Context, PluginKind, register

_OBJECT = re.compile(rb"(?m)^\s*(\d+)\s+(\d+)\s+obj\b")
_STREAM = re.compile(rb"<<(.*?)>>\s*stream\r?\n(.*?)\r?\nendstream", re.DOTALL)
_TEXT_STRING = re.compile(rb"\((?:\\.|[^\\()])*\)")
_HEX_STRING = re.compile(rb"<([0-9A-Fa-f\s]+)>")
_METADATA_KEYS = (
    "Title",
    "Author",
    "Subject",
    "Keywords",
    "Creator",
    "Producer",
    "CreationDate",
    "ModDate",
)


class PdfError(ValueError):
    """Raised when a PDF payload is not usable."""


def _decode_pdf_string(raw: bytes) -> str:
    raw = raw[1:-1] if raw.startswith(b"(") and raw.endswith(b")") else raw
    output = bytearray()
    escapes = {
        ord("n"): b"\n",
        ord("r"): b"\r",
        ord("t"): b"\t",
        ord("b"): b"\b",
        ord("f"): b"\f",
        ord("("): b"(",
        ord(")"): b")",
        ord("\\"): b"\\",
    }
    index = 0
    while index < len(raw):
        value = raw[index]
        if value != 0x5C:
            output.append(value)
            index += 1
            continue
        index += 1
        if index >= len(raw):
            break
        escape = raw[index]
        if escape in escapes:
            output.extend(escapes[escape])
            index += 1
        elif escape in b"01234567":
            digits = bytearray()
            while index < len(raw) and len(digits) < 3 and raw[index] in b"01234567":
                digits.append(raw[index])
                index += 1
            output.append(int(digits.decode(), 8) & 0xFF)
        elif escape in (0x0A, 0x0D):
            index += 1
            if escape == 0x0D and index < len(raw) and raw[index] == 0x0A:
                index += 1
        else:
            output.append(escape)
            index += 1
    return output.decode("latin-1", errors="replace")


def _metadata_value(payload: bytes, key: str) -> str | None:
    pattern = re.compile(rb"/" + key.encode() + rb"\s*(\((?:\\\\.|[^\\\\()])*\)|<[0-9A-Fa-f\\s]+>)")
    match = pattern.search(payload)
    if match is None:
        return None
    raw = match.group(1)
    if raw.startswith(b"<"):
        try:
            data = bytes.fromhex(re.sub(rb"\s+", b"", raw[1:-1]).decode())
        except ValueError:
            return None
        return data.decode("latin-1", errors="replace").strip("\x00")
    return _decode_pdf_string(raw).strip("\x00")


def _decode_stream(dictionary: bytes, data: bytes) -> tuple[bytes, str | None]:
    if b"/FlateDecode" in dictionary:
        try:
            return zlib.decompress(data), "flate"
        except zlib.error:
            return b"", "flate-error"
    if b"/ASCIIHexDecode" in dictionary:
        cleaned = re.sub(rb"\s+", b"", data.split(b">", 1)[0])
        try:
            return bytes.fromhex(cleaned.decode()), "asciihex"
        except ValueError:
            return b"", "asciihex-error"
    if b"/ASCII85Decode" in dictionary:
        import base64

        try:
            return base64.a85decode(data, adobe=True), "ascii85"
        except ValueError:
            return b"", "ascii85-error"
    return data, None


def _extract_text(data: bytes) -> str:
    pieces: list[str] = []
    for raw in _TEXT_STRING.findall(data):
        value = _decode_pdf_string(raw)
        if value:
            pieces.append(value)
    for raw in _HEX_STRING.findall(data):
        cleaned = re.sub(rb"\s+", b"", raw)
        if len(cleaned) % 2:
            cleaned += b"0"
        try:
            value = bytes.fromhex(cleaned.decode()).decode("latin-1", errors="replace")
        except ValueError:
            continue
        if value:
            pieces.append(value)
    return "".join(pieces)


def parse_pdf(payload: bytes, *, max_stream_bytes: int = 8 * 1024 * 1024) -> dict[str, Any]:
    """Parse common PDF metadata, streams and trailing data."""
    header = re.match(rb"%PDF-(\d\.\d)", payload[:16])
    if header is None:
        raise PdfError("missing %PDF- header")
    version = header.group(1).decode("ascii", errors="replace")
    objects = [(int(match.group(1)), match.start()) for match in _OBJECT.finditer(payload)]
    pages = len(re.findall(rb"/Type\s*/Page\b", payload))
    streams: list[dict[str, Any]] = []
    text_parts: list[str] = []
    for match in _STREAM.finditer(payload):
        dictionary, raw = match.group(1), match.group(2)
        if len(raw) > max_stream_bytes:
            streams.append({"offset": match.start(2), "size": len(raw), "status": "too-large"})
            continue
        decoded, filter_name = _decode_stream(dictionary, raw)
        entry: dict[str, Any] = {
            "offset": match.start(2),
            "size": len(raw),
            "filter": filter_name,
            "decoded_size": len(decoded),
        }
        streams.append(entry)
        if decoded:
            text = _extract_text(decoded)
            if text:
                text_parts.append(text)
    eof_positions = [match.start() for match in re.finditer(rb"%%EOF", payload)]
    trailing_offset = None
    if eof_positions:
        trailing_offset = eof_positions[-1] + 5
    encrypted = b"/Encrypt" in payload
    metadata = {key: _metadata_value(payload, key) for key in _METADATA_KEYS}
    metadata = {key: value for key, value in metadata.items() if value}
    return {
        "version": version,
        "objects": len(objects),
        "pages": pages,
        "streams": streams,
        "metadata": metadata,
        "text": "".join(text_parts)[:2_000_000],
        "encrypted": encrypted,
        "javascript": b"/JavaScript" in payload or b"/JS" in payload,
        "open_action": b"/OpenAction" in payload,
        "launch_action": b"/Launch" in payload,
        "embedded_files": payload.count(b"/EmbeddedFile"),
        "trailing_offset": trailing_offset,
        "trailing_bytes": 0 if trailing_offset is None else max(0, len(payload) - trailing_offset),
        "eof_markers": len(eof_positions),
    }


@register
class PdfPlugin(Analyzer):
    """Report PDF metadata, structure, decompressed text and suspicious actions."""

    name: ClassVar[str] = "generic.pdf"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("application/pdf",)
    priority: ClassVar[int] = 60
    description: ClassVar[str] = "Parse PDF metadata, streams, text, actions and trailing data."
    options: ClassVar[dict[str, object]] = {"max_stream_bytes": 8 * 1024 * 1024}

    def run(self, artifact: Artifact, ctx: Context) -> None:
        try:
            parsed = parse_pdf(
                artifact.payload,
                max_stream_bytes=max(1, int(ctx.option("max_stream_bytes", 8 * 1024 * 1024))),
            )
        except PdfError as exc:
            ctx.skip(f"pdf: {exc}")
        text = str(parsed.pop("text", ""))
        ctx.meta("pdf/metadata", parsed, source="pdf")
        if text:
            ctx.text("pdf/text", text, source="pdf-stream")
        trailing_offset = parsed.get("trailing_offset")
        if isinstance(trailing_offset, int) and parsed.get("trailing_bytes"):
            ctx.blob(
                "pdf/trailing",
                artifact.payload[trailing_offset:],
                media_type="application/octet-stream",
                offset=trailing_offset,
            )
        if not text and not parsed.get("metadata"):
            ctx.skip("pdf: no metadata or text extracted")


__all__ = ["PdfError", "PdfPlugin", "parse_pdf"]
