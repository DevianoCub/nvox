"""Extract printable strings from binary artifacts.

The reference M1 plugin now builds on :mod:`nvox.core.surfaces`, which is also
used by the flag engine. That keeps offsets and encoding rules consistent
between ``nvox scan`` and the strings plugin.
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
from nvox.core.surfaces import ascii_runs, utf16_runs, utf32_runs

_MAX_STRINGS = 20000
_MAX_CHARS = 1_000_000


@register
class StringsPlugin(Analyzer):
    """Extract ASCII, UTF-16 and UTF-32 printable runs from bytes."""

    name: ClassVar[str] = "generic.strings"
    version: ClassVar[str] = "0.3.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("*/*",)
    priority: ClassVar[int] = 90
    description: ClassVar[str] = "Extract ASCII, UTF-16LE/BE and UTF-32LE/BE printable runs."
    options: ClassVar[dict[str, object]] = {
        "min_length": 4,
        "utf16": True,
        "utf32": True,
    }

    def accepts(self, artifact: Artifact) -> bool:
        if artifact.kind in (ArtifactKind.TEXT, ArtifactKind.META):
            return False
        return super().accepts(artifact)

    def run(self, artifact: Artifact, ctx: Context) -> None:
        min_length = max(2, int(ctx.option("min_length", 4)))
        runs = [run.text for run in ascii_runs(artifact.payload, min_length)]
        if ctx.option("utf16", True):
            for byte_order in ("little", "big"):
                runs.extend(
                    run.text
                    for run in utf16_runs(
                        artifact.payload,
                        min_length,
                        byte_order=byte_order,
                    )
                )
        if ctx.option("utf32", True):
            for byte_order in ("little", "big"):
                runs.extend(
                    run.text
                    for run in utf32_runs(
                        artifact.payload,
                        min_length,
                        byte_order=byte_order,
                    )
                )
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


__all__ = ["StringsPlugin"]
