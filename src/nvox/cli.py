"""Command line interface for Nvox."""

from __future__ import annotations

import json as jsonlib
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from nvox import __version__
from nvox.core import doctor as doctor_module
from nvox.core.config import Config, ConfigError

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
    config_path: Path | None = (ctx.obj or {}).get("config_path")
    try:
        config = Config.load(explicit=config_path)
    except ConfigError as exc:
        console.print(f"[red]config error:[/] {exc}")
        raise typer.Exit(code=1) from exc

    report = doctor_module.collect(config, verbose=verbose)
    if json_output:
        typer.echo(jsonlib.dumps(report, indent=2, ensure_ascii=False))
        return
    doctor_module.render(console, report)


def main() -> None:
    """Entry point used by the ``nvox`` console script."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
