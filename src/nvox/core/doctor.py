"""Environment and optional-tool diagnostics for ``nvox doctor``."""

from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path
from typing import Any, TypedDict

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from nvox import __version__
from nvox.core.config import Config
from nvox.core.tools import TOOLS, ExternalTool, probe_version


class ToolReport(TypedDict):
    name: str
    binary: str
    category: str
    purpose: str
    stage: str
    nix: str | None
    found: bool
    path: str | None
    version: str | None
    bridges: list[str]


class DoctorReport(TypedDict):
    environment: dict[str, str | None]
    config: dict[str, Any]
    tools: list[ToolReport]
    summary: dict[str, int]


def _command_version(binary: str, args: tuple[str, ...] = ("--version",)) -> str | None:
    path = shutil.which(binary)
    if path is None:
        return None
    return probe_version(path, args)


def _tool_report(tool: ExternalTool, *, verbose: bool) -> ToolReport:
    path = shutil.which(tool.binary)
    version = probe_version(path, tool.version_args) if path and verbose else None
    return ToolReport(
        name=tool.name,
        binary=tool.binary,
        category=tool.category,
        purpose=tool.purpose,
        stage=tool.stage,
        nix=tool.nix,
        found=path is not None,
        path=path,
        version=version,
        bridges=list(tool.bridges),
    )


def collect(config: Config, *, verbose: bool = False) -> DoctorReport:
    """Collect environment, configuration and tool detection data."""
    tools = [_tool_report(tool, verbose=verbose) for tool in TOOLS]
    found = sum(1 for tool in tools if tool["found"])
    return DoctorReport(
        environment={
            "nvox": __version__,
            "python": platform.python_version(),
            "executable": sys.executable,
            "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "uv": _command_version("uv"),
            "cwd": str(Path.cwd()),
        },
        config={
            "path": str(config.path) if config.path is not None else None,
            "flag_formats": list(config.flag_formats),
            "plugin_dirs": [
                {"path": str(directory), "exists": directory.is_dir()}
                for directory in config.plugin_dirs
            ],
            "limits": {
                "max_depth": config.limits.max_depth,
                "timeout": config.limits.timeout,
                "max_artifact_bytes": config.limits.max_artifact_bytes,
                "jobs": config.limits.jobs,
                "effective_jobs": config.limits.effective_jobs(),
            },
            "disabled_tools": list(config.disabled_tools),
        },
        tools=tools,
        summary={
            "available": found,
            "missing": len(tools) - found,
            "total": len(tools),
        },
    )


def render(console: Console, report: DoctorReport) -> None:
    """Render a doctor report for humans."""
    console.print("[bold]Nvox doctor[/]\n")

    env = report["environment"]
    env_table = Table(title="Environment", title_justify="left", show_header=False)
    env_table.add_column("Key", style="cyan", no_wrap=True)
    env_table.add_column("Value")
    for key in ("nvox", "python", "executable", "platform", "uv", "cwd"):
        env_table.add_row(key, escape(str(env.get(key) or "not detected")))
    console.print(env_table)
    console.print()

    cfg = report["config"]
    config_table = Table(title="Configuration", title_justify="left", show_header=False)
    config_table.add_column("Key", style="cyan", no_wrap=True)
    config_table.add_column("Value")
    config_table.add_row("path", escape(cfg["path"] or "none (built-in defaults)"))
    formats = ", ".join(cfg["flag_formats"])
    config_table.add_row(
        "flag formats",
        escape(formats or "none (pass --flag-format <prefix>{})"),
    )
    limits = cfg["limits"]
    config_table.add_row(
        "limits",
        f"depth={limits['max_depth']} timeout={limits['timeout']:g}s "
        f"max_artifact={_human_bytes(limits['max_artifact_bytes'])} "
        f"jobs={limits['jobs'] or 'auto'} (effective {limits['effective_jobs']})",
    )
    for entry in cfg["plugin_dirs"]:
        state = "[green]found[/]" if entry["exists"] else "[dim]missing[/]"
        config_table.add_row("plugin dir", f"{escape(entry['path'])} ({state})")
    if cfg["disabled_tools"]:
        config_table.add_row("disabled tools", escape(", ".join(cfg["disabled_tools"])))
    console.print(config_table)
    console.print()

    tools = report["tools"]
    for category in _categories(tools):
        table = Table(title=f"Tools: {category}", title_justify="left")
        table.add_column("", width=1)
        table.add_column("Tool", style="bold", no_wrap=True)
        table.add_column("Purpose")
        table.add_column("Path", overflow="fold")
        table.add_column("Milestone", justify="center")
        for tool in tools:
            if tool["category"] != category:
                continue
            status = "[green]*[/]" if tool["found"] else "[dim]o[/]"
            location = "[dim]not installed[/]"
            tool_path = tool["path"]
            if tool_path is not None:
                location = escape(tool_path)
            tool_version = tool["version"]
            if tool_version:
                location = f"{location}\n[dim]{escape(tool_version)}[/]"
            purpose = tool["purpose"]
            if tool["bridges"]:
                purpose = f"{purpose}\n[dim]bridge: {', '.join(tool['bridges'])}[/]"
            table.add_row(status, tool["name"], purpose, location, tool["stage"])
        console.print(table)
        console.print()

    summary = report["summary"]
    console.print(
        f"[bold]{summary['available']}/{summary['total']}[/] external tools available. "
        "Missing tools are optional: built-in implementations are used instead."
    )
    missing_nix: set[str] = set()
    for tool in tools:
        if not tool["found"] and tool["nix"]:
            missing_nix.add(tool["nix"])
    if missing_nix:
        console.print(
            "\n[dim]Install optional tools on NixOS, for example:[/]\n  nix shell "
            + " ".join(sorted(missing_nix))
        )


def _categories(tools: list[ToolReport]) -> list[str]:
    seen: list[str] = []
    for tool in tools:
        if tool["category"] not in seen:
            seen.append(tool["category"])
    return seen


def _human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


__all__ = ["DoctorReport", "ToolReport", "collect", "render"]
