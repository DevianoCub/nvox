"""Extract printable strings from binary artifacts.

This is the M1 reference plugin: it demonstrates the artifact-free emission
style, options, and cooperative skipping.
"""

from __future__ import annotations

from typing import ClassVar

from nvox.api import (
    Analyzer,
    Artifact,
    ArtifactKind,
    Context,
    PluginKind,
    register,
)

_MAX_STRINGS = 20000
_MAX_CHARS = 1_000_000
_PRINTABLE = frozenset(range(0x20, 0x7F))


def _ascii_runs(data: bytes, min_length: int) -> list[str]:
    runs: list[str] = []
    current = bytearray()
    for byte in data:
        if byte in _PRINTABLE or byte == 0x09:
            current.append(byte)
            continue
        if len(current) >= min_length:
            runs.append(current.decode("ascii", errors="replace"))
        current.clear()
    if len(current) >= min_length:
        runs.append(current.decode("ascii", errors="replace"))
    return runs


def _utf16le_runs(data: bytes, min_length: int) -> list[str]:
    runs: list[str] = []
    for offset in (0, 1):
        current: list[str] = []
        limit = len(data) - 1
        index = offset
        while index < limit:
            low = data[index]
            high = data[index + 1]
            if high == 0 and low in _PRINTABLE:
                current.append(chr(low))
            else:
                if len(current) >= min_length:
                    runs.append("".join(current))
                current.clear()
            index += 2
        if len(current) >= min_length:
            runs.append("".join(current))
    return runs


@register
class StringsPlugin(Analyzer):
    """Extract ASCII and UTF-16LE printable runs from bytes."""

    name: ClassVar[str] = "generic.strings"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("*/*",)
    priority: ClassVar[int] = 90
    description: ClassVar[str] = "Extract ASCII and UTF-16LE printable runs from bytes."
    options: ClassVar[dict[str, object]] = {"min_length": 4, "utf16": True}

    def accepts(self, artifact: Artifact) -> bool:
        if artifact.kind in (ArtifactKind.TEXT, ArtifactKind.META):
            return False
        return super().accepts(artifact)

    def run(self, artifact: Artifact, ctx: Context) -> None:
        min_length = max(2, int(ctx.option("min_length", 4)))
        runs = _ascii_runs(artifact.payload, min_length)
        if ctx.option("utf16", True):
            runs.extend(_utf16le_runs(artifact.payload, min_length))
        if not runs:
            ctx.skip("no printable strings found")

        seen: set[str] = set()
        unique: list[str] = []
        total = 0
        for run in runs:
            if run in seen:
                continue
            seen.add(run)
            unique.append(run)
            total += len(run) + 1
            if len(unique) >= _MAX_STRINGS or total >= _MAX_CHARS:
                break
        if not unique:
            ctx.skip("no printable strings found")
        ctx.text("strings", "\n".join(unique), min_length=min_length)
