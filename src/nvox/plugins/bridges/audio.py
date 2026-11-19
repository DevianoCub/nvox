"""Optional audio bridges for ffprobe and SoX."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from nvox.api import Context, PluginKind, register
from nvox.core.sandbox import CommandResult
from nvox.plugins.bridges.common import ExternalBridge


@register
class FfprobeBridge(ExternalBridge):
    """Read stream and format metadata through ``ffprobe -show_format``."""

    name: ClassVar[str] = "bridge.ffprobe"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    tool: ClassVar[str] = "ffprobe"
    binary: ClassVar[str] = "ffprobe"
    requires: ClassVar[tuple[str, ...]] = ("ffprobe",)
    targets: ClassVar[tuple[str, ...]] = ("audio/*", "video/*")
    priority: ClassVar[int] = 12
    description: ClassVar[str] = "Read audio/video stream metadata with ffprobe (optional)."
    timeout: ClassVar[float] = 30.0
    max_output_bytes: ClassVar[int] = 4 * 1024 * 1024

    def build_argv(self, input_path: Path, output_dir: Path, ctx: Context) -> list[str]:
        return [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(input_path),
        ]

    def handle(
        self,
        result: CommandResult,
        input_path: Path,
        output_dir: Path,
        ctx: Context,
    ) -> None:
        if not result.stdout:
            ctx.skip(self.failure_reason(result, "ffprobe produced no metadata"))
        if result.stdout_truncated:
            ctx.skip("ffprobe output was truncated before it could be parsed as JSON")
        try:
            decoded = json.loads(result.stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            ctx.skip(f"ffprobe produced invalid JSON: {exc}")
        if not isinstance(decoded, dict):
            ctx.skip("ffprobe returned an unexpected JSON value")
        ctx.meta("ffprobe/metadata", decoded, returncode=result.returncode)


@register
class SoxBridge(ExternalBridge):
    """Report audio statistics with SoX's ``stat`` effect."""

    name: ClassVar[str] = "bridge.sox"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    tool: ClassVar[str] = "sox"
    binary: ClassVar[str] = "sox"
    requires: ClassVar[tuple[str, ...]] = ("sox",)
    targets: ClassVar[tuple[str, ...]] = ("audio/*",)
    priority: ClassVar[int] = 13
    description: ClassVar[str] = "Report audio statistics with SoX (optional)."
    timeout: ClassVar[float] = 30.0
    max_output_bytes: ClassVar[int] = 2 * 1024 * 1024
    options: ClassVar[dict[str, object]] = {"mode": "stat"}

    def build_argv(self, input_path: Path, output_dir: Path, ctx: Context) -> list[str]:
        mode = str(ctx.option("mode", "stat")).strip().lower()
        if mode == "info":
            return ["sox", "--i", str(input_path)]
        if mode != "stat":
            ctx.skip("sox: mode must be 'stat' or 'info'")
        return ["sox", str(input_path), "-n", "stat"]

    def handle(
        self,
        result: CommandResult,
        input_path: Path,
        output_dir: Path,
        ctx: Context,
    ) -> None:
        if not self.emit_text(ctx, "report", result, fallback_to_stderr=True):
            ctx.skip(self.failure_reason(result, "sox produced no report"))


__all__ = ["FfprobeBridge", "SoxBridge"]
