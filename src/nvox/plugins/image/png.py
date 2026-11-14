"""Minimal PNG bit-plane extraction (stdlib only).

M2 only needs the least-significant bit plane to prove the artifact chain; M3
will add configurable planes, ordering and Pillow-backed decoding for more
colour types. This module deliberately rejects formats it cannot decode instead
of guessing.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import ClassVar

from nvox.api import Analyzer, Artifact, Context, PluginKind, register


class PngError(ValueError):
    """Raised when a PNG cannot be decoded by the minimal reader."""


@dataclass(frozen=True)
class PngImage:
    width: int
    height: int
    channels: int
    pixels: bytes


_CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}
_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def decode_png(payload: bytes, *, max_pixels: int = 2_000_000) -> PngImage:
    """Decode an 8-bit, non-interlaced PNG into unfiltered pixel bytes."""
    if not payload.startswith(_SIGNATURE):
        raise PngError("missing PNG signature")
    offset = len(_SIGNATURE)
    width = height = bit_depth = color_type = interlace = -1
    idat: list[bytes] = []
    while offset + 8 <= len(payload):
        length = int.from_bytes(payload[offset : offset + 4], "big")
        chunk_type = payload[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        if data_end + 4 > len(payload):
            raise PngError("truncated PNG chunk")
        data = payload[data_start:data_end]
        if chunk_type == b"IHDR":
            if len(data) != 13:
                raise PngError("invalid IHDR length")
            width, height, bit_depth, color_type, compression, filter_method, interlace = (
                struct.unpack(">IIBBBBB", data)
            )
            if compression != 0 or filter_method != 0:
                raise PngError("unsupported PNG compression/filter method")
        elif chunk_type == b"IDAT":
            idat.append(data)
        elif chunk_type == b"IEND":
            break
        offset = data_end + 4

    if width <= 0 or height <= 0 or not idat:
        raise PngError("missing IHDR or IDAT")
    if bit_depth != 8:
        raise PngError(f"unsupported bit depth: {bit_depth}")
    if color_type not in _CHANNELS:
        raise PngError(f"unsupported colour type: {color_type}")
    if interlace != 0:
        raise PngError("interlaced PNG is not supported")
    if width * height > max_pixels:
        raise PngError(f"image exceeds max_pixels ({max_pixels})")

    channels = _CHANNELS[color_type]
    stride = width * channels
    expected = height * (stride + 1)
    try:
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(b"".join(idat), expected + 1)
    except zlib.error as exc:
        raise PngError(f"corrupt IDAT stream: {exc}") from exc
    if len(raw) < expected:
        raise PngError("truncated pixel data")

    return PngImage(
        width=width,
        height=height,
        channels=channels,
        pixels=_unfilter(raw, width, height, channels),
    )


def _unfilter(raw: bytes, width: int, height: int, channels: int) -> bytes:
    stride = width * channels
    previous = bytearray(stride)
    output = bytearray()
    cursor = 0
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        line = bytearray(raw[cursor : cursor + stride])
        cursor += stride
        if filter_type == 1:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                line[index] = (line[index] + left) & 0xFF
        elif filter_type == 2:
            for index in range(stride):
                line[index] = (line[index] + previous[index]) & 0xFF
        elif filter_type == 3:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                line[index] = (line[index] + ((left + previous[index]) >> 1)) & 0xFF
        elif filter_type == 4:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                upper_left = previous[index - channels] if index >= channels else 0
                line[index] = (line[index] + _paeth(left, previous[index], upper_left)) & 0xFF
        elif filter_type != 0:
            raise PngError(f"invalid PNG filter type: {filter_type}")
        output.extend(line)
        previous = line
    return bytes(output)


def _paeth(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    distance_left = abs(estimate - left)
    distance_up = abs(estimate - up)
    distance_upper_left = abs(estimate - upper_left)
    if distance_left <= distance_up and distance_left <= distance_upper_left:
        return left
    if distance_up <= distance_upper_left:
        return up
    return upper_left


def extract_lsb(pixels: bytes, *, channels: int, channel: int) -> bytes:
    """Pack one channel's least-significant bits row-major, MSB first."""
    values = pixels[channel::channels]
    packed = bytearray()
    current = 0
    bits = 0
    for value in values:
        current = (current << 1) | (value & 1)
        bits += 1
        if bits == 8:
            packed.append(current)
            current = 0
            bits = 0
    if bits:
        packed.append(current << (8 - bits))
    return bytes(packed)


@register
class PngLsbPlugin(Analyzer):
    name: ClassVar[str] = "image.lsb"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("image/png",)
    priority: ClassVar[int] = 40
    description: ClassVar[str] = "Extract per-channel PNG least-significant bit planes."
    options: ClassVar[dict[str, object]] = {"max_pixels": 2_000_000, "channels": "all"}

    def run(self, artifact: Artifact, ctx: Context) -> None:
        max_pixels = max(1, int(ctx.option("max_pixels", 2_000_000)))
        try:
            image = decode_png(artifact.payload, max_pixels=max_pixels)
        except PngError as exc:
            ctx.skip(f"png: {exc}")

        selected = ctx.option("channels", "all")
        if selected == "all":
            channels = tuple(range(image.channels))
        elif isinstance(selected, str):
            channels = tuple(int(part) for part in selected.split(",") if part.strip())
        else:
            channels = tuple(int(part) for part in selected)  # type: ignore[union-attr]
        emitted = 0
        for channel in channels:
            if not 0 <= channel < image.channels:
                continue
            packed = extract_lsb(image.pixels, channels=image.channels, channel=channel)
            if not packed:
                continue
            ctx.blob(
                f"lsb/channel={channel}",
                packed,
                bit=1,
                channel=channel,
                channels=image.channels,
                width=image.width,
                height=image.height,
                layout="row-major",
                order="msb",
            )
            emitted += 1
        if not emitted:
            ctx.skip("png: no bit planes extracted")


__all__ = ["PngError", "PngImage", "PngLsbPlugin", "decode_png", "extract_lsb"]
