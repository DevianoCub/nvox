"""PNG validation bridge for ``pngcheck``."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from nvox.api import Context, PluginKind, register
from nvox.core.sandbox import CommandResult
from nvox.plugins.bridges.common import ExternalBridge


@register
class PngcheckBridge(ExternalBridge):
    """Run ``pngcheck -v`` and keep its structural report."""

    name: ClassVar[str] = "bridge.pngcheck"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    tool: ClassVar[str] = "pngcheck"
    binary: ClassVar[str] = "pngcheck"
    requires: ClassVar[tuple[str, ...]] = ("pngcheck",)
    targets: ClassVar[tuple[str, ...]] = ("image/png",)
    priority: ClassVar[int] = 35
    description: ClassVar[str] = "Validate PNG chunks and report errors with pngcheck (optional)."
    timeout: ClassVar[float] = 30.0

    def build_argv(self, input_path: Path, output_dir: Path, ctx: Context) -> list[str]:
        return ["pngcheck", "-v", str(input_path)]

    def handle(
        self,
        result: CommandResult,
        input_path: Path,
        output_dir: Path,
        ctx: Context,
    ) -> None:
        if not self.emit_text(ctx, "report", result, fallback_to_stderr=True):
            ctx.skip(self.failure_reason(result, "pngcheck produced no report"))
