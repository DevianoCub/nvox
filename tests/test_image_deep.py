"""M3 deep image analysis tests and fixtures."""

from __future__ import annotations

import base64
import bz2
import gzip
import lzma
import zlib

import pytest

from fixture_builders import (
    make_bmp,
    make_gif,
    make_jpeg,
    make_jpeg_with_metadata,
    make_jpeg_with_nested_exif,
    make_low_depth_gray_png,
    make_palette_png,
    make_png,
    make_png_text_chunks,
    make_png_with_unknown_chunk,
    make_tiff_with_strings,
    make_zip,
    png_with_bitplane,
    raw_deflate,
)
from nvox.api import ArtifactKind, Context, infer_kind, make_artifact
from nvox.plugins.decoders.deflate import DeflateDecoder
from nvox.plugins.generic.carve import EmbeddedCarver, zip_end
from nvox.plugins.image.analysis import analyze_png_channels
from nvox.plugins.image.bmp import BmpMetadataPlugin, bmp_end, parse_bmp
from nvox.plugins.image.exif import extract_exif
from nvox.plugins.image.gif import GifMetadataPlugin, gif_end, parse_gif
from nvox.plugins.image.jpeg import (
    JpegMetadataPlugin,
    extract_jpeg_metadata,
    extract_jpeg_texts,
    jpeg_end,
    parse_icc_profile,
    walk_jpeg,
)
from nvox.plugins.image.png import (
    PngLsbPlugin,
    bit_plane_pixels,
    decode_png,
    encode_gray_png,
    extract_plane,
    parse_selection,
    png_end,
    walk_png,
)
from nvox.plugins.image.png_chunks import PngChunksPlugin, parse_text_chunk
from nvox.plugins.image.structure import ImageStructurePlugin, find_prepended, find_trailing
from nvox.plugins.image.tiff import TiffMetadataPlugin, parse_tiff_metadata


def _artifact(payload: bytes, media_type: str, *, depth: int = 0) -> object:
    return make_artifact(
        kind=infer_kind(media_type),
        name="fixture",
        payload=payload,
        media_type=media_type,
        depth=depth,
    )


def _run(
    plugin: object,
    payload: bytes,
    media_type: str,
    *,
    options: dict[str, object] | None = None,
    depth: int = 0,
) -> tuple[object, ...]:
    artifact = _artifact(payload, media_type, depth=depth)
    ctx = Context(
        artifact,
        plugin.name,  # type: ignore[attr-defined]
        max_artifact_bytes=64 * 1024 * 1024,
        depth=depth + 1,
        options=options,
    )
    plugin.run(artifact, ctx)  # type: ignore[attr-defined]
    return ctx.artifacts


def test_png_text_chunks_are_parsed() -> None:
    payload = make_png_text_chunks(
        text="ctf{text_chunk}",
        compressed=base64.b64encode(b"ctf{ztext_chunk}").decode(),
        international="<xmp>ctf{itxt_chunk}</xmp>",
    )
    chunks = walk_png(payload)
    text_chunks = [chunk for chunk in chunks if chunk.type in {b"tEXt", b"zTXt", b"iTXt"}]
    assert len(text_chunks) == 3
    values = [parse_text_chunk(chunk, max_text_bytes=1000) for chunk in text_chunks]
    assert values[0] == ("tEXt", "Comment", "ctf{text_chunk}")
    assert values[1][2] == base64.b64encode(b"ctf{ztext_chunk}").decode()
    assert values[2][2] == "<xmp>ctf{itxt_chunk}</xmp>"

    artifacts = _run(PngChunksPlugin(), payload, "image/png")
    text_payloads = [artifact.payload for artifact in artifacts]
    assert any(b"ctf{text_chunk}" in value for value in text_payloads)
    meta = next(artifact for artifact in artifacts if artifact.kind is ArtifactKind.META)
    assert b'"crc_errors": []' in meta.payload
    assert b'"trailing_bytes": 0' in meta.payload


def test_png_unknown_chunks_and_iccp() -> None:
    payload = make_png_with_unknown_chunk(
        b"ctf{custom_chunk}",
        icc="png fixture profile",
    )
    artifacts = _run(PngChunksPlugin(), payload, "image/png")
    assert any(artifact.payload == b"ctf{custom_chunk}" for artifact in artifacts)
    assert any(b"png fixture profile" in artifact.payload for artifact in artifacts)
    meta = next(artifact for artifact in artifacts if artifact.kind is ArtifactKind.META)
    assert b'"unknown_chunks"' in meta.payload
    assert b'"icc"' in meta.payload


