"""Steganography bridges for zsteg, steghide, stegseek, outguess and StegSolve."""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from nvox.api import Context, PluginKind, register
from nvox.core.sandbox import CommandResult, SandboxError, run_command
from nvox.plugins.bridges.common import ExternalBridge

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _render(part: str, values: dict[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda match: values.get(match.group(1), match.group(0)), part)


@register
class ZstegBridge(ExternalBridge):
    """Run ``zsteg -a`` against PNG and BMP images."""

    name: ClassVar[str] = "bridge.zsteg"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    tool: ClassVar[str] = "zsteg"
    binary: ClassVar[str] = "zsteg"
    requires: ClassVar[tuple[str, ...]] = ("zsteg",)
    targets: ClassVar[tuple[str, ...]] = ("image/png", "image/bmp", "image/x-ms-bmp")
    priority: ClassVar[int] = 45
    description: ClassVar[str] = "Detect PNG/BMP LSB steganography with zsteg (optional)."
    timeout: ClassVar[float] = 60.0
    max_output_bytes: ClassVar[int] = 2 * 1024 * 1024

    def build_argv(self, input_path: Path, output_dir: Path, ctx: Context) -> list[str]:
        return ["zsteg", "-a", str(input_path)]

    def handle(
        self,
        result: CommandResult,
        input_path: Path,
        output_dir: Path,
        ctx: Context,
    ) -> None:
        if self.emit_text(ctx, "report", result, fallback_to_stderr=True):
            return
        if result.returncode == 0:
            ctx.skip("zsteg found no LSB data")
        ctx.skip(self.failure_reason(result, "zsteg produced no output"))


@register
class SteghideBridge(ExternalBridge):
    """Probe and extract steghide payloads using an optional passphrase."""

    name: ClassVar[str] = "bridge.steghide"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.EXTRACTOR
    tool: ClassVar[str] = "steghide"
    binary: ClassVar[str] = "steghide"
    requires: ClassVar[tuple[str, ...]] = ("steghide",)
    targets: ClassVar[tuple[str, ...]] = (
        "image/jpeg",
        "image/bmp",
        "image/x-ms-bmp",
        "audio/wav",
        "audio/x-wav",
        "audio/au",
        "audio/basic",
    )
    priority: ClassVar[int] = 46
    description: ClassVar[str] = "Probe and extract steghide payloads (optional)."
    timeout: ClassVar[float] = 30.0
    max_output_bytes: ClassVar[int] = 2 * 1024 * 1024
    options: ClassVar[dict[str, object]] = {"passphrase": ""}

    def build_argv(self, input_path: Path, output_dir: Path, ctx: Context) -> list[str]:
        passphrase = str(ctx.option("passphrase", ""))
        return ["steghide", "info", "-sf", str(input_path), "-p", passphrase]

    def handle(
        self,
        result: CommandResult,
        input_path: Path,
        output_dir: Path,
        ctx: Context,
    ) -> None:
        if result.returncode != 0:
            emitted_info = self.emit_text(ctx, "info", result)
            if emitted_info:
                return
            ctx.skip(self.failure_reason(result, "steghide could not read the artifact"))
        passphrase = str(ctx.option("passphrase", ""))
        output_path = output_dir / "steghide.out"
        try:
            extraction = run_command(
                [
                    "steghide",
                    "extract",
                    "-sf",
                    str(input_path),
                    "-p",
                    passphrase,
                    "-xf",
                    str(output_path),
                    "-f",
                ],
                cwd=output_dir,
                env=self.environment(output_dir.parent),
                limits=self.sandbox_limits(),
            )
        except SandboxError as exc:
            ctx.skip(f"steghide: {exc}")
        if extraction.timed_out:
            ctx.skip(f"steghide extraction timed out after {self.timeout:g}s")
        collection = self.emit_files(ctx, output_dir)
        emitted_info = self.emit_text(ctx, "info", result)
        if collection or emitted_info:
            return
        ctx.skip(self.failure_reason(extraction, "steghide extracted no payload"))


@register
class StegseekBridge(ExternalBridge):
    """Crack a steghide passphrase with ``stegseek`` and a configured wordlist."""

    name: ClassVar[str] = "bridge.stegseek"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.EXTRACTOR
    tool: ClassVar[str] = "stegseek"
    binary: ClassVar[str] = "stegseek"
    requires: ClassVar[tuple[str, ...]] = ("stegseek",)
    targets: ClassVar[tuple[str, ...]] = (
        "image/jpeg",
        "image/bmp",
        "image/x-ms-bmp",
        "audio/wav",
        "audio/x-wav",
        "audio/au",
        "audio/basic",
    )
    priority: ClassVar[int] = 47
    description: ClassVar[str] = (
        "Crack steghide passphrases with stegseek and a wordlist (optional)."
    )
    timeout: ClassVar[float] = 120.0
    max_output_bytes: ClassVar[int] = 2 * 1024 * 1024
    options: ClassVar[dict[str, object]] = {"wordlist": ""}

    def build_argv(self, input_path: Path, output_dir: Path, ctx: Context) -> list[str]:
        wordlist_raw = str(ctx.option("wordlist", "")).strip()
        if not wordlist_raw:
            ctx.skip('stegseek: set plugins.options["bridge.stegseek"].wordlist')
        wordlist = Path(wordlist_raw).expanduser()
        if not wordlist.is_file():
            ctx.skip(f"stegseek: wordlist is not a file: {wordlist}")
        # The child runs inside the temporary workspace, so a relative wordlist
        # must be resolved before it is handed to stegseek.
        wordlist = wordlist.resolve()
        output_path = output_dir / "stegseek.out"
        return ["stegseek", "-q", "-o", str(output_path), str(input_path), str(wordlist)]

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
            ctx.skip("stegseek found no embedded file")
        ctx.skip(self.failure_reason(result, "stegseek produced no output"))


@register
class OutguessBridge(ExternalBridge):
    """Extract outguess payloads from JPEG images."""

    name: ClassVar[str] = "bridge.outguess"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.EXTRACTOR
    tool: ClassVar[str] = "outguess"
    binary: ClassVar[str] = "outguess"
    requires: ClassVar[tuple[str, ...]] = ("outguess",)
    targets: ClassVar[tuple[str, ...]] = ("image/jpeg",)
    priority: ClassVar[int] = 48
    description: ClassVar[str] = "Extract outguess JPEG payloads (optional)."
    timeout: ClassVar[float] = 30.0
    max_output_bytes: ClassVar[int] = 2 * 1024 * 1024
    options: ClassVar[dict[str, object]] = {"key": ""}

    def build_argv(self, input_path: Path, output_dir: Path, ctx: Context) -> list[str]:
        argv = ["outguess"]
        key = str(ctx.option("key", ""))
        if key:
            argv.extend(["-k", key])
        argv.extend(["-r", str(input_path), str(output_dir / "outguess.out")])
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
            ctx.skip("outguess found no embedded data")
        ctx.skip(self.failure_reason(result, "outguess produced no output"))


@register
class StegSolveBridge(ExternalBridge):
    """Run a configured non-GUI StegSolve command or report the GUI limitation.

    StegSolve itself is an interactive Java GUI. The bridge therefore accepts a
    ``command`` option for headless forks or wrappers; the original jar cannot
    be driven meaningfully by an automated scan.
    """

    name: ClassVar[str] = "bridge.stegsolve"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    tool: ClassVar[str] = "stegsolve"
    binary: ClassVar[str] = "java"
    requires: ClassVar[tuple[str, ...]] = ("java",)
    targets: ClassVar[tuple[str, ...]] = ("image/*",)
    priority: ClassVar[int] = 44
    description: ClassVar[str] = "Run a configured headless StegSolve command (optional)."
    timeout: ClassVar[float] = 30.0
    max_output_bytes: ClassVar[int] = 2 * 1024 * 1024
    max_depth: ClassVar[int] = 2
    options: ClassVar[dict[str, object]] = {"jar": "", "command": []}

    def build_argv(self, input_path: Path, output_dir: Path, ctx: Context) -> list[str]:
        raw = ctx.option("command", [])
        if raw:
            if (
                not isinstance(raw, list)
                or not raw
                or not all(isinstance(part, str) and "\x00" not in part for part in raw)
                or not raw[0]
            ):
                ctx.skip("stegsolve: command must be a non-empty list of strings")
            values = {
                "input": str(input_path),
                "input_name": input_path.name,
                "outdir": str(output_dir),
            }
            return [_render(part, values) for part in raw]

        jar_raw = str(ctx.option("jar", "")).strip()
        if not jar_raw:
            ctx.skip('stegsolve: set plugins.options["bridge.stegsolve"].jar or command')
        jar = Path(jar_raw).expanduser()
        if not jar.is_file():
            ctx.skip(f"stegsolve: jar is not a file: {jar}")
        ctx.skip(
            'stegsolve is GUI-only; set plugins.options["bridge.stegsolve"].command '
            "to a headless wrapper that writes output below {outdir}"
        )

    def handle(
        self,
        result: CommandResult,
        input_path: Path,
        output_dir: Path,
        ctx: Context,
    ) -> None:
        emitted = self.emit_text(ctx, "report", result, fallback_to_stderr=True)
        collection = self.emit_files(ctx, output_dir)
        if emitted or collection:
            return
        ctx.skip(self.failure_reason(result, "stegsolve produced no output"))
