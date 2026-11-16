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


def make_png(
    width: int,
    height: int,
    rows: list[bytes],
    *,
    color_type: int = 2,
    bit_depth: int = 8,
    extra_chunks: list[tuple[bytes, bytes]] | None = None,
) -> bytes:
    """Build a non-interlaced PNG from already-filtered scanlines."""
    if len(rows) != height:
        raise ValueError("row count does not match height")
    raw = b"".join(b"\x00" + row for row in rows)
    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0)
    chunks = b"".join(png_chunk(kind, data) for kind, data in (extra_chunks or ()))
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + chunks
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


def sub_blocks(data: bytes, *, block_size: int = 255) -> bytes:
    """Encode data as GIF-style length-prefixed sub-blocks."""
    output = bytearray()
    for offset in range(0, len(data), block_size):
        block = data[offset : offset + block_size]
        output.append(len(block))
        output.extend(block)
    output.append(0)
    return bytes(output)


def make_png_text_chunks(*, text: str = "", compressed: str = "", international: str = "") -> bytes:
    """Build a 1x1 PNG carrying tEXt, zTXt and iTXt chunks."""
    extra: list[tuple[bytes, bytes]] = []
    if text:
        extra.append((b"tEXt", b"Comment\x00" + text.encode()))
    if compressed:
        extra.append((b"zTXt", b"Compressed\x00\x00" + zlib.compress(compressed.encode())))
    if international:
        extra.append(
            (
                b"iTXt",
                b"International\x00\x00\x00\x00\x00" + international.encode(),
            )
        )
    return make_png(1, 1, [b"\x00\x00\x00"], extra_chunks=extra)


def make_png_with_unknown_chunk(value: bytes, *, icc: str = "") -> bytes:
    extra: list[tuple[bytes, bytes]] = [(b"meTa", value)]
    if icc:
        profile = make_icc_profile(icc)
        extra.append((b"iCCP", b"ICC\x00\x00" + zlib.compress(profile)))
    return make_png(1, 1, [b"\x00\x00\x00"], extra_chunks=extra)


def make_palette_png(*, trailing: bytes = b"") -> bytes:
    """Build an 8-bit palette PNG with a duplicate entry and transparency."""
    palette = b"\x00\x00\x00\xff\x00\x00\x00\xff\x00\xff\x00\x00"
    rows = [bytes([0, 1, 1, 3])]
    payload = make_png(
        4,
        1,
        rows,
        color_type=3,
        extra_chunks=[(b"PLTE", palette), (b"tRNS", b"\x00\xff\x80\xff")],
    )
    return payload + trailing


def make_low_depth_gray_png(bit_depth: int = 1) -> bytes:
    """Build a 1-bit or 2-bit grayscale PNG."""
    if bit_depth == 1:
        row = b"\xaa"
    elif bit_depth == 2:
        row = b"\x1b"
    else:
        raise ValueError("only 1-bit and 2-bit fixtures are provided")
    return make_png(8 if bit_depth == 1 else 4, 1, [row], color_type=0, bit_depth=bit_depth)


def png_with_bitplane(
    value: bytes,
    *,
    bit: int = 1,
    order: str = "msb",
    layout: str = "row-major",
    channel: int = 1,
    channels: int = 3,
) -> bytes:
    """Embed ``value`` into an arbitrary PNG bit plane and scan layout."""
    shifts = range(7, -1, -1) if order == "msb" else range(8)
    bits = [(byte >> shift) & 1 for byte in value for shift in shifts]
    if layout == "row-major":
        width, height = len(bits), 1
    elif layout == "column-major":
        if len(bits) % 8:
            raise ValueError("column fixtures need a whole number of bytes")
        width, height = len(bits) // 8, 8
    else:
        raise ValueError("unknown layout")
    pixels = [bytearray(width * channels) for _ in range(height)]
    for index, bit_value in enumerate(bits):
        if layout == "row-major":
            x, y = index, 0
        else:
            x, y = index // height, index % height
        if bit_value:
            pixels[y][x * channels + channel] |= 1 << (bit - 1)
    return make_png(width, height, [bytes(row) for row in pixels])


def jpeg_segment(marker: int, data: bytes) -> bytes:
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(data) + 2) + data


def make_jpeg(*segments: bytes, trailer: bytes = b"") -> bytes:
    return b"\xff\xd8" + b"".join(segments) + b"\xff\xd9" + trailer


