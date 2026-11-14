"""Text decoders: base encodings, URL escaping, ROT and reversal."""

from __future__ import annotations

import base64
import binascii
import codecs
import re
import urllib.parse
from typing import ClassVar

from nvox.api import Artifact, Context, PluginKind, register
from nvox.plugins.decoders.common import (
    BaseDecoder,
    clean_text,
    emit_decoded,
    looks_like_text,
    looks_meaningful,
    printable_ratio,
)

_B64_STANDARD = re.compile(r"[A-Za-z0-9+/]+={0,2}")
_B64_URL = re.compile(r"[A-Za-z0-9_-]+={0,2}")
_B32 = re.compile(r"[A-Z2-7]+={0,6}", re.IGNORECASE)
_HEX = re.compile(r"[0-9A-Fa-f]+")
_URL_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")


def _b64decode(value: str, *, url_safe: bool = False) -> bytes | None:
    if url_safe:
        if "-" not in value and "_" not in value:
            return None
        if _B64_URL.fullmatch(value) is None:
            return None
        normalised = value.replace("-", "+").replace("_", "/")
    else:
        if _B64_STANDARD.fullmatch(value) is None:
            return None
        normalised = value
    if "=" in normalised:
        if len(normalised) % 4:
            return None
    else:
        remainder = len(normalised) % 4
        if remainder == 1:
            return None
        normalised += "=" * ((4 - remainder) % 4)
    try:
        return base64.b64decode(normalised, validate=True)
    except (binascii.Error, ValueError):
        return None


@register
class Base64Decoder(BaseDecoder):
    name: ClassVar[str] = "decoder.base64"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.DECODER
    description: ClassVar[str] = "Decode standard base64 text."
    priority: ClassVar[int] = 32

    def run(self, artifact: Artifact, ctx: Context) -> None:
        value = clean_text(artifact.payload.decode("latin-1"))
        if len(value) < 8:
            ctx.skip("base64: candidate too short")
        decoded = _b64decode(value)
        if decoded is None or not looks_meaningful(decoded):
            ctx.skip("base64: not valid or not meaningful")
        emit_decoded(ctx, "base64", decoded, codec="base64")


@register
class Base64UrlDecoder(BaseDecoder):
    name: ClassVar[str] = "decoder.base64url"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.DECODER
    description: ClassVar[str] = "Decode URL-safe base64 text."
    priority: ClassVar[int] = 31

    def run(self, artifact: Artifact, ctx: Context) -> None:
        value = clean_text(artifact.payload.decode("latin-1"))
        if len(value) < 8:
            ctx.skip("base64url: candidate too short")
        decoded = _b64decode(value, url_safe=True)
        if decoded is None or not looks_meaningful(decoded):
            ctx.skip("base64url: not valid or not meaningful")
        emit_decoded(ctx, "base64url", decoded, codec="base64url")


@register
class Base32Decoder(BaseDecoder):
    name: ClassVar[str] = "decoder.base32"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.DECODER
    description: ClassVar[str] = "Decode RFC 4648 base32 text."
    priority: ClassVar[int] = 30

    def run(self, artifact: Artifact, ctx: Context) -> None:
        value = clean_text(artifact.payload.decode("latin-1")).upper()
        if len(value) < 8 or _B32.fullmatch(value) is None:
            ctx.skip("base32: not a base32 candidate")
        padded = value
        if "=" in padded:
            if len(padded) % 8:
                ctx.skip("base32: invalid padding")
        else:
            padded += "=" * ((8 - len(padded) % 8) % 8)
        try:
            decoded = base64.b32decode(padded, casefold=True)
        except (binascii.Error, ValueError):
            ctx.skip("base32: decode failed")
        if not looks_meaningful(decoded):
            ctx.skip("base32: decoded payload is not meaningful")
        emit_decoded(ctx, "base32", decoded, codec="base32")


