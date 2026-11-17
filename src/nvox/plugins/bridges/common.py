"""Shared base class for optional external-tool bridges."""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from nvox.api import Analyzer, Artifact, ArtifactKind, Context, PluginKind
from nvox.core.external import (
    OutputCollection,
    OutputError,
    OutputPolicy,
    build_child_environment,
    collect_output_files,
    safe_input_name,
)
from nvox.core.sandbox import (
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TIMEOUT,
    CommandResult,
    SandboxError,
    SandboxLimits,
    run_command,
)


class ExternalBridge(Analyzer):
    """Analyzer that runs one optional executable on a temporary input file.

    Subclasses provide ``tool`` (the stable plugin label), ``binary`` (looked up
    by the engine through ``requires``), ``build_argv`` and ``handle``. The base
    class owns the temporary workspace, environment, timeout, output caps and
    process-group cleanup so every bridge behaves consistently.
    """

    name: ClassVar[str] = ""
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ()
    priority: ClassVar[int] = 50
    requires: ClassVar[tuple[str, ...]] = ()
    description: ClassVar[str] = ""

    tool: ClassVar[str] = ""
    binary: ClassVar[str] = ""
    timeout: ClassVar[float] = DEFAULT_TIMEOUT
    max_output_bytes: ClassVar[int] = DEFAULT_MAX_OUTPUT_BYTES
    output_policy: ClassVar[OutputPolicy] = OutputPolicy()
    env: ClassVar[Mapping[str, str]] = {}
    inherit_env: ClassVar[bool] = False
    max_depth: ClassVar[int | None] = None

    def accepts(self, artifact: Artifact) -> bool:
        if artifact.kind in (ArtifactKind.TEXT, ArtifactKind.META):
            return False
        if self.max_depth is not None and artifact.depth > self.max_depth:
            return False
        return super().accepts(artifact)

    def run(self, artifact: Artifact, ctx: Context) -> None:
        if not self.tool or not self.binary:
            ctx.skip(f"{self.name or 'external bridge'} is not configured")
        with tempfile.TemporaryDirectory(prefix=f"nvox-{self.tool}-") as directory:
            workspace = Path(directory)
            input_dir = workspace / "input"
            output_dir = workspace / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            input_path = input_dir / safe_input_name(artifact.name, artifact.media_type)
            input_path.write_bytes(artifact.payload)

            try:
                argv = self.build_argv(input_path, output_dir, ctx)
                result = run_command(
                    argv,
                    cwd=output_dir,
                    env=self.environment(workspace),
                    limits=self.sandbox_limits(),
                )
            except (SandboxError, OutputError) as exc:
                ctx.skip(f"{self.tool}: {exc}")

            if result.timed_out:
                ctx.skip(f"{self.tool} timed out after {self.timeout:g}s")
            if result.error:
                ctx.skip(f"{self.tool} output could not be read: {result.error}")
            self.handle(result, input_path, output_dir, ctx)

    def sandbox_limits(self) -> SandboxLimits:
        return SandboxLimits(
            timeout=self.timeout,
            max_output_bytes=self.max_output_bytes,
            max_file_bytes=self.output_policy.max_file_bytes,
        )

    def environment(self, workspace: Path) -> dict[str, str]:
        return build_child_environment(
            workspace,
            extra=self.env,
            inherit=self.inherit_env,
        )

    def build_argv(
        self,
        input_path: Path,
        output_dir: Path,
        ctx: Context,
    ) -> list[str]:
        """Return the command to execute."""
        raise NotImplementedError

    def handle(
        self,
        result: CommandResult,
        input_path: Path,
        output_dir: Path,
        ctx: Context,
    ) -> None:
        """Record the command result as artifacts or a skip."""
        raise NotImplementedError

    def failure_reason(self, result: CommandResult, default: str) -> str:
        if result.stderr_tail():
            return f"{self.tool}: {result.stderr_tail()}"
        if result.returncode is not None:
            return f"{self.tool} exited with {result.returncode}"
        return default

    def emit_text(
        self,
        ctx: Context,
        name: str,
        result: CommandResult,
        *,
        fallback_to_stderr: bool = False,
    ) -> bool:
        text = result.stdout_text
        stream = "stdout"
        if not text and fallback_to_stderr:
            text = result.stderr_text
            stream = "stderr"
        if not text:
            return False
        ctx.text(
            f"{self.tool}/{name}",
            text,
            returncode=result.returncode,
            stream=stream,
            truncated=result.stdout_truncated if stream == "stdout" else result.stderr_truncated,
        )
        return True

    def emit_files(self, ctx: Context, output_dir: Path) -> OutputCollection:
        collection = collect_output_files(output_dir, self.output_policy)
        for file in collection.files:
            params: dict[str, Any] = {"source": file.relative}
            if collection.truncated:
                params["truncated"] = True
            if collection.notes:
                params["limits"] = "; ".join(collection.notes[:10])
            ctx.blob(
                f"{self.tool}/{file.relative}",
                file.data,
                media_type=file.media_type,
                **params,
            )
        return collection


__all__ = ["ExternalBridge"]