def test_png_crc_errors_are_reported() -> None:
    payload = bytearray(make_png_text_chunks(text="ctf{crc}"))
    text_offset = payload.find(b"tEXt")
    payload[text_offset + 4] ^= 0x01
    artifacts = _run(PngChunksPlugin(), bytes(payload), "image/png")
    meta = next(artifact for artifact in artifacts if artifact.kind is ArtifactKind.META)
    assert b'"crc_errors"' in meta.payload
    assert b'"type": "tEXt"' in meta.payload


def test_palette_and_low_depth_png() -> None:
    palette = decode_png(make_palette_png())
    assert (palette.width, palette.height, palette.channels) == (4, 1, 1)
    assert palette.bit_depth == 8
    assert palette.color_type == 3
    assert palette.pixels == bytes([0, 1, 1, 3])
    stats = analyze_png_channels(palette)
    palette_stats = stats["palette"]
    assert isinstance(palette_stats, dict)
    assert palette_stats["entries"] == 4
    assert palette_stats["duplicate_entries"] == 1
    assert palette_stats["transparency"] == 4

    one_bit = decode_png(make_low_depth_gray_png(1))
    assert one_bit.bit_depth == 1
    assert one_bit.pixels == bytes([1, 0, 1, 0, 1, 0, 1, 0])
    two_bit = decode_png(make_low_depth_gray_png(2))
    assert two_bit.pixels == bytes([0, 1, 2, 3])


def test_bitplane_selection_and_visualisation() -> None:
    value = b"ABC"
    payload = png_with_bitplane(
        value,
        bit=3,
        order="lsb",
        layout="column-major",
        channel=1,
    )
    image = decode_png(payload)
    packed = extract_plane(
        image.pixels,
        width=image.width,
        height=image.height,
        channels=image.channels,
        channel=1,
        bit=3,
        order="lsb",
        layout="column-major",
    )
    assert packed == value
    pixels = bit_plane_pixels(
        image.pixels,
        width=image.width,
        height=image.height,
        channels=image.channels,
        channel=1,
        bit=3,
    )
    assert len(pixels) == image.width * image.height
    assert decode_png(encode_gray_png(image.width, image.height, pixels)).width == image.width

    artifacts = _run(
        PngLsbPlugin(),
        payload,
        "image/png",
        options={"bits": "3", "order": "lsb", "layout": "column-major", "visualize": True},
    )
    planes = [
        artifact
        for artifact in artifacts
        if artifact.kind is ArtifactKind.BYTES and artifact.params.get("bit") == 3
    ]
    assert len(planes) == 3
    assert any(artifact.payload == value for artifact in planes)
    assert any(artifact.kind is ArtifactKind.IMAGE for artifact in artifacts)


@pytest.mark.parametrize("bit", range(1, 9))
@pytest.mark.parametrize("order", ["msb", "lsb"])
@pytest.mark.parametrize("layout", ["row-major", "column-major"])
def test_bitplane_matrix(bit: int, order: str, layout: str) -> None:
    value = b"ABC"
    payload = png_with_bitplane(value, bit=bit, order=order, layout=layout, channel=1)
    image = decode_png(payload)
    assert (
        extract_plane(
            image.pixels,
            width=image.width,
            height=image.height,
            channels=image.channels,
            channel=1,
            bit=bit,
            order=order,
            layout=layout,
        )
        == value
    )


def test_bitplane_option_parsing() -> None:
    assert parse_selection("1-3", default=(1,), allowed=tuple(range(1, 9)), label="bits") == (
        1,
        2,
        3,
    )
    assert parse_selection("all", default=(1,), allowed=(1, 2), label="bits") == (1, 2)
    with pytest.raises(ValueError):
        parse_selection("9", default=(1,), allowed=tuple(range(1, 9)), label="bits")
    with pytest.raises(ValueError):
        decode_png(b"not a png")
    assert png_end(make_png(1, 1, [b"\x00\x00\x00"])) is not None


def test_exif_nested_ifds_gps_and_xmp_strings() -> None:
    payload = make_jpeg_with_nested_exif(
        image_description="ctf{exif_description}",
        software="fixture",
        date_time_original="2026:11:16 00:00:00 ctf{exif_date}",
        user_comment="ctf{exif_comment}",
        gps_latitude_ref="N",
        gps_processing_method="ctf{gps_method}",
        xmp=b"<xmp>ctf{exif_xmp}</xmp>",
        thumbnail_description="ctf{thumbnail_description}",
    )
    values = extract_exif(payload, "image/jpeg")
    assert values["ImageDescription"] == "ctf{exif_description}"
    assert values["DateTimeOriginal"] == "2026:11:16 00:00:00 ctf{exif_date}"
    assert values["UserComment"] == "ctf{exif_comment}"
    assert values["GPSLatitudeRef"] == "N"
    assert values["GPSProcessingMethod"] == "ctf{gps_method}"
    assert values["XMP"] == "<xmp>ctf{exif_xmp}</xmp>"
    assert values["DocumentName"] == "ctf{thumbnail_description}"


