"""Tests for built-in decoder plugins."""

from __future__ import annotations

import base64
import bz2
import gzip
import lzma
import zlib

import pytest

from nvox.api import Artifact, ArtifactKind, Context, Plugin, PluginSkip, make_artifact
from nvox.plugins.decoders.compression import (
    Bzip2Decoder,
    GzipDecoder,
    XzDecoder,
    ZlibDecoder,
)
from nvox.plugins.decoders.text import (
    Base32Decoder,
    Base64Decoder,
    Base64UrlDecoder,
    Base85Decoder,
    HexDecoder,
    ReverseDecoder,
    Rot13Decoder,
    Rot47Decoder,
    UrlDecoder,
)
from nvox.plugins.decoders.xor import XorBruteDecoder


def _run(
    plugin: Plugin,
    payload: bytes,
    *,
    kind: ArtifactKind = ArtifactKind.TEXT,
    max_bytes: int = 1024 * 1024,
    options: dict[str, object] | None = None,
) -> tuple[Artifact, ...]:
    artifact = make_artifact(kind=kind, name="input", payload=payload)
    merged = dict(plugin.options or {})
    merged.update(options or {})
    ctx = Context(
        artifact,
        plugin.name,
        max_artifact_bytes=max_bytes,
        depth=1,
        options=merged,
    )
    plugin.run(artifact, ctx)
    return ctx.artifacts


def test_base64_and_base64url() -> None:
    payload = base64.b64encode(b"ctf{base64_ok}")
    artifacts = _run(Base64Decoder(), payload)
    assert len(artifacts) == 1
    assert artifacts[0].kind is ArtifactKind.TEXT
    assert artifacts[0].text == "ctf{base64_ok}"
    assert artifacts[0].params["codec"] == "base64"

    url_payload = base64.urlsafe_b64encode(b"\xffctf{url_ok}").rstrip(b"=")
    with pytest.raises(PluginSkip):
        _run(Base64Decoder(), url_payload)
    artifacts = _run(Base64UrlDecoder(), url_payload)
    assert any("ctf{url_ok}" in artifact.text for artifact in artifacts)


def test_base64_to_binary_magic_is_emitted() -> None:
    inner = zlib.compress(b"ctf{nested}")
    payload = base64.b64encode(inner)
    artifacts = _run(Base64Decoder(), payload)
    assert len(artifacts) == 1
    assert artifacts[0].kind is ArtifactKind.BYTES
    assert artifacts[0].payload == inner


def test_base32_hex_and_base85() -> None:
    b32 = base64.b32encode(b"ctf{base32_ok}")
    assert _run(Base32Decoder(), b32)[0].text == "ctf{base32_ok}"

    hexed = b"ctf{hex_ok}".hex().encode()
    assert _run(HexDecoder(), hexed)[0].text == "ctf{hex_ok}"

    b85 = base64.b85encode(b"ctf{base85_ok}")
    assert _run(Base85Decoder(), b85)[0].text == "ctf{base85_ok}"


def _rot47(value: str) -> str:
    return "".join(
        chr(33 + ((ord(char) - 33 + 47) % 94)) if 33 <= ord(char) <= 126 else char for char in value
    )


def test_url_rot13_rot47_and_reverse() -> None:
    assert _run(UrlDecoder(), b"ctf%7Burl_ok%7D")[0].text == "ctf{url_ok}"
    assert "ctf{rot13_ok}" in _run(Rot13Decoder(), b"pgs{ebg13_bx}")[0].text
    transformed = _rot47("ctf{rot47_ok}").encode()
    assert _run(Rot47Decoder(), transformed)[0].text == "ctf{rot47_ok}"
    assert _run(ReverseDecoder(), b"}ko_esrever{ftc")[0].text == "ctf{reverse_ok}"


def test_compression_decoders() -> None:
    data = b"compressed ctf{codec_ok}"
    assert _run(GzipDecoder(), gzip.compress(data))[0].text == data.decode()
    assert _run(ZlibDecoder(), zlib.compress(data))[0].text == data.decode()
    o = Bzip2Decoder()
    assert _run(o, bz2.compress(data), kind=ArtifactKind.BYTES)[0].text == data.decode()
    x = XzDecoder()
    assert _run(x, lzma.compress(data), kind=ArtifactKind.BYTES)[0].text == data.decode()


def test_decoders_skip_wrong_magic_and_oversized_output() -> None:
    with pytest.raises(PluginSkip):
        _run(GzipDecoder(), b"not gzip", kind=ArtifactKind.BYTES)
    with pytest.raises(PluginSkip):
        _run(ZlibDecoder(), b"not zlib", kind=ArtifactKind.BYTES)
    with pytest.raises(PluginSkip):
        _run(ZlibDecoder(), zlib.compress(b"x" * 5000), max_bytes=100)


def test_xor_finds_flag_candidate() -> None:
    plaintext = b"prefix ctf{xor_decoder_ok} suffix"
    encoded = bytes(byte ^ 0x42 for byte in plaintext)
    artifacts = _run(
        XorBruteDecoder(),
        encoded,
        kind=ArtifactKind.BYTES,
        options={"max_candidates": 8},
    )
    assert any("ctf{xor_decoder_ok}" in artifact.text for artifact in artifacts)
    matching = next(artifact for artifact in artifacts if "ctf{xor_decoder_ok}" in artifact.text)
    assert matching.params["key"] == 0x42


def test_xor_ignores_text_and_images() -> None:
    xor = XorBruteDecoder()
    text_artifact = make_artifact(kind=ArtifactKind.TEXT, name="t", payload=b"ctf{text}")
    image_artifact = make_artifact(
        kind=ArtifactKind.IMAGE,
        name="i",
        payload=b"png",
        media_type="image/png",
    )
    assert not xor.accepts(text_artifact)
    assert not xor.accepts(image_artifact)
