"""Basic BMP header parsing."""

from __future__ import annotations

from typing import ClassVar

from nvox.api import Analyzer, Artifact, Context, PluginKind, register


class BmpError(ValueError):
    """Raised when a BMP header cannot be parsed."""


def bmp_end(payload: bytes) -> int | None:
    """Return the declared end of a BMP file, if it is plausible."""
    try:
        info = parse_bmp(payload)
    except BmpError:
        return None
    size = info.get("declared_size")
    if isinstance(size, int) and 18 <= size <= len(payload):
        return size
    return None


def parse_bmp(payload: bytes) -> dict[str, object]:
    """Parse the BMP file and DIB headers."""
    if len(payload) < 18 or not payload.startswith(b"BM"):
        raise BmpError("missing BMP signature or truncated header")
    declared_size = int.from_bytes(payload[2:6], "little")
    pixel_offset = int.from_bytes(payload[10:14], "little")
    dib_size = int.from_bytes(payload[14:18], "little")
    info: dict[str, object] = {
        "declared_size": declared_size,
        "pixel_offset": pixel_offset,
        "dib_size": dib_size,
    }
    if dib_size == 12 and len(payload) >= 26:
        width = int.from_bytes(payload[18:20], "little")
        height = int.from_bytes(payload[20:22], "little")
        planes = int.from_bytes(payload[22:24], "little")
        bits_per_pixel = int.from_bytes(payload[24:26], "little")
        compression = 0
        image_size = 0
        colors_used = 0
    elif dib_size >= 40 and len(payload) >= 54:
        width = int.from_bytes(payload[18:22], "little", signed=True)
        height = int.from_bytes(payload[22:26], "little", signed=True)
        planes = int.from_bytes(payload[26:28], "little")
        bits_per_pixel = int.from_bytes(payload[28:30], "little")
        compression = int.from_bytes(payload[30:34], "little")
        image_size = int.from_bytes(payload[34:38], "little")
        colors_used = int.from_bytes(payload[46:50], "little")
    else:
        raise BmpError(f"unsupported BMP DIB header size {dib_size}")
    info.update(
        {
            "width": width,
            "height": abs(height),
            "top_down": height < 0,
            "planes": planes,
            "bits_per_pixel": bits_per_pixel,
            "compression": compression,
            "image_size": image_size,
            "colors_used": colors_used,
        }
    )
    if pixel_offset < 14 or pixel_offset > len(payload):
        info["pixel_offset_valid"] = False
    else:
        info["pixel_offset_valid"] = True
    info["trailing_bytes"] = max(0, len(payload) - declared_size) if declared_size else None
    return info


@register
class BmpMetadataPlugin(Analyzer):
    """Read BMP dimensions, pixel layout and declared file size."""

    name: ClassVar[str] = "image.bmp"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("image/bmp", "image/x-ms-bmp")
    priority: ClassVar[int] = 82
    description: ClassVar[str] = "Parse BMP header, DIB layout and trailing data size."

    def run(self, artifact: Artifact, ctx: Context) -> None:
        try:
            metadata = parse_bmp(artifact.payload)
        except BmpError as exc:
            ctx.skip(f"bmp: {exc}")
        ctx.meta("bmp/metadata", metadata, source="bmp")


__all__ = ["BmpError", "BmpMetadataPlugin", "bmp_end", "parse_bmp"]
