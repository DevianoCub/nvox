"""Small binary fixture builders used by the image and scan tests.

Everything is generated with the standard library so the tests do not depend on
Pillow or checked-in binary blobs.
"""

from __future__ import annotations

import base64
import binascii
import struct
import zlib


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = binascii.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def make_png(width: int, height: int, rows: list[bytes], *, color_type: int = 2) -> bytes:
    """Build an 8-bit, non-interlaced PNG from already-filtered scanlines."""
    if len(rows) != height:
        raise ValueError("row count does not match height")
    raw = b"".join(b"\x00" + row for row in rows)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", zlib.compress(raw))
        + png_chunk(b"IEND", b"")
    )


def png_with_lsb_text(value: bytes, *, channel: int = 1, channels: int = 3) -> bytes:
    """Embed ``value`` in one channel's LSB plane, one pixel per bit.

    The image is one row wide enough that no extra LSB bytes follow the text.
    """
    bits = [(byte >> shift) & 1 for byte in value for shift in range(7, -1, -1)]
    width = len(bits)
    row = bytearray(width * channels)
    for index, bit in enumerate(bits):
        row[index * channels + channel] = bit
    return make_png(width, 1, [bytes(row)])


def lsb_payload(value: str) -> bytes:
    """Base64 text that fits exactly into a bit plane."""
    return base64.b64encode(value.encode())


def make_jpeg_with_exif(*, image_description: str = "", user_comment: str = "") -> bytes:
    """Build a minimal JPEG APP1/Exif block with string tags."""
    entries: list[tuple[int, int, bytes]] = []
    if image_description:
        entries.append((0x010E, 2, image_description.encode() + b"\x00"))
    if user_comment:
        entries.append((0x9286, 7, b"ASCII\x00\x00\x00" + user_comment.encode() + b"\x00"))
    if not entries:
        raise ValueError("provide at least one EXIF value")

    ifd_size = 2 + 12 * len(entries) + 4
    data_offset = 8 + ifd_size
    entry_bytes = bytearray()
    payload = bytearray()
    for tag, value_type, data in entries:
        count = len(data)
        entry_bytes += struct.pack("<HHI", tag, value_type, count)
        if count <= 4:
            entry_bytes += data.ljust(4, b"\x00")
        else:
            entry_bytes += struct.pack("<I", data_offset + len(payload))
            payload += data
    tiff = (
        b"II*\x00"
        + struct.pack("<I", 8)
        + struct.pack("<H", len(entries))
        + bytes(entry_bytes)
        + struct.pack("<I", 0)
        + bytes(payload)
    )
    app1 = b"Exif\x00\x00" + tiff
    return b"\xff\xd8" + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + b"\xff\xd9"


def xor_bytes(value: bytes, key: int) -> bytes:
    return bytes(byte ^ key for byte in value)
