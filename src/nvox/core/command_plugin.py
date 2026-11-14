"""Declarative command plugins defined in TOML.

A TOML file in a plugin directory can wrap any executable:

```toml
[plugin]
name = "example"
command = ["tool", "--flag", "{input}"]
output = "text"
```
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
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

_OUTPUT_MODES = frozenset({"text", "bytes", "files", "json"})
_MAX_OUTPUT_FILES = 1000
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class CommandPluginError(Exception):
    """Raised when a declarative plugin definition is invalid."""


class CommandPlugin(Plugin):
    """Base class for plugins generated from a TOML definition."""

    command: ClassVar[tuple[str, ...]] = ()
    output: ClassVar[str] = "text"

    def run(self, artifact: Artifact, ctx: Context) -> None:
        if not self.command:
            ctx.skip("empty command")
        with tempfile.TemporaryDirectory(prefix="nvox-cmd-") as directory:
            workspace = Path(directory)
            input_dir = workspace / "input"
            output_dir = workspace / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            input_path = input_dir / _safe_name(artifact.name)
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
                completed = subprocess.run(
                    argv,
                    cwd=output_dir,
                    capture_output=True,
                    timeout=self.timeout or 60.0,
                    check=False,
                    env=dict(os.environ),
                )
            except subprocess.TimeoutExpired:
                ctx.skip(f"command timed out after {(self.timeout or 60.0):g}s")
            except OSError as exc:
                ctx.skip(f"cannot execute {argv[0]!r}: {exc}")

            stderr_tail = completed.stderr.decode("utf-8", errors="replace")[-300:].strip()
            stdout = completed.stdout
            if completed.returncode != 0 and not stdout:
                reason = f"command exited with {completed.returncode}"
                if stderr_tail:
                    reason = f"{reason}: {stderr_tail}"
                ctx.skip(reason)

            if self.output == "files":
                self._emit_files(output_dir, ctx)
            elif self.output == "json":
                self._emit_json(stdout, ctx)
            elif self.output == "bytes":
                if not stdout:
                    ctx.skip("command produced no output")
                ctx.blob(f"{self.name}/stdout", stdout)
            else:
                if not stdout:
                    ctx.skip("command produced no output")
                ctx.text(f"{self.name}/stdout", stdout.decode("utf-8", errors="replace"))

    def _emit_files(self, output_dir: Path, ctx: Context) -> None:
        files = sorted(path for path in output_dir.rglob("*") if path.is_file())
        if not files:
            ctx.skip("command produced no files")
        if len(files) > _MAX_OUTPUT_FILES:
            ctx.skip(f"command produced {len(files)} files (limit {_MAX_OUTPUT_FILES})")
        for path in files:
            relative = path.relative_to(output_dir).as_posix()
            media_type, _ = mimetypes.guess_type(path.name)
            ctx.blob(
                f"{self.name}/{relative}",
                path.read_bytes(),
                media_type=media_type or "application/octet-stream",
                source=relative,
            )

    def _emit_json(self, stdout: bytes, ctx: Context) -> None:
        if not stdout:
            ctx.skip("command produced no output")
        try:
            decoded = json.loads(stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            ctx.skip(f"command produced invalid JSON: {exc}")
        if isinstance(decoded, Mapping):
            ctx.meta(f"{self.name}/json", decoded)
        else:
            ctx.meta(f"{self.name}/json", {"value": decoded})


def _render(template: str, values: Mapping[str, str]) -> str:
    """Substitute known ``{name}`` placeholders and leave other braces alone.

    Unknown names are kept verbatim so shell snippets such as ``awk '{print}'``
    or JSON literals survive unchanged.
    """
    return _PLACEHOLDER.sub(lambda match: values.get(match.group(1), match.group(0)), template)


def _safe_name(name: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in name)
    return cleaned[:120] or "artifact.bin"


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
        or not all(isinstance(part, str) for part in command)
    ):
        raise CommandPluginError(f"{source}: plugin.command must be a non-empty list of strings")

    output = table.get("output", "text")
    if output not in _OUTPUT_MODES:
        raise CommandPluginError(f"{source}: plugin.output must be one of {sorted(_OUTPUT_MODES)}")

    kind_name = table.get("kind", "analyzer")
    try:
        kind = PluginKind(str(kind_name))
    except ValueError as exc:
        raise CommandPluginError(f"{source}: unknown plugin.kind {kind_name!r}") from exc

    targets = _string_tuple(table.get("targets", ["*/*"]), "targets", source)
    requires = _string_tuple(table.get("requires", []), "requires", source)
    priority = table.get("priority", 50)
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise CommandPluginError(f"{source}: plugin.priority must be an integer")
    timeout = table.get("timeout", 60.0)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise CommandPluginError(f"{source}: plugin.timeout must be a positive number")
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
        "timeout": float(timeout),
        "command": tuple(command),
        "output": str(output),
        "__doc__": description,
    }
    return type(class_name, (CommandPlugin,), attributes)


def _string_tuple(value: Any, key: str, source: Path) -> tuple[str, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or not all(isinstance(item, str) for item in value)
    ):
        raise CommandPluginError(f"{source}: plugin.{key} must be a list of strings")
    return tuple(value)


__all__ = ["CommandPlugin", "CommandPluginError", "parse_command_plugin"]
