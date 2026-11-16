"""PNG parsing and configurable bit-plane extraction.

The reader is deliberately dependency-free. It supports non-interlaced 8-bit
PNG images in all standard colour modes and 1/2/4/8-bit grayscale or palette
images. 16-bit and Adam7 images are rejected with a clear skip reason instead of
being decoded incorrectly.

The plugin keeps the M2 ``image.lsb`` name and default output naming for bit 1,
MSB-first, row-major extraction. Options extend it to every bit plane, channel,
bit order and scan layout.
"""

from __future__ import annotations

import binascii
import struct
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, ClassVar

from nvox.api import Analyzer, Artifact, Context, PluginKind, register

_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_VALID_DEPTHS = {0: {1, 2, 4, 8}, 2: {8}, 3: {1, 2, 4, 8}, 4: {8}, 6: {8}}
_MAX_CHUNKS = 4096


class PngError(ValueError):
    """Raised when a PNG cannot be decoded by the built-in reader."""


@dataclass(frozen=True)
class PngChunk:
    """One PNG chunk with its location and CRC status."""

    type: bytes
    data: bytes
    offset: int
    crc: int
    crc_ok: bool

    @property
    def length(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class PngImage:
    """Decoded PNG samples.

    ``pixels`` contains one byte per sample. Palette images keep their palette
    indices in ``pixels`` so bit-plane analysis sees the original indices; the
    ``palette`` and ``transparency`` fields expose PLTE/tRNS data.
    """

    width: int
    height: int
    channels: int
    pixels: bytes
    bit_depth: int = 8
    color_type: int = 2
    palette: bytes | None = None
    transparency: bytes | None = None


def walk_png(payload: bytes, *, max_chunks: int = _MAX_CHUNKS) -> tuple[PngChunk, ...]:
    """Return PNG chunks in file order, recording CRC validity.

    Truncated chunks raise :class:`PngError`. A missing IEND is tolerated so
    metadata and structure plugins can still report a damaged file.
    """
    if not payload.startswith(_SIGNATURE):
        raise PngError("missing PNG signature")
    offset = len(_SIGNATURE)
    chunks: list[PngChunk] = []
    while offset + 8 <= len(payload):
        if len(chunks) >= max_chunks:
            raise PngError(f"PNG has more than {max_chunks} chunks")
        length = int.from_bytes(payload[offset : offset + 4], "big")
        chunk_type = payload[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        if data_end + 4 > len(payload):
            raise PngError(f"truncated PNG chunk at offset {offset}")
        data = payload[data_start:data_end]
        crc = int.from_bytes(payload[data_end : data_end + 4], "big")
        expected = binascii.crc32(chunk_type + data) & 0xFFFFFFFF
        chunks.append(
            PngChunk(
                type=chunk_type,
                data=data,
                offset=offset,
                crc=crc,
                crc_ok=crc == expected,
            )
        )
        offset = data_end + 4
        if chunk_type == b"IEND":
            break
    return tuple(chunks)


def png_end(payload: bytes) -> int | None:
    """Return the offset just after IEND, or ``None`` if it is absent."""
    try:
        chunks = walk_png(payload)
    except PngError:
        return None
    for chunk in chunks:
        if chunk.type == b"IEND":
            return chunk.offset + 12 + len(chunk.data)
    return None


def decode_png(payload: bytes, *, max_pixels: int = 2_000_000) -> PngImage:
    """Decode a supported PNG into unfiltered, unpacked samples."""
    chunks = walk_png(payload)
    header: bytes | None = None
    palette: bytes | None = None
    transparency: bytes | None = None
    idat: list[bytes] = []
    for chunk in chunks:
        if chunk.type == b"IHDR" and header is None:
            header = chunk.data
        elif chunk.type == b"PLTE":
            palette = chunk.data
        elif chunk.type == b"tRNS":
            transparency = chunk.data
        elif chunk.type == b"IDAT":
            idat.append(chunk.data)
    if header is None or len(header) != 13:
        raise PngError("missing or invalid IHDR")
    width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(
        ">IIBBBBB", header
    )
    if width <= 0 or height <= 0:
        raise PngError("invalid PNG dimensions")
    if width * height > max_pixels:
        raise PngError(f"image exceeds max_pixels ({max_pixels})")
    if compression != 0 or filter_method != 0:
        raise PngError("unsupported PNG compression/filter method")
    if interlace != 0:
        raise PngError("interlaced PNG is not supported")
    if color_type not in _CHANNELS:
        raise PngError(f"unsupported colour type: {color_type}")
    if bit_depth not in _VALID_DEPTHS[color_type]:
        raise PngError(f"unsupported bit depth {bit_depth} for colour type {color_type}")
    if color_type == 3 and (palette is None or len(palette) % 3):
        raise PngError("palette image is missing a valid PLTE chunk")
    if palette is not None and len(palette) % 3:
        raise PngError("PLTE length is not a multiple of three")
    if not idat:
        raise PngError("missing IDAT")

    channels = _CHANNELS[color_type]
    row_bytes = _row_bytes(width, channels, bit_depth)
    bpp = max(1, (channels * bit_depth + 7) // 8)
    expected = height * (row_bytes + 1)
    try:
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(b"".join(idat), expected + 1)
    except zlib.error as exc:
        raise PngError(f"corrupt IDAT stream: {exc}") from exc
    if len(raw) < expected:
        raise PngError("truncated pixel data")
    filtered = _unfilter(raw, width, height, row_bytes, bpp)
    pixels = _unpack_samples(filtered, width, height, channels, bit_depth)
    return PngImage(
        width=width,
        height=height,
        channels=channels,
        pixels=pixels,
        bit_depth=bit_depth,
        color_type=color_type,
        palette=palette,
        transparency=transparency,
    )


def _row_bytes(width: int, channels: int, bit_depth: int) -> int:
    return (width * channels * bit_depth + 7) // 8


def _unfilter(raw: bytes, width: int, height: int, row_bytes: int, bpp: int) -> bytes:
    previous = bytearray(row_bytes)
    output = bytearray()
    cursor = 0
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        line = bytearray(raw[cursor : cursor + row_bytes])
        cursor += row_bytes
        if filter_type == 1:
            for index in range(row_bytes):
                left = line[index - bpp] if index >= bpp else 0
                line[index] = (line[index] + left) & 0xFF
        elif filter_type == 2:
            for index in range(row_bytes):
                line[index] = (line[index] + previous[index]) & 0xFF
        elif filter_type == 3:
            for index in range(row_bytes):
                left = line[index - bpp] if index >= bpp else 0
                line[index] = (line[index] + ((left + previous[index]) >> 1)) & 0xFF
        elif filter_type == 4:
            for index in range(row_bytes):
                left = line[index - bpp] if index >= bpp else 0
                upper_left = previous[index - bpp] if index >= bpp else 0
                line[index] = (line[index] + _paeth(left, previous[index], upper_left)) & 0xFF
        elif filter_type != 0:
            raise PngError(f"invalid PNG filter type: {filter_type}")
        output.extend(line)
        previous = line
    return bytes(output)


def _unpack_samples(
    filtered: bytes,
    width: int,
    height: int,
    channels: int,
    bit_depth: int,
) -> bytes:
    row_bytes = _row_bytes(width, channels, bit_depth)
    samples_per_row = width * channels
    if bit_depth == 8:
        return b"".join(filtered[row * row_bytes : (row + 1) * row_bytes] for row in range(height))
    mask = (1 << bit_depth) - 1
    output = bytearray()
    for row in range(height):
        line = filtered[row * row_bytes : (row + 1) * row_bytes]
        for index in range(samples_per_row):
            bit_offset = index * bit_depth
            byte_index = bit_offset // 8
            shift = 8 - bit_depth - (bit_offset % 8)
            output.append((line[byte_index] >> shift) & mask)
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
    return extract_plane(
        pixels,
        width=len(pixels) // max(1, channels),
        height=1,
        channels=channels,
        channel=channel,
        bit=1,
        order="msb",
        layout="row-major",
    )


def extract_plane(
    pixels: bytes,
    *,
    width: int,
    height: int,
    channels: int,
    channel: int,
    bit: int,
    order: str = "msb",
    layout: str = "row-major",
) -> bytes:
    """Pack one sample bit plane in the requested scan order."""
    if channels <= 0 or width <= 0 or height <= 0:
        raise ValueError("width, height and channels must be positive")
    if not 0 <= channel < channels:
        raise ValueError(f"channel {channel} is outside 0..{channels - 1}")
    if not 1 <= bit <= 8:
        raise ValueError("bit must be between 1 and 8")
    if order not in {"msb", "lsb"}:
        raise ValueError("order must be 'msb' or 'lsb'")
    if layout not in {"row-major", "column-major"}:
        raise ValueError("layout must be 'row-major' or 'column-major'")
    if len(pixels) < width * height * channels:
        raise ValueError("pixel buffer is smaller than the image dimensions")

    def values() -> Iterable[int]:
        if layout == "row-major":
            return pixels[channel::channels]
        return (
            pixels[x * channels + channel + y * width * channels]
            for x in range(width)
            for y in range(height)
        )

    packed = bytearray()
    current = 0
    used = 0
    shift = bit - 1
    for value in values():
        sample = (int(value) >> shift) & 1
        if order == "msb":
            current = (current << 1) | sample
        else:
            current |= sample << used
        used += 1
        if used == 8:
            packed.append(current)
            current = 0
            used = 0
    if used:
        packed.append(current << (8 - used) if order == "msb" else current)
    return bytes(packed)


def bit_plane_pixels(
    pixels: bytes,
    *,
    width: int,
    height: int,
    channels: int,
    channel: int,
    bit: int,
) -> bytes:
    """Return a row-major 0/255 grayscale image of one bit plane."""
    shift = bit - 1
    output = bytearray(width * height)
    index = channel
    for pixel in range(width * height):
        output[pixel] = 255 if (pixels[index] >> shift) & 1 else 0
        index += channels
    return bytes(output)


def encode_gray_png(width: int, height: int, pixels: bytes) -> bytes:
    """Encode an 8-bit grayscale PNG from row-major 0..255 samples."""
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    if len(pixels) != width * height:
        raise ValueError("pixel buffer does not match the image dimensions")
    raw = b"".join(b"\x00" + pixels[row * width : (row + 1) * width] for row in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        _SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw))
        + _png_chunk(b"IEND", b"")
    )


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = binascii.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def parse_selection(
    value: object,
    *,
    default: tuple[int, ...],
    allowed: tuple[int, ...],
    label: str,
) -> tuple[int, ...]:
    """Parse an option that may be ``all``, a range, a list or one value."""
    if value is None or value == "":
        return default
    if value == "all":
        return allowed
    raw_values: list[object]
    if isinstance(value, str):
        raw_values = []
        for part in value.replace(",", " ").split():
            if "-" in part:
                start, end = part.split("-", 1)
                raw_values.extend(range(int(start), int(end) + 1))
            else:
                raw_values.append(part)
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw_values = list(value)
    else:
        raw_values = [value]
    selected: list[int] = []
    for item in raw_values:
        if isinstance(item, bool) or not isinstance(item, (int, str)):
            raise ValueError(f"{label} values must be integers")
        number = int(item)
        if number not in allowed:
            raise ValueError(f"{label} value {number} is not in {allowed}")
        if number not in selected:
            selected.append(number)
    return tuple(selected)


def _selection(
    value: object,
    *,
    default: str,
    allowed: tuple[str, ...],
    label: str,
) -> tuple[str, ...]:
    if value is None or value == "":
        value = default
    if value == "both":
        return allowed
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{label} must be one of {(*allowed, 'both')}")
    return (value,)


@register
class PngLsbPlugin(Analyzer):
    """Extract configurable PNG bit planes from each channel."""

    name: ClassVar[str] = "image.lsb"
    version: ClassVar[str] = "0.2.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("image/png",)
    priority: ClassVar[int] = 40
    description: ClassVar[str] = "Extract PNG bit planes (bits 1-8, channels, order, layout)."
    options: ClassVar[dict[str, object]] = {
        "max_pixels": 16_000_000,
        "channels": "all",
        "bits": "1",
        "order": "msb",
        "layout": "row-major",
        "visualize": False,
        "max_planes": 64,
    }

    def run(self, artifact: Artifact, ctx: Context) -> None:
        max_pixels = max(1, int(ctx.option("max_pixels", 16_000_000)))
        try:
            image = decode_png(artifact.payload, max_pixels=max_pixels)
        except PngError as exc:
            ctx.skip(f"png: {exc}")

        try:
            channels = self._channels(ctx.option("channels", "all"), image.channels)
            bits = parse_selection(
                ctx.option("bits", "1"),
                default=(1,),
                allowed=tuple(range(1, 9)),
                label="bits",
            )
            orders = _selection(
                ctx.option("order", "msb"),
                default="msb",
                allowed=("msb", "lsb"),
                label="order",
            )
            layouts = _selection(
                ctx.option("layout", "row-major"),
                default="row-major",
                allowed=("row-major", "column-major"),
                label="layout",
            )
        except (TypeError, ValueError) as exc:
            ctx.skip(f"png: invalid bit-plane option: {exc}")

        max_planes = max(1, int(ctx.option("max_planes", 64)))
        if len(channels) * len(bits) * len(orders) * len(layouts) > max_planes:
            ctx.skip(
                "png: requested bit-plane combinations exceed max_planes "
                f"({max_planes}); raise the option to allow more"
            )
        default_names = bits == (1,) and orders == ("msb",) and layouts == ("row-major",)
        visualize = bool(ctx.option("visualize", False))
        emitted = 0
        for channel in channels:
            for bit in bits:
                for order in orders:
                    for layout in layouts:
                        packed = extract_plane(
                            image.pixels,
                            width=image.width,
                            height=image.height,
                            channels=image.channels,
                            channel=channel,
                            bit=bit,
                            order=order,
                            layout=layout,
                        )
                        if not packed:
                            continue
                        if default_names:
                            name = f"lsb/channel={channel}"
                        else:
                            name = (
                                f"bitplane/channel={channel}/bit={bit}/"
                                f"order={order}/layout={layout.replace('-major', '')}"
                            )
                        params: dict[str, Any] = {
                            "bit": bit,
                            "channel": channel,
                            "channels": image.channels,
                            "width": image.width,
                            "height": image.height,
                            "layout": layout,
                            "order": order,
                            "bit_depth": image.bit_depth,
                            "color_type": image.color_type,
                        }
                        ctx.blob(name, packed, **params)
                        emitted += 1
                        if visualize:
                            ctx.image(
                                f"bitplane-image/channel={channel}/bit={bit}",
                                encode_gray_png(
                                    image.width,
                                    image.height,
                                    bit_plane_pixels(
                                        image.pixels,
                                        width=image.width,
                                        height=image.height,
                                        channels=image.channels,
                                        channel=channel,
                                        bit=bit,
                                    ),
                                ),
                                width=image.width,
                                height=image.height,
                                bit=bit,
                                channel=channel,
                            )
        if not emitted:
            ctx.skip("png: no bit planes extracted")

    @staticmethod
    def _channels(value: object, available: int) -> tuple[int, ...]:
        if value == "all" or value is None:
            return tuple(range(available))
        if isinstance(value, str):
            selected = tuple(int(part) for part in value.replace(",", " ").split())
        elif isinstance(value, (list, tuple, set, frozenset)):
            selected = tuple(int(part) for part in value)
        elif isinstance(value, int) and not isinstance(value, bool):
            selected = (value,)
        else:
            raise ValueError("channels must be 'all', a list or an integer")
        return tuple(channel for channel in selected if 0 <= channel < available)


__all__ = [
    "PngChunk",
    "PngError",
    "PngImage",
    "PngLsbPlugin",
    "bit_plane_pixels",
    "decode_png",
    "encode_gray_png",
    "extract_lsb",
    "extract_plane",
    "parse_selection",
    "png_end",
    "walk_png",
]
