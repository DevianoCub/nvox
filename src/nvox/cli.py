"""Command line interface for Nvox."""

from __future__ import annotations

import hashlib
import json as jsonlib
from importlib.metadata import entry_points
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from nvox import __version__
from nvox.api import Plugin
from nvox.core import doctor as doctor_module
from nvox.core.config import Config, ConfigError
from nvox.core.registry import ENTRY_POINT_GROUP, PluginRegistry

console = Console()

app = typer.Typer(
    name="nvox",
    help="Modular forensic and steganography analysis framework.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
    pretty_exceptions_show_locals=False,
    pretty_exceptions_enable=False,
)

plugins_app = typer.Typer(
    name="plugins",
    help="Inspect loaded plugins.",
    no_args_is_help=True,
)
app.add_typer(plugins_app, name="plugins")


def _show_version(value: bool) -> None:
    if value:
        console.print(f"nvox {__version__}")
        raise typer.Exit()


@app.callback()
def _callback(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=_show_version,
            is_eager=True,
            help="Show the Nvox version and exit.",
        ),
    ] = False,
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to an explicit nvox.toml."),
    ] = None,
) -> None:
    """Nvox: modular forensic and steganography analysis framework."""
    ctx.obj = {"config_path": config}


@app.command()
def doctor(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a machine-readable JSON report."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Probe versions of installed external tools."),
    ] = False,
) -> None:
    """Inspect the environment, configuration and optional external tooling."""
    config = _load_config(ctx)
    report = doctor_module.collect(config, verbose=verbose)
    if json_output:
        typer.echo(jsonlib.dumps(report, indent=2, ensure_ascii=False))
        return
    doctor_module.render(console, report)


@plugins_app.command("list")
def plugins_list(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a machine-readable JSON report."),
    ] = False,
) -> None:
    """List every loaded plugin and its availability."""
    config = _load_config(ctx)
    registry = _load_registry(config, json_output=json_output)
    rows: list[dict[str, Any]] = []
    for record in registry.records:
        plugin = record.plugin
        requirements = registry.requirements(plugin)
        status = _plugin_status(plugin, requirements, config)
        rows.append(
            {
                "name": plugin.name,
                "version": plugin.version,
                "kind": plugin.kind.value,
                "priority": plugin.priority,
                "targets": list(plugin.targets),
                "requires": requirements,
                "status": status,
                "origin": record.origin,
                "source": record.source,
                "description": plugin.description,
            }
        )

    if json_output:
        typer.echo(jsonlib.dumps(rows, indent=2, ensure_ascii=False))
        return

    table = Table(title=f"Plugins ({len(rows)})", title_justify="left")
    table.add_column("Status", no_wrap=True)
    table.add_column("Name", style="bold", no_wrap=True)
    table.add_column("Version", no_wrap=True)
    table.add_column("Kind", no_wrap=True)
    table.add_column("Prio", justify="right", no_wrap=True)
    table.add_column("Targets", overflow="fold", min_width=7)
    table.add_column("Requires", overflow="fold", min_width=8)
    table.add_column("Origin", no_wrap=True)
    for row in rows:
        status_style = {"ok": "green", "missing": "yellow", "disabled": "dim"}[row["status"]]
        requires = (
            ", ".join(
                f"{name}:{'ok' if available else 'missing'}"
                for name, available in row["requires"].items()
            )
            or "-"
        )
        table.add_row(
            f"[{status_style}]{row['status']}[/]",
            row["name"],
            row["version"],
            row["kind"],
            str(row["priority"]),
            ", ".join(row["targets"]),
            requires,
            row["origin"],
        )
    console.print(table)


@plugins_app.command("info")
def plugins_info(ctx: typer.Context, name: str) -> None:
    """Show the manifest, requirements and hooks of one plugin."""
    config = _load_config(ctx)
    registry = _load_registry(config)
    record = registry.record(name)
    if record is None:
        _fail(f"unknown plugin: {name}")
    plugin = record.plugin
    requirements = registry.requirements(plugin)
    status = _plugin_status(plugin, requirements, config)

    table = Table(title=f"Plugin: {plugin.name}", title_justify="left", show_header=False)
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value", overflow="fold")
    table.add_row("status", status)
    table.add_row("version", plugin.version)
    table.add_row("plugin api", str(plugin.api_version))
    table.add_row("kind", plugin.kind.value)
    table.add_row("priority", str(plugin.priority))
    table.add_row("origin", f"{record.origin} ({record.source})")
    table.add_row("targets", ", ".join(plugin.targets))
    table.add_row(
        "requirements",
        ", ".join(
            f"{tool}: {'found' if available else 'missing'}"
            for tool, available in requirements.items()
        )
        or "none",
    )
    table.add_row("description", plugin.description or "-")
    hooks = [
        hook
        for hook in ("pre_ingest", "on_artifact", "post_scan", "on_finding")
        if getattr(type(plugin), hook) is not getattr(Plugin, hook)
    ]
    table.add_row("hooks", ", ".join(hooks) or "none")
    defaults = dict(plugin.options or {})
    merged = dict(defaults)
    merged.update(config.plugin_options.get(plugin.name, {}))
    table.add_row(
        "options",
        ", ".join(f"{key}={value!r}" for key, value in merged.items()) or "none",
    )
    console.print(table)