@register
class Base85Decoder(BaseDecoder):
    name: ClassVar[str] = "decoder.base85"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.DECODER
    description: ClassVar[str] = "Decode Python base85 (RFC 1924 alphabet) text."
    priority: ClassVar[int] = 29

    def run(self, artifact: Artifact, ctx: Context) -> None:
        value = "".join(artifact.payload.decode("latin-1").split())
        if len(value) < 12:
            ctx.skip("base85: candidate too short")
        try:
            decoded = base64.b85decode(value.encode("ascii"))
        except (binascii.Error, ValueError, UnicodeEncodeError):
            ctx.skip("base85: decode failed")
        if not looks_meaningful(decoded):
            ctx.skip("base85: decoded payload is not meaningful")
        emit_decoded(ctx, "base85", decoded, codec="base85")


@register
class HexDecoder(BaseDecoder):
    name: ClassVar[str] = "decoder.hex"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.DECODER
    description: ClassVar[str] = "Decode hexadecimal text."
    priority: ClassVar[int] = 30

    def run(self, artifact: Artifact, ctx: Context) -> None:
        value = clean_text(artifact.payload.decode("latin-1")).replace(" ", "")
        if len(value) < 8 or len(value) % 2 or _HEX.fullmatch(value) is None:
            ctx.skip("hex: not a hex candidate")
        decoded = bytes.fromhex(value)
        if not looks_meaningful(decoded):
            ctx.skip("hex: decoded payload is not meaningful")
        emit_decoded(ctx, "hex", decoded, codec="hex")


@register
class UrlDecoder(BaseDecoder):
    name: ClassVar[str] = "decoder.url"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.DECODER
    description: ClassVar[str] = "Decode percent-escaped text."
    priority: ClassVar[int] = 28

    def run(self, artifact: Artifact, ctx: Context) -> None:
        value = artifact.payload.decode("latin-1")
        if _URL_ESCAPE.search(value) is None:
            ctx.skip("url: no percent escapes")
        decoded = urllib.parse.unquote_to_bytes(value)
        if decoded == artifact.payload:
            ctx.skip("url: decoding did not change the payload")
        if not looks_meaningful(decoded):
            ctx.skip("url: decoded payload is not meaningful")
        emit_decoded(ctx, "url", decoded, codec="url")


@register
class Rot13Decoder(BaseDecoder):
    name: ClassVar[str] = "decoder.rot13"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.DECODER
    description: ClassVar[str] = "Apply ROT13 to printable text."
    priority: ClassVar[int] = 20

    def run(self, artifact: Artifact, ctx: Context) -> None:
        text = artifact.payload.decode("latin-1")
        if printable_ratio(artifact.payload) < 0.85:
            ctx.skip("rot13: payload is not textual")
        transformed = codecs.decode(text, "rot13")
        if transformed == text:
            ctx.skip("rot13: no letters to rotate")
        ctx.text("rot13", transformed, codec="rot13")


@register
class Rot47Decoder(BaseDecoder):
    name: ClassVar[str] = "decoder.rot47"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.DECODER
    description: ClassVar[str] = "Apply ROT47 to printable ASCII text."
    priority: ClassVar[int] = 20

    def run(self, artifact: Artifact, ctx: Context) -> None:
        text = artifact.payload.decode("latin-1")
        if printable_ratio(artifact.payload) < 0.85:
            ctx.skip("rot47: payload is not textual")
        transformed = "".join(
            chr(33 + ((ord(char) - 33 + 47) % 94)) if 33 <= ord(char) <= 126 else char
            for char in text
        )
        if transformed == text:
            ctx.skip("rot47: nothing to rotate")
        ctx.text("rot47", transformed, codec="rot47")


@register
class ReverseDecoder(BaseDecoder):
    name: ClassVar[str] = "decoder.reverse"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.DECODER
    description: ClassVar[str] = "Reverse printable text."
    priority: ClassVar[int] = 19

    def run(self, artifact: Artifact, ctx: Context) -> None:
        text = artifact.payload.decode("latin-1")
        if not looks_like_text(artifact.payload):
            ctx.skip("reverse: payload is not textual")
        transformed = text[::-1]
        if transformed == text:
            ctx.skip("reverse: nothing to reverse")
        ctx.text("reverse", transformed, codec="reverse")


__all__ = [
    "Base32Decoder",
    "Base64Decoder",
    "Base64UrlDecoder",
    "Base85Decoder",
    "HexDecoder",
    "ReverseDecoder",
    "Rot13Decoder",
    "Rot47Decoder",
    "UrlDecoder",
]