def make_tiff_with_strings(
    *,
    image_description: str = "",
    software: str = "",
    xmp: bytes = b"",
) -> bytes:
    """Build a little-endian TIFF with IFD0 string/XMP tags."""
    entries: list[tuple[int, int, bytes]] = []
    if image_description:
        entries.append((0x010E, 2, image_description.encode() + b"\x00"))
    if software:
        entries.append((0x0131, 2, software.encode() + b"\x00"))
    if xmp:
        entries.append((0x02BC, 1, xmp))
    if not entries:
        raise ValueError("provide at least one TIFF value")
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
    return (
        b"II*\x00"
        + struct.pack("<I", 8)
        + struct.pack("<H", len(entries))
        + bytes(entry_bytes)
        + struct.pack("<I", 0)
        + bytes(payload)
    )


def make_tiff_with_nested_exif(
    *,
    image_description: str = "",
    software: str = "",
    date_time_original: str = "",
    user_comment: str = "",
    gps_latitude_ref: str = "",
    gps_processing_method: str = "",
    xmp: bytes = b"",
    thumbnail_description: str = "",
) -> bytes:
    """Build a little-endian TIFF with Exif/GPS/thumbnail IFDs and XMP."""
    ifd0: list[tuple[int, int, bytes]] = []
    if image_description:
        ifd0.append((0x010E, 2, image_description.encode() + b"\x00"))
    if software:
        ifd0.append((0x0131, 2, software.encode() + b"\x00"))
    if xmp:
        ifd0.append((0x02BC, 1, xmp))
    exif: list[tuple[int, int, bytes]] = []
    if date_time_original:
        exif.append((0x9003, 2, date_time_original.encode() + b"\x00"))
    if user_comment:
        exif.append((0x9286, 7, b"ASCII\x00\x00\x00" + user_comment.encode() + b"\x00"))
    gps: list[tuple[int, int, bytes]] = []
    if gps_latitude_ref:
        gps.append((0x0001, 2, gps_latitude_ref.encode() + b"\x00"))
    if gps_processing_method:
        gps.append((0x001B, 7, b"ASCII\x00\x00\x00" + gps_processing_method.encode() + b"\x00"))
    thumbnail: list[tuple[int, int, bytes]] = []
    if thumbnail_description:
        thumbnail.append((0x010D, 2, thumbnail_description.encode() + b"\x00"))
    if not ifd0 and not exif and not gps and not thumbnail:
        raise ValueError("provide at least one TIFF value")

    if exif:
        ifd0.append((0x8769, 4, b""))
    if gps:
        ifd0.append((0x8825, 4, b""))
    ifds = [ifd0] + [entries for entries in (exif, gps, thumbnail) if entries]
    sizes = [2 + 12 * len(entries) + 4 for entries in ifds]
    offsets: list[int] = []
    cursor = 8
    for size in sizes:
        offsets.append(cursor)
        cursor += size
    data_start = cursor
    exif_offset = offsets[1] if exif else 0
    gps_offset = offsets[2] if gps else 0
    thumbnail_offset = offsets[-1] if thumbnail else 0
    resolved: list[list[tuple[int, int, bytes]]] = []
    for entries in ifds:
        current: list[tuple[int, int, bytes]] = []
        for tag, value_type, data in entries:
            if tag == 0x8769 and not data:
                data = struct.pack("<I", exif_offset)
            elif tag == 0x8825 and not data:
                data = struct.pack("<I", gps_offset)
            current.append((tag, value_type, data))
        resolved.append(current)

    payload = bytearray()
    blocks: list[bytes] = []
    for index, entries in enumerate(resolved):
        block = bytearray(struct.pack("<H", len(entries)))
        for tag, value_type, data in entries:
            count = 1 if value_type in {4, 13} else len(data)
            block += struct.pack("<HHI", tag, value_type, count)
            if len(data) <= 4:
                block += data.ljust(4, b"\x00")
            else:
                block += struct.pack("<I", data_start + len(payload))
                payload += data
        next_ifd = thumbnail_offset if index == 0 and thumbnail else 0
        block += struct.pack("<I", next_ifd)
        blocks.append(bytes(block))
    return b"II*\x00" + struct.pack("<I", 8) + b"".join(blocks) + bytes(payload)


def make_jpeg_with_nested_exif(
    *,
    image_description: str = "",
    software: str = "",
    date_time_original: str = "",
    user_comment: str = "",
    gps_latitude_ref: str = "",
    gps_processing_method: str = "",
    xmp: bytes = b"",
    thumbnail_description: str = "",
) -> bytes:
    tiff = make_tiff_with_nested_exif(
        image_description=image_description,
        software=software,
        date_time_original=date_time_original,
        user_comment=user_comment,
        gps_latitude_ref=gps_latitude_ref,
        gps_processing_method=gps_processing_method,
        xmp=xmp,
        thumbnail_description=thumbnail_description,
    )
    app1 = b"Exif\x00\x00" + tiff
    return b"\xff\xd8" + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + b"\xff\xd9"