@plugins_app.command("dirs")
def plugins_dirs(ctx: typer.Context) -> None:
    """Show plugin discovery paths and their load order."""
    config = _load_config(ctx)
    import nvox.plugins as builtin_package

    table = Table(title="Plugin sources", title_justify="left")
    table.add_column("Order", justify="right", no_wrap=True)
    table.add_column("Source", no_wrap=True)
    table.add_column("Location", overflow="fold")
    table.add_column("Status", no_wrap=True)

    builtin_path = Path(builtin_package.__path__[0])
    table.add_row("1", "builtin", escape(str(builtin_path)), "loaded")
    order = 2
    for directory in config.plugin_dirs:
        state = "loaded" if directory.is_dir() else "missing"
        table.add_row(str(order), "directory", escape(str(directory)), state)
        order += 1
    discovered = entry_points(group=ENTRY_POINT_GROUP)
    table.add_row(
        str(order),
        "entry points",
        f"group {ENTRY_POINT_GROUP} ({len(discovered)} found)",
        "loaded",
    )
    console.print(table)


@app.command()
def extract(
    artifact_id: Annotated[str, typer.Argument(help="Artifact SHA-256 or unique prefix.")],
    store: Annotated[
        Path,
        typer.Option("--store", "-s", help="Directory containing a previous run's artifacts."),
    ] = Path("out"),
    out: Annotated[
        Path | None,
        typer.Option(
            "--out", "-o", help="Write the artifact to this file instead of printing a path."
        ),
    ] = None,
) -> None:
    """Write a stored artifact to disk by hash or unique prefix."""
    base = store / "artifacts"
    if not base.is_dir():
        _fail(f"no artifact store at {base}")
    matches = sorted(
        path for path in base.rglob("*") if path.is_file() and path.name.startswith(artifact_id)
    )
    if not matches:
        _fail(f"no artifact matching {artifact_id!r} in {base}")
    if len(matches) > 1:
        names = ", ".join(path.name[:12] for path in matches[:10])
        _fail(f"ambiguous artifact id {artifact_id!r}: {names}")
    source = matches[0]
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != source.name:
        _fail(f"artifact store integrity error: {source.name} hashes to {digest}")
    if out is None:
        console.print(f"{escape(str(source))}")
        console.print(f"[dim]sha256 {digest} ({len(payload)} bytes)[/]")
        return
    if out.exists():
        _fail(f"refusing to overwrite existing file: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    console.print(f"wrote {escape(str(out))} [dim]({len(payload)} bytes, sha256 {digest})[/]")


def _load_config(ctx: typer.Context) -> Config:
    config_path: Path | None = (ctx.obj or {}).get("config_path")
    try:
        return Config.load(explicit=config_path)
    except ConfigError as exc:
        console.print(f"[red]config error:[/] {escape(str(exc))}")
        raise typer.Exit(code=1) from exc


def _load_registry(config: Config, *, json_output: bool = False) -> PluginRegistry:
    registry = PluginRegistry().load_all(config)
    for warning in registry.warnings:
        if json_output:
            typer.echo(f"warning: {warning}", err=True)
        else:
            console.print(f"[yellow]warning:[/] {escape(warning)}")
    return registry


def _plugin_status(plugin: Plugin, requirements: dict[str, bool], config: Config) -> str:
    if plugin.name in config.disabled_plugins:
        return "disabled"
    if any(tool in config.disabled_tools for tool in plugin.requires):
        return "disabled"
    if not all(requirements.values()):
        return "missing"
    return "ok"


def _fail(message: str) -> NoReturn:
    console.print(f"[red]error:[/] {escape(message)}")
    raise typer.Exit(code=1)


def main() -> None:
    """Entry point used by the ``nvox`` console script."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
