"""Heuristic raw-deflate and embedded-zlib decoding for bitstreams."""

from __future__ import annotations

import zlib
from typing import ClassVar

from nvox.api import Artifact, Context, PluginKind, register
from nvox.plugins.decoders.common import (
    BaseDecoder,
    emit_decoded,
    looks_meaningful,
    printable_ratio,
)


def _zlib_header(data: bytes, offset: int) -> bool:
    if offset + 1 >= len(data):
        return False
    value = (data[offset] << 8) | data[offset + 1]
    return data[offset] & 0x0F == 8 and value % 31 == 0


def _decompress(data: bytes, *, wbits: int, limit: int) -> bytes | None:
    try:
        decompressor = zlib.decompressobj(wbits)
        output = decompressor.decompress(data, limit + 1)
    except zlib.error:
        return None
    if not decompressor.eof or len(output) > limit:
        return None
    return output


@register
class DeflateDecoder(BaseDecoder):
    """Try raw deflate and zlib streams at bounded offsets."""

    name: ClassVar[str] = "decoder.deflate"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.DECODER
    description: ClassVar[str] = "Decode raw deflate streams and embedded zlib streams."
    priority: ClassVar[int] = 24
    options: ClassVar[dict[str, object]] = {
        "max_input_bytes": 8_000_000,
        "max_offsets": 64,
        "min_output": 8,
        "max_candidates": 3,
    }

    def run(self, artifact: Artifact, ctx: Context) -> None:
        data = artifact.payload
        max_input = max(1, int(ctx.option("max_input_bytes", 8_000_000)))
        if len(data) > max_input:
            ctx.skip(f"deflate: input exceeds max_input_bytes ({max_input})")
        limit = ctx.max_artifact_bytes
        min_output = max(1, int(ctx.option("min_output", 8)))
        max_candidates = max(1, int(ctx.option("max_candidates", 3)))
        candidates: list[tuple[float, int, str, bytes]] = []

        raw = _decompress(data, wbits=-15, limit=limit)
        if raw is not None and len(raw) >= min_output and looks_meaningful(raw):
            candidates.append((printable_ratio(raw), 0, "raw-deflate", raw))

        max_offsets = min(len(data) - 1, max(0, int(ctx.option("max_offsets", 64))))
        for offset in range(1, max_offsets):
            if not _zlib_header(data, offset):
                continue
            decoded = _decompress(data[offset:], wbits=zlib.MAX_WBITS, limit=limit)
            if decoded is None or len(decoded) < min_output or not looks_meaningful(decoded):
                continue
            candidates.append((printable_ratio(decoded), offset, "zlib-embedded", decoded))
            if len(candidates) >= max_candidates:
                break

        if not candidates:
            ctx.skip("deflate: no meaningful stream found")
        candidates.sort(key=lambda item: (-item[0], item[1]))
        for _, offset, kind, decoded in candidates[:max_candidates]:
            emit_decoded(
                ctx,
                f"{kind}/offset={offset}",
                decoded,
                codec=kind,
                offset=offset,
            )


__all__ = ["DeflateDecoder"]
