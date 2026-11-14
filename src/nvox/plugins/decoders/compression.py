"""Compression decoders gated by container magic."""

from __future__ import annotations

import bz2
import lzma
import zlib
from typing import ClassVar

from nvox.api import Artifact, Context, PluginKind, register
from nvox.plugins.decoders.common import BaseDecoder, emit_decoded


def _output_limit(ctx: Context) -> int:
    return ctx.max_artifact_bytes


@register
class GzipDecoder(BaseDecoder):
    name: ClassVar[str] = "decoder.gzip"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.DECODER
    description: ClassVar[str] = "Decompress gzip payloads."
    priority: ClassVar[int] = 26

    def run(self, artifact: Artifact, ctx: Context) -> None:
        data = artifact.payload
        if not data.startswith(b"\x1f\x8b"):
            ctx.skip("gzip: missing magic")
        limit = _output_limit(ctx)
        try:
            decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
            decoded = decompressor.decompress(data, limit + 1)
        except zlib.error:
            ctx.skip("gzip: corrupt stream")
        if len(decoded) > limit or not decompressor.eof:
            ctx.skip("gzip: output exceeds limit or stream is truncated")
        emit_decoded(ctx, "gzip", decoded, codec="gzip")


@register
class ZlibDecoder(BaseDecoder):
    name: ClassVar[str] = "decoder.zlib"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.DECODER
    description: ClassVar[str] = "Decompress zlib payloads."
    priority: ClassVar[int] = 26

    def run(self, artifact: Artifact, ctx: Context) -> None:
        data = artifact.payload
        if len(data) < 2 or data[0] != 0x78 or ((data[0] << 8) | data[1]) % 31:
            ctx.skip("zlib: missing header")
        limit = _output_limit(ctx)
        try:
            decompressor = zlib.decompressobj(zlib.MAX_WBITS)
            decoded = decompressor.decompress(data, limit + 1)
        except zlib.error:
            ctx.skip("zlib: corrupt stream")
        if len(decoded) > limit or not decompressor.eof:
            ctx.skip("zlib: output exceeds limit or stream is truncated")
        emit_decoded(ctx, "zlib", decoded, codec="zlib")


@register
class Bzip2Decoder(BaseDecoder):
    name: ClassVar[str] = "decoder.bzip2"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.DECODER
    description: ClassVar[str] = "Decompress bzip2 payloads."
    priority: ClassVar[int] = 25

    def run(self, artifact: Artifact, ctx: Context) -> None:
        data = artifact.payload
        if not data.startswith(b"BZh"):
            ctx.skip("bzip2: missing magic")
        limit = _output_limit(ctx)
        try:
            decompressor = bz2.BZ2Decompressor()
            decoded = decompressor.decompress(data, max_length=limit + 1)
        except (OSError, EOFError):
            ctx.skip("bzip2: corrupt stream")
        if len(decoded) > limit or not decompressor.eof:
            ctx.skip("bzip2: output exceeds limit or stream is truncated")
        emit_decoded(ctx, "bzip2", decoded, codec="bzip2")


@register
class XzDecoder(BaseDecoder):
    name: ClassVar[str] = "decoder.xz"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.DECODER
    description: ClassVar[str] = "Decompress xz/LZMA payloads."
    priority: ClassVar[int] = 25

    def run(self, artifact: Artifact, ctx: Context) -> None:
        data = artifact.payload
        if not data.startswith(b"\xfd7zXZ\x00"):
            ctx.skip("xz: missing magic")
        limit = _output_limit(ctx)
        try:
            decompressor = lzma.LZMADecompressor()
            decoded = decompressor.decompress(data, max_length=limit + 1)
        except (lzma.LZMAError, EOFError):
            ctx.skip("xz: corrupt stream")
        if len(decoded) > limit or not decompressor.eof:
            ctx.skip("xz: output exceeds limit or stream is truncated")
        emit_decoded(ctx, "xz", decoded, codec="xz")


__all__ = ["Bzip2Decoder", "GzipDecoder", "XzDecoder", "ZlibDecoder"]
