"""Single-byte XOR brute force with printable-text scoring."""

from __future__ import annotations

import string
from typing import ClassVar

from nvox.api import Artifact, ArtifactKind, Context, PluginKind, register
from nvox.plugins.decoders.common import BaseDecoder

_LETTERS = frozenset(string.ascii_letters.encode("ascii"))
_DIGITS = frozenset(string.digits.encode("ascii"))
_KEYWORDS = (b"ctf", b"flag", b"htb", b"pico", b"secret", b"key")


def _score(candidate: bytes) -> tuple[float, float]:
    if not candidate:
        return 0.0, 0.0
    size = len(candidate)
    printable = sum(1 for byte in candidate if 0x20 <= byte <= 0x7E or byte in (9, 10, 13))
    readable = sum(1 for byte in candidate if byte in _LETTERS or byte in _DIGITS)
    ratio = printable / size
    bonus = 0.0
    if b"{" in candidate and b"}" in candidate:
        bonus += 0.25
    lowered = candidate.lower()
    if any(word in lowered for word in _KEYWORDS):
        bonus += 0.2
    return ratio + (readable / size) * 0.1 + bonus, ratio


@register
class XorBruteDecoder(BaseDecoder):
    name: ClassVar[str] = "decoder.xor"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.DECODER
    description: ClassVar[str] = "Try single-byte XOR keys and keep the most text-like results."
    priority: ClassVar[int] = 10
    options: ClassVar[dict[str, object]] = {
        "max_input_bytes": 16384,
        "max_candidates": 5,
        "min_printable": 0.75,
    }

    def accepts(self, artifact: Artifact) -> bool:
        return artifact.kind is ArtifactKind.BYTES and super().accepts(artifact)

    def run(self, artifact: Artifact, ctx: Context) -> None:
        data = artifact.payload
        max_input = max(1, int(ctx.option("max_input_bytes", 16384)))
        if len(data) < 3:
            ctx.skip("xor: payload too short")
        if len(data) > max_input:
            ctx.skip(f"xor: payload exceeds max_input_bytes ({max_input})")

        min_printable = float(ctx.option("min_printable", 0.75))
        limit = max(1, int(ctx.option("max_candidates", 5)))
        candidates: list[tuple[float, float, int, bytes]] = []
        for key in range(1, 256):
            decoded = bytes(byte ^ key for byte in data)
            score, printable = _score(decoded)
            if printable >= min_printable:
                candidates.append((score, printable, key, decoded))
        if not candidates:
            ctx.skip("xor: no printable candidate")
        candidates.sort(key=lambda item: (item[0], -item[2]), reverse=True)
        for score, _printable, key, decoded in candidates[:limit]:
            ctx.text(
                f"xor/key={key:02x}",
                decoded.decode("latin-1"),
                codec="xor",
                key=key,
                score=round(score, 4),
            )


__all__ = ["XorBruteDecoder"]
