"""Metadata bridge for ExifTool."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from nvox.api import Context, PluginKind, register
from nvox.core.external import OutputPolicy
from nvox.core.sandbox import CommandResult
from nvox.plugins.bridges.common import ExternalBridge


@register
class ExiftoolBridge(ExternalBridge):
    """Run ``exiftool`` and emit its grouped JSON metadata."""

    name: ClassVar[str] = "bridge.exiftool"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    tool: ClassVar[str] = "exiftool"
    binary: ClassVar[str] = "exiftool"
    requires: ClassVar[tuple[str, ...]] = ("exiftool",)
    targets: ClassVar[tuple[str, ...]] = (
        "image/*",
        "audio/*",
        "video/*",
        "application/pdf",
        "application/zip",
        "application/x-7z-compressed",
    )
    priority: ClassVar[int] = 15
    description: ClassVar[str] = "Read broad metadata with ExifTool (optional)."
    timeout: ClassVar[float] = 30.0
    max_output_bytes: ClassVar[int] = 4 * 1024 * 1024
    output_policy: ClassVar[OutputPolicy] = OutputPolicy(
        max_files=16, max_total_bytes=8 * 1024 * 1024
    )

    def build_argv(self, input_path: Path, output_dir: Path, ctx: Context) -> list[str]:
        return [
            "exiftool",
            "-json",
            "-G1",
            "-s",
            "-n",
            "-charset",
            "filename=UTF8",
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
            ctx.skip(self.failure_reason(result, "exiftool produced no metadata"))
        if result.stdout_truncated:
            ctx.skip("exiftool output was truncated before it could be parsed as JSON")
        try:
            decoded = json.loads(result.stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            ctx.skip(f"exiftool produced invalid JSON: {exc}")
        if not isinstance(decoded, list) or not decoded:
            ctx.skip("exiftool found no metadata")
        entries: list[Mapping[str, Any]] = [
            entry for entry in decoded if isinstance(entry, Mapping)
        ]
        if not entries:
            ctx.skip("exiftool returned no metadata objects")
        ctx.meta(
            "exiftool/metadata",
            {"tool": "exiftool", "entries": entries},
            returncode=result.returncode,
        )
