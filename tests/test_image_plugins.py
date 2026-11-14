"""Tests for the M2 image plugins and their fixture builders."""

from __future__ import annotations

import base64

import pytest

from fixture_builders import (
    lsb_payload,
    make_jpeg_with_exif,
    make_png,
    png_with_lsb_text,
)
from nvox.api import ArtifactKind, Context, Plugin, PluginSkip, make_artifact
from nvox.plugins.image.exif import ExifError, ExifPlugin, extract_exif, jpeg_exif_block
from nvox.plugins.image.png import (
    PngError,
    PngLsbPlugin,
    decode_png,
    extract_lsb,
)


def _run(plugin: Plugin, payload: bytes, media_type: str) -> tuple[object, ...]:
    artifact = make_artifact(
        kind=ArtifactKind.IMAGE,
        name="image",
        payload=payload,
        media_type=media_type,
    )
    ctx = Context(artifact, plugin.name, max_artifact_bytes=1024 * 1024, depth=1)
    plugin.run(artifact, ctx)
    return ctx.artifacts


def test_decode_png_pixels_and_lsb() -> None:
    payload = png_with_lsb_text(b"abc", channel=1)
    image = decode_png(payload)
    assert (image.width, image.height, image.channels) == (24, 1, 3)
    assert extract_lsb(image.pixels, channels=3, channel=1) == b"abc"


def test_png_lsb_plugin_emits_bit_planes() -> None:
    hidden = lsb_payload("ctf{lsb_ok}")
    payload = png_with_lsb_text(hidden, channel=1)
    artifacts = _run(PngLsbPlugin(), payload, "image/png")
    texts = [artifact.payload for artifact in artifacts]
    assert hidden in texts
    green = next(artifact for artifact in artifacts if artifact.params.get("channel") == 1)
    assert green.text == hidden.decode()


def test_decode_png_rejects_unsupported_inputs() -> None:
    with pytest.raises(PngError):
        decode_png(b"not a png")
    interlaced = bytearray(make_png(1, 1, [b"\x00\x00\x00"]))
    interlaced[28] = 1  # IHDR interlace method
    with pytest.raises(PngError):
        decode_png(bytes(interlaced))


def test_exif_description_is_extracted() -> None:
    encoded = base64.b64encode(b"ctf{exif_ok}")
    jpeg = make_jpeg_with_exif(image_description=encoded.decode())
    assert jpeg_exif_block(jpeg) is not None
    values = extract_exif(jpeg, "image/jpeg")
    assert values == {"ImageDescription": encoded.decode()}

    artifacts = _run(ExifPlugin(), jpeg, "image/jpeg")
    assert any(artifact.text == encoded.decode() for artifact in artifacts)
    assert any(artifact.kind is ArtifactKind.META for artifact in artifacts)


def test_exif_user_comment_and_missing_block() -> None:
    jpeg = make_jpeg_with_exif(user_comment="ctf{comment}")
    values = extract_exif(jpeg, "image/jpeg")
    assert values == {"UserComment": "ctf{comment}"}
    with pytest.raises(ExifError):
        extract_exif(b"\xff\xd8\xff\xd9", "image/jpeg")


def test_exif_plugin_skips_plain_jpeg() -> None:
    plugin = ExifPlugin()
    artifact = make_artifact(
        kind=ArtifactKind.IMAGE,
        name="plain",
        payload=b"\xff\xd8\xff\xd9",
        media_type="image/jpeg",
    )
    ctx = Context(artifact, plugin.name, max_artifact_bytes=1024, depth=1)
    with pytest.raises(PluginSkip):
        plugin.run(artifact, ctx)