def make_8bim(resource_id: int, data: bytes) -> bytes:
    """Build one Photoshop 8BIM resource block."""
    resource = b"8BIM" + struct.pack(">H", resource_id) + b"\x00\x00"
    resource += struct.pack(">I", len(data)) + data
    if len(data) % 2:
        resource += b"\x00"
    return resource


def make_iptc_dataset(record: int, dataset: int, value: bytes) -> bytes:
    return b"\x1c" + bytes([record, dataset]) + struct.pack(">H", len(value)) + value


def make_icc_profile(description: str, *, copyright_text: str = "") -> bytes:
    """Build a minimal ICC profile with desc/cprt text tags."""
    tags: list[tuple[bytes, bytes]] = []
    for signature, text in ((b"desc", description), (b"cprt", copyright_text)):
        if not text:
            continue
        encoded = text.encode("latin-1", errors="replace") + b"\x00"
        data = signature + b"\x00\x00\x00\x00" + struct.pack(">I", len(encoded)) + encoded
        tags.append((signature, data))
    header = bytearray(128)
    header[8:12] = b"\x02\x10\x00\x00"
    header[12:16] = b"mntr"
    header[16:20] = b"RGB "
    header[20:24] = b"XYZ "
    header[36:40] = b"acsp"
    table_size = 4 + 12 * len(tags)
    data_offset = 128 + table_size
    table = bytearray(struct.pack(">I", len(tags)))
    payload = bytearray()
    for signature, data in tags:
        table += signature + struct.pack(">II", data_offset + len(payload), len(data))
        payload += data
    profile = bytes(header) + bytes(table) + bytes(payload)
    return struct.pack(">I", len(profile)) + profile[4:]


def make_jpeg_with_metadata(
    *, xmp: str = "", iptc: str = "", icc: str = "", comment: str = ""
) -> bytes:
    segments: list[bytes] = []
    if xmp:
        segments.append(jpeg_segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00" + xmp.encode()))
    if iptc:
        payload = make_iptc_dataset(1, 90, iptc.encode())
        app13 = b"Photoshop 3.0\x00" + make_8bim(0x0404, payload)
        segments.append(jpeg_segment(0xED, app13))
    if icc:
        profile = make_icc_profile(icc, copyright_text="fixture copyright")
        segments.append(jpeg_segment(0xE2, b"ICC_PROFILE\x00\x01\x01" + profile))
    if comment:
        segments.append(jpeg_segment(0xFE, comment.encode()))
    if not segments:
        raise ValueError("provide at least one metadata segment")
    return make_jpeg(*segments)


def make_gif(*, comment: str = "", application: str = "", trailer: bytes = b"") -> bytes:
    """Build a minimal animated-capable GIF with optional extensions."""
    payload = bytearray(b"GIF89a")
    payload += struct.pack("<HHBBB", 1, 1, 0, 0, 0)
    if comment:
        payload += b"\x21\xfe" + sub_blocks(comment.encode())
    if application:
        payload += b"\x21\xff\x0bNETSCAPE2.0" + sub_blocks(application.encode())
    payload += b"\x2c" + struct.pack("<HHHHB", 0, 0, 1, 1, 0)
    payload += b"\x02" + sub_blocks(b"\x44\x01")
    payload += b"\x3b" + trailer
    return bytes(payload)


def make_bmp(*, trailing: bytes = b"") -> bytes:
    """Build a 1x1 24-bit BMP with an optional trailer."""
    pixels = b"\x00\x00\x00\x00"
    file_size = 14 + 40 + len(pixels)
    file_header = b"BM" + struct.pack("<IHHI", file_size, 0, 0, 54)
    dib = struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 24, 0, len(pixels), 0, 0, 0, 0)
    return file_header + dib + pixels + trailing


def make_zip(name: str = "secret.txt", data: bytes = b"payload") -> bytes:
    """Build a one-entry stored ZIP archive."""
    crc = binascii.crc32(data) & 0xFFFFFFFF
    local = (
        b"PK\x03\x04"
        + struct.pack("<HHHHHIIIHH", 20, 0, 0, 0, 0, crc, len(data), len(data), len(name), 0)
        + name.encode()
        + data
    )
    central = (
        b"PK\x01\x02"
        + struct.pack(
            "<HHHHHHIIIHHHHHII",
            20,
            20,
            0,
            0,
            0,
            0,
            crc,
            len(data),
            len(data),
            len(name),
            0,
            0,
            0,
            0,
            0,
            0,
        )
        + name.encode()
    )
    eocd = b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 1, 1, len(central), len(local), 0)
    return local + central + eocd


def raw_deflate(data: bytes) -> bytes:
    compressor = zlib.compressobj(wbits=-15)
    return compressor.compress(data) + compressor.flush()