def test_jpeg_segments_xmp_iptc_icc_and_comment() -> None:
    payload = make_jpeg_with_metadata(
        xmp="<xmp>ctf{jpeg_xmp}</xmp>",
        iptc="ctf{jpeg_iptc}",
        icc="fixture profile",
        comment="ctf{jpeg_comment}",
    )
    metadata = extract_jpeg_metadata(payload)
    assert metadata["comments"] == ["ctf{jpeg_comment}"]
    assert "ctf{jpeg_xmp}" in str(metadata["xmp"])
    assert metadata["iptc"] == {"Caption": ["ctf{jpeg_iptc}"]}
    icc = metadata["icc"]
    assert isinstance(icc, dict)
    assert icc["description"] == "fixture profile"
    assert extract_jpeg_texts(metadata)
    profile = parse_icc_profile(
        b"".join(segment.data[14:] for segment in walk_jpeg(payload) if segment.marker == 0xE2)
    )
    assert profile["description"] == "fixture profile"

    artifacts = _run(JpegMetadataPlugin(), payload, "image/jpeg")
    assert any(b"ctf{jpeg_xmp}" in artifact.payload for artifact in artifacts)
    assert any(b"ctf{jpeg_iptc}" in artifact.payload for artifact in artifacts)


def test_jpeg_end_stops_at_eoi() -> None:
    payload = make_jpeg(trailer=b"PK\x03\x04trailing")
    assert jpeg_end(payload) == len(make_jpeg())
    assert jpeg_end(payload) < len(payload)


def test_gif_extensions_and_trailing() -> None:
    payload = make_gif(comment="ctf{gif_comment}", application="loop=2", trailer=b"extra")
    metadata = parse_gif(payload)
    assert metadata["comments"] == ["ctf{gif_comment}"]
    applications = metadata["applications"]
    assert isinstance(applications, list)
    assert applications[0]["identifier"] == "NETSCAPE2.0"
    assert metadata["frames"] == 1
    assert gif_end(payload) == len(payload) - len(b"extra")
    artifacts = _run(GifMetadataPlugin(), payload, "image/gif")
    assert any(b"ctf{gif_comment}" in artifact.payload for artifact in artifacts)


def test_bmp_and_tiff_basics() -> None:
    bmp = make_bmp(trailing=b"trailer")
    info = parse_bmp(bmp)
    assert (info["width"], info["height"], info["bits_per_pixel"]) == (1, 1, 24)
    assert bmp_end(bmp) == len(bmp) - len(b"trailer")
    artifacts = _run(BmpMetadataPlugin(), bmp, "image/bmp")
    assert any(artifact.kind is ArtifactKind.META for artifact in artifacts)

    tiff = make_tiff_with_strings(image_description="ctf{tiff_description}", software="fixture")
    metadata = parse_tiff_metadata(tiff)
    assert metadata["byte_order"] == "little"
    strings = metadata["strings"]
    assert isinstance(strings, dict)
    assert strings["ImageDescription"] == "ctf{tiff_description}"
    assert metadata["ifds"]
    tiff_artifacts = _run(TiffMetadataPlugin(), tiff, "image/tiff")
    assert any(b"ctf{tiff_description}" in artifact.payload for artifact in tiff_artifacts)

    xmp_tiff = make_tiff_with_strings(xmp=b"<xmp>ctf{tiff_xmp}</xmp>")
    xmp_metadata = parse_tiff_metadata(xmp_tiff)
    xmp_strings = xmp_metadata["strings"]
    assert isinstance(xmp_strings, dict)
    assert xmp_strings["XMP"] == "<xmp>ctf{tiff_xmp}</xmp>"


def test_structure_trailing_prepended_and_polyglot() -> None:
    archive = make_zip(data=b"ctf{embedded_zip}")
    png = make_png(1, 1, [b"\x00\x00\x00"]) + archive
    assert find_trailing(png, "image/png") == len(png) - len(archive)
    artifacts = _run(ImageStructurePlugin(), png, "image/png")
    trailing = next(artifact for artifact in artifacts if artifact.name == "structure/trailing")
    assert trailing.payload == archive
    meta = next(artifact for artifact in artifacts if artifact.kind is ArtifactKind.META)
    assert b'"polyglot": true' in meta.payload
    assert b'"zip"' in meta.payload

    jpeg = make_jpeg(trailer=b"prefix")
    assert find_prepended(b"JUNK" + jpeg, "image/jpeg") == 4
    prepended_artifacts = _run(
        ImageStructurePlugin(),
        b"JUNK" + jpeg,
        "image/jpeg",
    )
    assert any(artifact.name == "structure/prepended" for artifact in prepended_artifacts)


