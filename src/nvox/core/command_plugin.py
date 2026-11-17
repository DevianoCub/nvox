"""Declarative command plugins defined in TOML.

A TOML file in a plugin directory can wrap any executable:

```toml
[plugin]
name = "example"
command = ["tool", "--flag", "{input}"]
output = "text"
```

Commands run through :func:`nvox.core.sandbox.run_command`: they receive a
wall-clock timeout, a new process group, streamed output caps and best-effort
resource limits. Output files are collected without following symlinks and are
subject to per-file, per-command and total byte caps.
"""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

from nvox.api import (
    API_VERSION,
    Artifact,
    Context,
    Plugin,
    PluginKind,
)
from nvox.core.external import (
    OutputError,
    OutputPolicy,
    build_child_environment,
    collect_output_files,
    safe_input_name,
    validate_env,
)
from nvox.core.sandbox import CommandResult, SandboxError, SandboxLimits, run_command

_OUTPUT_MODES = frozenset({"text", "bytes", "files", "json"})
_MAX_OUTPUT_BYTES = 1_000_000
_MAX_OUTPUT_FILES = 1000
_MAX_FILE_BYTES = 16 * 1024 * 1024
_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class CommandPluginError(Exception):
    """Raised when a declarative plugin definition is invalid."""


class CommandPlugin(Plugin):
    """Base class for plugins generated from a TOML definition."""

    command: ClassVar[tuple[str, ...]] = ()
    output: ClassVar[str] = "text"
    max_output_bytes: ClassVar[int] = _MAX_OUTPUT_BYTES
    output_policy: ClassVar[OutputPolicy] = OutputPolicy()
    inherit_env: ClassVar[bool] = False
    env: ClassVar[Mapping[str, str]] = {}

    def run(self, artifact: Artifact, ctx: Context) -> None:
        if not self.command:
            ctx.skip("empty command")
        with tempfile.TemporaryDirectory(prefix="nvox-cmd-") as directory:
            workspace = Path(directory)
            input_dir = workspace / "input"
            output_dir = workspace / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            input_path = input_dir / safe_input_name(artifact.name, artifact.media_type)
            input_path.write_bytes(artifact.payload)

            values: dict[str, str] = {
                "input": str(input_path),
                "input_name": input_path.name,
                "outdir": str(output_dir),
            }
            for key, value in ctx.options.items():
                if isinstance(value, bool):
                    values[key] = "true" if value else "false"
                else:
                    values[key] = str(value)

            argv = [_render(part, values) for part in self.command]
            try:
                environment = build_child_environment(
                    workspace,
                    extra=self.env,
                    inherit=self.inherit_env,
                )
                result = run_command(
                    argv,
                    cwd=output_dir,
                    env=environment,
                    limits=SandboxLimits(
                        timeout=self.timeout or 60.0,
                        max_output_bytes=self.max_output_bytes,
                        max_file_bytes=self.output_policy.max_file_bytes,
                    ),
                )
            except (SandboxError, OutputError) as exc:
                ctx.skip(str(exc))

            if result.timed_out:
                ctx.skip(f"command timed out after {(self.timeout or 60.0):g}s")
            if result.error:
                ctx.skip(f"command output could not be read: {result.error}")

            stderr_tail = result.stderr_tail()
            if result.returncode != 0 and not result.stdout and self.output != "files":
                reason = f"command exited with {result.returncode}"
                if stderr_tail:
                    reason = f"{reason}: {stderr_tail}"
                ctx.skip(reason)

            if self.output == "files":
                self._emit_files(output_dir, ctx, result)
            elif self.output == "json":
                self._emit_json(result, ctx)
            elif self.output == "bytes":
                if not result.stdout:
                    ctx.skip("command produced no output")
                ctx.blob(
                    f"{self.name}/stdout",
                    result.stdout,
                    returncode=result.returncode,
                    truncated=result.stdout_truncated,
                )
            else:
                if not result.stdout:
                    ctx.skip("command produced no output")
                ctx.text(
                    f"{self.name}/stdout",
                    result.stdout_text,
                    returncode=result.returncode,
                    truncated=result.stdout_truncated,
                    stderr=stderr_tail,
                )

    def _emit_files(self, output_dir: Path, ctx: Context, result: CommandResult) -> None:
        collection = collect_output_files(output_dir, self.output_policy)
        if not collection.files:
            if result.returncode != 0:
                reason = f"command exited with {result.returncode}"
            else:
                reason = "command produced no files"
            detail = result.stderr_tail()
            if detail:
                reason = f"{reason}: {detail}"
            if collection.notes:
                reason = f"{reason}: {'; '.join(collection.notes[:3])}"
            ctx.skip(reason)
        stderr_tail = result.stderr_tail()
        for file in collection.files:
            params: dict[str, Any] = {
                "source": file.relative,
                "returncode": result.returncode,
                "truncated": collection.truncated,
            }
            if stderr_tail:
                params["stderr"] = stderr_tail
            if collection.notes:
                params["limits"] = "; ".join(collection.notes[:10])
            ctx.blob(
                f"{self.name}/{file.relative}",
                file.data,
                media_type=file.media_type,
                **params,
            )

    def _emit_json(self, result: Any, ctx: Context) -> None:
        if not result.stdout:
            ctx.skip("command produced no output")
        if result.stdout_truncated:
            ctx.skip("command output was truncated before it could be parsed as JSON")
        try:
            decoded = json.loads(result.stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            ctx.skip(f"command produced invalid JSON: {exc}")
        params = {"returncode": result.returncode}
        if isinstance(decoded, Mapping):
            ctx.meta(f"{self.name}/json", decoded, **params)
        else:
            ctx.meta(f"{self.name}/json", {"value": decoded}, **params)


def _render(template: str, values: Mapping[str, str]) -> str:
    """Substitute known ``{name}`` placeholders and leave other braces alone.

    Unknown names are kept verbatim so shell snippets such as ``awk '{print}'``
    or JSON literals survive unchanged.
    """
    return _PLACEHOLDER.sub(lambda match: values.get(match.group(1), match.group(0)), template)


def parse_command_plugin(raw: Mapping[str, Any], *, source: Path) -> type[Plugin]:
    """Validate a TOML definition and build a plugin class."""
    table = raw.get("plugin")
    if not isinstance(table, Mapping):
        raise CommandPluginError(f"{source}: missing [plugin] table")

    name = table.get("name")
    if not isinstance(name, str) or not name.strip():
        raise CommandPluginError(f"{source}: plugin.name must be a non-empty string")

    command = table.get("command")
    if (
        not isinstance(command, Sequence)
        or isinstance(command, (str, bytes))
        or not command
        or not all(isinstance(part, str) and "\x00" not in part for part in command)
        or not command[0]
    ):
        raise CommandPluginError(
            f"{source}: plugin.command must be a non-empty list of strings without NUL bytes"
        )

    output = table.get("output", "text")
    if not isinstance(output, str) or output not in _OUTPUT_MODES:
        raise CommandPluginError(f"{source}: plugin.output must be one of {sorted(_OUTPUT_MODES)}")

    kind_name = table.get("kind", "analyzer")
    try:
        kind = PluginKind(str(kind_name))
    except ValueError as exc:
        raise CommandPluginError(f"{source}: unknown plugin.kind {kind_name!r}") from exc

    targets = _string_tuple(table.get("targets", ["*/*"]), "targets", source)
    requires = _string_tuple(table.get("requires", []), "requires", source)
    if not requires:
        executable = command[0]
        if "/" not in executable and "\\" not in executable and not executable.startswith("{"):
            requires = (executable,)
    priority = table.get("priority", 50)
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise CommandPluginError(f"{source}: plugin.priority must be an integer")

    sandbox = table.get("sandbox", {})
    if not isinstance(sandbox, Mapping):
        raise CommandPluginError(f"{source}: plugin.sandbox must be a table")

    timeout_raw = sandbox.get("timeout", table.get("timeout", 60.0))
    if (
        not isinstance(timeout_raw, (int, float))
        or isinstance(timeout_raw, bool)
        or timeout_raw <= 0
    ):
        raise CommandPluginError(f"{source}: plugin.timeout must be a positive number")
    timeout = float(timeout_raw)
    max_output_bytes = _positive_int(
        sandbox.get("max_output_bytes", table.get("max_output_bytes", _MAX_OUTPUT_BYTES)),
        "max_output_bytes",
        source,
    )
    max_file_bytes = _positive_int(
        sandbox.get("max_file_bytes", table.get("max_file_bytes", _MAX_FILE_BYTES)),
        "max_file_bytes",
        source,
    )
    max_files = _positive_int(
        sandbox.get("max_files", table.get("max_files", _MAX_OUTPUT_FILES)),
        "max_files",
        source,
    )
    max_total_bytes = _positive_int(
        sandbox.get("max_total_bytes", table.get("max_total_bytes", _MAX_TOTAL_BYTES)),
        "max_total_bytes",
        source,
    )
    inherit_env = sandbox.get("inherit_env", table.get("inherit_env", False))
    if not isinstance(inherit_env, bool):
        raise CommandPluginError(f"{source}: plugin.inherit_env must be a boolean")
    env_raw = sandbox.get("env", table.get("env", {}))
    if not isinstance(env_raw, Mapping):
        raise CommandPluginError(f"{source}: plugin.env must be a table")
    try:
        env = validate_env(env_raw)
        output_policy = OutputPolicy(
            max_files=max_files,
            max_file_bytes=max_file_bytes,
            max_total_bytes=max_total_bytes,
        )
        output_policy.validate()
    except OutputError as exc:
        raise CommandPluginError(f"{source}: {exc}") from exc

    version = table.get("version", "0.0.0")
    if not isinstance(version, str):
        raise CommandPluginError(f"{source}: plugin.version must be a string")
    api_version = table.get("api_version", API_VERSION)
    if not isinstance(api_version, int) or isinstance(api_version, bool):
        raise CommandPluginError(f"{source}: plugin.api_version must be an integer")

    options_raw = table.get("options", {})
    if not isinstance(options_raw, Mapping):
        raise CommandPluginError(f"{source}: plugin.options must be a table")
    defaults: dict[str, Any] = {}
    for key, value in options_raw.items():
        if isinstance(value, Mapping) and "default" in value:
            defaults[str(key)] = value["default"]
        else:
            defaults[str(key)] = value

    description = table.get("description", f"Command plugin wrapping {command[0]}")
    if not isinstance(description, str):
        raise CommandPluginError(f"{source}: plugin.description must be a string")

    class_name = "CommandPlugin_" + "".join(char if char.isalnum() else "_" for char in name)
    attributes: dict[str, Any] = {
        "name": name,
        "version": version,
        "api_version": api_version,
        "kind": kind,
        "targets": targets,
        "priority": priority,
        "requires": requires,
        "description": description,
        "options": defaults,
        "timeout": timeout,
        "command": tuple(command),
        "output": str(output),
        "max_output_bytes": max_output_bytes,
        "output_policy": output_policy,
        "inherit_env": inherit_env,
        "env": env,
        "__doc__": description,
    }
    return type(class_name, (CommandPlugin,), attributes)


def _positive_int(value: Any, key: str, source: Path) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CommandPluginError(f"{source}: plugin.{key} must be a positive integer")
    return value


def _string_tuple(value: Any, key: str, source: Path) -> tuple[str, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or not all(isinstance(item, str) and "\x00" not in item for item in value)
    ):
        raise CommandPluginError(f"{source}: plugin.{key} must be a list of strings")
    return tuple(value)


__all__ = ["CommandPlugin", "CommandPluginError", "parse_command_plugin"]
