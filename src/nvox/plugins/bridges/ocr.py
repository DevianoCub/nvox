"""Barcode and OCR bridges for ``zbarimg`` and ``tesseract``."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from nvox.api import Context, PluginKind, register
from nvox.core.sandbox import CommandResult
from nvox.plugins.bridges.common import ExternalBridge


@register
class ZbarBridge(ExternalBridge):
    """Decode QR codes and barcodes with ``zbarimg``."""

    name: ClassVar[str] = "bridge.zbar"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    tool: ClassVar[str] = "zbar"
    binary: ClassVar[str] = "zbarimg"
    requires: ClassVar[tuple[str, ...]] = ("zbarimg",)
    targets: ClassVar[tuple[str, ...]] = ("image/*",)
    priority: ClassVar[int] = 30
    description: ClassVar[str] = "Decode QR codes and barcodes with zbarimg (optional)."
    timeout: ClassVar[float] = 30.0
    max_output_bytes: ClassVar[int] = 512 * 1024
    max_depth: ClassVar[int] = 2

    def build_argv(self, input_path: Path, output_dir: Path, ctx: Context) -> list[str]:
        return ["zbarimg", "-q", "--raw", str(input_path)]

    def handle(
        self,
        result: CommandResult,
        input_path: Path,
        output_dir: Path,
        ctx: Context,
    ) -> None:
        if self.emit_text(ctx, "decoded", result):
            return
        if result.returncode in (0, 4):
            ctx.skip("zbarimg found no barcodes or QR codes")
        ctx.skip(self.failure_reason(result, "zbarimg produced no output"))


@register
class TesseractBridge(ExternalBridge):
    """Recognise text in images with ``tesseract``."""

    name: ClassVar[str] = "bridge.tesseract"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    tool: ClassVar[str] = "tesseract"
    binary: ClassVar[str] = "tesseract"
    requires: ClassVar[tuple[str, ...]] = ("tesseract",)
    targets: ClassVar[tuple[str, ...]] = ("image/*",)
    priority: ClassVar[int] = 25
    description: ClassVar[str] = "OCR image text with tesseract (optional)."
    timeout: ClassVar[float] = 60.0
    max_output_bytes: ClassVar[int] = 2 * 1024 * 1024
    max_depth: ClassVar[int] = 2
    options: ClassVar[dict[str, object]] = {"lang": "", "psm": 6}

    def build_argv(self, input_path: Path, output_dir: Path, ctx: Context) -> list[str]:
        argv = ["tesseract", str(input_path), "stdout"]
        lang = str(ctx.option("lang", "")).strip()
        if lang:
            argv.extend(["-l", lang])
        psm = ctx.option("psm", 6)
        if psm is not None and str(psm).strip():
            argv.extend(["--psm", str(psm)])
        return argv

    def handle(
        self,
        result: CommandResult,
        input_path: Path,
        output_dir: Path,
        ctx: Context,
    ) -> None:
        if self.emit_text(ctx, "text", result, fallback_to_stderr=False):
            return
        if result.returncode == 0:
            ctx.skip("tesseract found no text")
        ctx.skip(self.failure_reason(result, "tesseract produced no text"))