def test_carve_embedded_zip_after_png() -> None:
    archive = make_zip(name="flag.txt", data=b"ctf{carved}")
    png = make_png(1, 1, [b"\x00\x00\x00"]) + archive
    assert zip_end(archive) == len(archive)
    structure_artifacts = _run(ImageStructurePlugin(), png, "image/png")
    trailing = next(
        artifact for artifact in structure_artifacts if artifact.name == "structure/trailing"
    )
    carved = _run(
        EmbeddedCarver(),
        trailing.payload,
        "application/octet-stream",
        depth=1,
    )
    archives = [artifact for artifact in carved if artifact.kind is ArtifactKind.ARCHIVE]
    assert len(archives) == 1
    assert archives[0].media_type == "application/zip"
    assert archives[0].payload == archive


def test_carver_recognizes_all_declared_containers() -> None:
    wav_body = b"WAVE" + b"fmt " + (16).to_bytes(4, "little") + b"\x01\x00\x01\x00"
    wav = b"RIFF" + len(wav_body).to_bytes(4, "little") + wav_body
    cases = [
        ("png", make_png(1, 1, [b"\x00\x00\x00"])),
        ("jpeg", make_jpeg()),
        ("gif", make_gif()),
        ("bmp", make_bmp()),
        ("zip", make_zip(data=b"ctf{carved_container}")),
        ("pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"),
        ("gzip", gzip.compress(b"ctf{gzip_container}")),
        ("bzip2", bz2.compress(b"ctf{bzip2_container}")),
        ("xz", lzma.compress(b"ctf{xz_container}")),
        ("wav", wav),
    ]
    for label, payload in cases:
        artifacts = _run(EmbeddedCarver(), b"X" + payload, "application/octet-stream")
        assert any(artifact.payload == payload for artifact in artifacts), label


def test_deflate_and_embedded_zlib_decoder() -> None:
    raw = raw_deflate(b"ctf{raw_deflate}")
    artifacts = _run(DeflateDecoder(), raw, "application/octet-stream")
    assert any(b"ctf{raw_deflate}" in artifact.payload for artifact in artifacts)

    embedded = b"prefix-" + zlib.compress(b"ctf{embedded_zlib}")
    artifacts = _run(DeflateDecoder(), embedded, "application/octet-stream")
    assert any(b"ctf{embedded_zlib}" in artifact.payload for artifact in artifacts)


def test_m3_end_to_end_png_text_chain(tmp_path) -> None:
    from nvox.core.config import Config
    from nvox.core.engine import Engine
    from nvox.core.flags import FlagScanner, build_flag_specs
    from nvox.core.registry import PluginRegistry
    from nvox.core.store import ArtifactStore

    hidden = base64.b64encode(b"ctf{deep_png_chain}").decode()
    payload = make_png_text_chunks(compressed=hidden)
    path = tmp_path / "deep.png"
    path.write_bytes(payload)
    config = Config.default(cwd=tmp_path)
    registry = PluginRegistry()
    registry.load_builtin()
    registry = registry.subset(["image.png_chunks", "decoder.base64"])
    engine = Engine(
        config,
        registry=registry,
        store=ArtifactStore(),
        jobs=1,
        flag_scanner=FlagScanner(build_flag_specs(["ctf{}"], [])),
    )
    result = engine.analyze(path)
    assert [finding.value for finding in result.findings] == ["deep_png_chain"]
    assert result.findings[0].chain[0].plugin == "image.png_chunks"


def test_m3_end_to_end_jpeg_xmp_chain(tmp_path) -> None:
    from nvox.core.config import Config
    from nvox.core.engine import Engine
    from nvox.core.flags import FlagScanner, build_flag_specs
    from nvox.core.registry import PluginRegistry
    from nvox.core.store import ArtifactStore

    hidden = base64.b64encode(b"ctf{deep_jpeg_chain}").decode()
    payload = make_jpeg_with_metadata(xmp=hidden)
    path = tmp_path / "deep.jpg"
    path.write_bytes(payload)
    config = Config.default(cwd=tmp_path)
    registry = PluginRegistry()
    registry.load_builtin()
    registry = registry.subset(["image.jpeg", "decoder.base64"])
    engine = Engine(
        config,
        registry=registry,
        store=ArtifactStore(),
        jobs=1,
        flag_scanner=FlagScanner(build_flag_specs(["ctf{}"], [])),
    )
    result = engine.analyze(path)
    assert [finding.value for finding in result.findings] == ["deep_jpeg_chain"]
    assert result.findings[0].chain[0].plugin == "image.jpeg"
