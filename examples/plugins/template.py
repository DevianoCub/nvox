"""Copy-paste template for a local Nvox Python plugin.

Drop a copy into ``plugins/`` or ``~/.config/nvox/plugins``.  The conformance
test at ``tests/test_plugin_conformance.py`` loads this exact file, so keep the
manifest and the ``run`` contract intact when using it as a starting point.
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


@register
class TemplatePlugin(Analyzer):
    """Find a marker and emit a small metadata artifact."""

    name: ClassVar[str] = "example.template"
    version: ClassVar[str] = "0.1.0"
    api_version: ClassVar[int] = 1
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("text/plain", "application/octet-stream")
    priority: ClassVar[int] = 10
    requires: ClassVar[tuple[str, ...]] = ()
    description: ClassVar[str] = "Template plugin: locate a configurable marker."
    options: ClassVar[dict[str, object]] = {
        "marker": "ctf{",
        "emit_preview": True,
        "preview_bytes": 64,
    }

    def accepts(self, artifact: Artifact) -> bool:
        # Narrow the MIME targets without duplicating the matching logic.
        if artifact.kind is ArtifactKind.IMAGE:
            return False
        return super().accepts(artifact)

    def run(self, artifact: Artifact, ctx: Context) -> None:
        marker = str(ctx.option("marker", "ctf{")).encode()
        offset = artifact.payload.find(marker)
        if offset < 0:
            ctx.skip(f"marker {marker!r} not found")
        ctx.meta(
            "example/template",
            {
                "marker": marker.decode(errors="replace"),
                "offset": offset,
                "artifact_size": artifact.size,
            },
            technique="template",
        )
        if bool(ctx.option("emit_preview", True)):
            size = max(0, int(ctx.option("preview_bytes", 64)))
            preview = artifact.payload[offset : offset + size]
            ctx.text("example/preview", preview.decode("utf-8", errors="replace"))
