"""Carving bridges for ``binwalk`` and ``foremost``."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from nvox.api import Context, PluginKind, register
from nvox.core.external import OutputPolicy
from nvox.core.sandbox import CommandResult
from nvox.plugins.bridges.common import ExternalBridge


@register
class BinwalkBridge(ExternalBridge):
    """Run ``binwalk`` signature analysis (optionally extracting)."""

    name: ClassVar[str] = "bridge.binwalk"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.EXTRACTOR
    tool: ClassVar[str] = "binwalk"
    binary: ClassVar[str] = "binwalk"
    requires: ClassVar[tuple[str, ...]] = ("binwalk",)
    targets: ClassVar[tuple[str, ...]] = ("*/*",)
    priority: ClassVar[int] = 70
    description: ClassVar[str] = "Scan embedded signatures with binwalk (optional)."
    max_depth: ClassVar[int] = 2
    timeout: ClassVar[float] = 60.0
    max_output_bytes: ClassVar[int] = 2 * 1024 * 1024
    options: ClassVar[dict[str, object]] = {"extract": False}

    def build_argv(self, input_path: Path, output_dir: Path, ctx: Context) -> list[str]:
        argv = ["binwalk"]
        if bool(ctx.option("extract", False)):
            argv.extend(["-e", "-C", str(output_dir)])
        argv.append(str(input_path))
        return argv

    def handle(
        self,
        result: CommandResult,
        input_path: Path,
        output_dir: Path,
        ctx: Context,
    ) -> None:
        collection = None
        if bool(ctx.option("extract", False)):
            collection = self.emit_files(ctx, output_dir)
        emitted = self.emit_text(ctx, "report", result, fallback_to_stderr=True)
        if not emitted and not (collection and collection.files):
            if result.returncode == 0:
                ctx.skip("binwalk found no embedded signatures")
            ctx.skip(self.failure_reason(result, "binwalk produced no output"))


@register
class ForemostBridge(ExternalBridge):
    """Carve files with ``foremost`` into a bounded temporary directory."""

    name: ClassVar[str] = "bridge.foremost"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.EXTRACTOR
    tool: ClassVar[str] = "foremost"
    binary: ClassVar[str] = "foremost"
    requires: ClassVar[tuple[str, ...]] = ("foremost",)
    targets: ClassVar[tuple[str, ...]] = ("*/*",)
    priority: ClassVar[int] = 72
    description: ClassVar[str] = "Carve common file types with foremost (optional)."
    max_depth: ClassVar[int] = 2
    timeout: ClassVar[float] = 60.0
    max_output_bytes: ClassVar[int] = 1 * 1024 * 1024
    options: ClassVar[dict[str, object]] = {"types": ""}
    output_policy: ClassVar[OutputPolicy] = OutputPolicy(max_files=500)

    def build_argv(self, input_path: Path, output_dir: Path, ctx: Context) -> list[str]:
        argv = ["foremost", "-q", "-i", str(input_path), "-o", str(output_dir)]
        types = str(ctx.option("types", "")).strip()
        if types:
            argv.extend(["-t", types])
        return argv

    def handle(
        self,
        result: CommandResult,
        input_path: Path,
        output_dir: Path,
        ctx: Context,
    ) -> None:
        emitted = self.emit_text(ctx, "report", result)
        collection = self.emit_files(ctx, output_dir)
        if collection or emitted:
            return
        if result.returncode == 0:
            ctx.skip("foremost found no files")
        ctx.skip(self.failure_reason(result, "foremost produced no files"))
