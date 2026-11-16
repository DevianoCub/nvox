"""Command line interface for Nvox."""

from __future__ import annotations

import csv
import hashlib
import json as jsonlib
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime
from importlib.metadata import entry_points
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.tree import Tree

from nvox import __version__
from nvox.api import Plugin
from nvox.core import doctor as doctor_module
from nvox.core.config import Config, ConfigError
from nvox.core.engine import Engine, ScanResult, Skip
from nvox.core.findings import Finding
from nvox.core.flags import FlagFormatError, FlagScanner, build_flag_specs
from nvox.core.registry import (
    ENTRY_POINT_GROUP,
    PluginLoadError,
    PluginRegistry,
    is_external,
)
from nvox.core.store import ArtifactStore, ArtifactStoreError
from nvox.progress import ScanProgress
from nvox.report import (
    DEFAULT_MAX_PREVIEW_BYTES,
    ReportError,
    default_html_path,
    load_report,
    write_html_report,
)

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
def scan(
    ctx: typer.Context,
    path: Annotated[
        Path,
        typer.Argument(help="Evidence file or directory to scan recursively."),
    ],
    flag_formats: Annotated[
        list[str] | None,
        typer.Option(
            "--flag-format",
            "-f",
            help="Flag format with a single '{}' placeholder; repeatable.",
        ),
    ] = None,
    patterns: Annotated[
        list[str] | None,
        typer.Option("--flag", help="Raw flag regular expression; repeatable."),
    ] = None,
    ignore_case: Annotated[
        bool,
        typer.Option("--ignore-case", "-i", help="Match formats and patterns case-insensitively."),
    ] = False,
    min_length: Annotated[
        int,
        typer.Option("--min-length", help="Minimum flag body length."),
    ] = 4,
    max_length: Annotated[
        int,
        typer.Option("--max-length", help="Maximum flag body length."),
    ] = 200,
    depth: Annotated[
        int | None,
        typer.Option("--depth", help="Override the maximum artifact depth."),
    ] = None,
    timeout: Annotated[
        float | None,
        typer.Option("--timeout", help="Override the per-plugin timeout in seconds."),
    ] = None,
    jobs: Annotated[
        int | None,
        typer.Option("--jobs", "-j", help="Override the worker count (0 = auto)."),
    ] = None,
    plugin_filter: Annotated[
        str | None,
        typer.Option(
            "--plugins",
            help="Comma/space separated plugin names to run (default: all loaded).",
        ),
    ] = None,
    no_external: Annotated[
        bool,
        typer.Option("--no-external", help="Disable command plugins and external-tool bridges."),
    ] = False,
    no_xor_brute: Annotated[
        bool,
        typer.Option("--no-xor-brute", help="Disable the single-byte XOR brute-force decoder."),
    ] = False,
    out: Annotated[
        Path,
        typer.Option("--out", "-o", help="Directory for artifacts and findings.json."),
    ] = Path("out"),
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit the complete report as JSON on stdout."),
    ] = False,
    csv_output: Annotated[
        bool,
        typer.Option(
            "--csv",
            help="Write out/artifacts.csv with one row per artifact.",
        ),
    ] = False,
    html_output: Annotated[
        bool,
        typer.Option(
            "--html/--no-html",
            help="Write a self-contained out/report.html after the scan.",
        ),
    ] = True,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show skips and per-plugin failures."),
    ] = False,
) -> None:
    """Analyse evidence; search for flags when a format or pattern is supplied."""
    started_at = datetime.now(UTC)
    config = _load_config(ctx)
    config = _config_with_limits(
        config,
        depth=depth,
        timeout=timeout,
        jobs=jobs,
    )
    requested_formats = list(flag_formats or ()) or list(config.flag_formats)
    requested_patterns = list(patterns or ())
    flag_search = bool(requested_formats or requested_patterns)
    scanner: FlagScanner | None = None
    if flag_search:
        try:
            specs = build_flag_specs(
                requested_formats,
                requested_patterns,
                ignore_case=ignore_case,
                min_length=min_length,
                max_length=max_length,
            )
        except FlagFormatError as exc:
            _fail(str(exc))
        scanner = FlagScanner(specs)

    registry = _load_registry(config, json_output=json_output)
    registry = _filter_registry(
        registry,
        plugin_filter=plugin_filter,
        no_external=no_external,
        no_xor_brute=no_xor_brute,
    )

    out_dir = out.expanduser()
    try:
        store = ArtifactStore(root=out_dir, max_bytes=config.limits.max_artifact_bytes)
    except ArtifactStoreError as exc:
        _fail(str(exc))

    files = _collect_inputs(path, out_dir)
    results: list[tuple[Path, ScanResult]] = []
    progress = ScanProgress(
        console,
        total_files=len(files),
        enabled=not json_output and console.is_terminal,
    )
    with progress:
        engine = Engine(
            config,
            registry=registry,
            store=store,
            flag_scanner=scanner,
            jobs=config.limits.effective_jobs(),
            on_event=progress.on_event,
        )
        for evidence_path in files:
            progress.start_file(evidence_path)
            result = engine.analyze(evidence_path)
            progress.finish_file(result)
            results.append((evidence_path, result))

    findings = _aggregate_findings(results)
    report = _build_report(
        results,
        findings,
        mode="flags" if flag_search else "artifacts",
        formats=requested_formats,
        patterns=requested_patterns,
        ignore_case=ignore_case,
        min_length=min_length,
        max_length=max_length,
        started_at=started_at,
    )
    report_path = out_dir / "findings.json"
    try:
        report_path.write_text(
            jsonlib.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        _fail(f"cannot write {report_path}: {exc}")

    csv_path = out_dir / "artifacts.csv" if csv_output else None
    if csv_path is not None:
        try:
            _write_artifacts_csv(report, csv_path)
        except OSError as exc:
            _fail(f"cannot write {csv_path}: {exc}")

    html_path = out_dir / "report.html" if html_output else None
    if html_path is not None:
        try:
            write_html_report(
                report,
                html_path,
                store_root=out_dir,
                title=f"Nvox scan report — {path}",
            )
        except ReportError as exc:
            _fail(str(exc))

    if json_output:
        typer.echo(jsonlib.dumps(report, indent=2, ensure_ascii=False))
    else:
        _render_scan(
            report,
            results,
            findings,
            report_path,
            verbose=verbose,
            target=path,
            flag_search=flag_search,
            csv_path=csv_path,
            html_path=html_path,
        )
    if findings:
        raise typer.Exit(code=2)


@app.command()
def report(
    source: Annotated[
        Path,
        typer.Argument(help="findings.json or a scan output directory."),
    ] = Path("out"),
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            "-o",
            help="HTML output path (default: report.html next to findings).",
        ),
    ] = None,
    open_browser: Annotated[
        bool,
        typer.Option("--open", help="Open the generated report in the default browser."),
    ] = False,
    max_preview_bytes: Annotated[
        int,
        typer.Option(
            "--max-preview-bytes",
            help="Largest image/audio payload embedded in the HTML report.",
        ),
    ] = DEFAULT_MAX_PREVIEW_BYTES,
) -> None:
    """Re-render a saved scan as a standalone HTML report without rescanning."""
    if max_preview_bytes < 1:
        _fail("--max-preview-bytes must be greater than zero")
    try:
        report_data, report_path = load_report(source)
    except ReportError as exc:
        _fail(str(exc))
    target = out.expanduser() if out is not None else default_html_path(source)
    try:
        path = write_html_report(
            report_data,
            target,
            store_root=report_path.parent,
            title=f"Nvox report — {report_path.parent.name}",
            max_preview_bytes=max_preview_bytes,
        )
    except ReportError as exc:
        _fail(str(exc))
    console.print(f"[green]wrote[/] {escape(str(path))}")
    if open_browser:
        import webbrowser

        webbrowser.open(path.resolve().as_uri())


@app.command()
def tui(
    ctx: typer.Context,
    source: Annotated[
        Path | None,
        typer.Argument(help="Evidence file/directory to scan, or omit with --report."),
    ] = None,
    report_source: Annotated[
        Path | None,
        typer.Option("--report", help="Load an existing findings.json or output directory."),
    ] = None,
    out: Annotated[
        Path,
        typer.Option("--out", "-o", help="Artifact output directory for new scans."),
    ] = Path("out"),
) -> None:
    """Open the interactive Textual triage interface."""
    if source is not None and report_source is not None:
        _fail("provide either an evidence path or --report, not both")
    config = _load_config(ctx)
    try:
        from nvox.tui import NvoxTui
    except ImportError as exc:
        _fail(f"Textual is not installed; install the tui extra: {exc}")
    app_instance = NvoxTui(
        path=source,
        report_source=report_source,
        out=out.expanduser(),
        config=config,
    )
    app_instance.run()


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


def _config_with_limits(
    config: Config,
    *,
    depth: int | None,
    timeout: float | None,
    jobs: int | None,
) -> Config:
    limits = config.limits
    if depth is not None:
        if depth < 1:
            _fail("--depth must be at least 1")
        limits = replace(limits, max_depth=depth)
    if timeout is not None:
        if timeout <= 0:
            _fail("--timeout must be greater than zero")
        limits = replace(limits, timeout=timeout)
    if jobs is not None:
        if jobs < 0:
            _fail("--jobs must be zero or greater")
        limits = replace(limits, jobs=jobs)
    return replace(config, limits=limits)


def _filter_registry(
    registry: PluginRegistry,
    *,
    plugin_filter: str | None,
    no_external: bool,
    no_xor_brute: bool,
) -> PluginRegistry:
    records = {record.name: record for record in registry.records}
    if plugin_filter is not None:
        requested = [part for part in plugin_filter.replace(",", " ").split() if part]
        if not requested:
            _fail("--plugins did not contain any plugin names")
        unknown = [name for name in requested if name not in records]
        if unknown:
            _fail(f"unknown plugin(s): {', '.join(sorted(unknown))}")
        allowed = set(requested)
    else:
        allowed = set(records)
    if no_external:
        allowed = {name for name in allowed if not is_external(records[name])}
    if no_xor_brute:
        allowed.discard("decoder.xor")
    try:
        return registry.subset(sorted(allowed))
    except PluginLoadError as exc:
        _fail(str(exc))


def _collect_inputs(path: Path, out_dir: Path) -> list[Path]:
    target = path.expanduser()
    if not target.exists():
        _fail(f"input does not exist: {target}")
    if target.is_file():
        return [target]
    if not target.is_dir():
        _fail(f"input is neither a file nor a directory: {target}")
    out_resolved = out_dir.resolve()
    files: list[Path] = []
    for candidate in sorted(target.rglob("*")):
        if not candidate.is_file():
            continue
        try:
            candidate.resolve().relative_to(out_resolved)
        except ValueError:
            files.append(candidate)
    if not files:
        _fail(f"no files found in {target}")
    return files


def _aggregate_findings(
    results: list[tuple[Path, ScanResult]],
) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[tuple[str, str, str, int]] = set()
    for _path, result in results:
        for finding in result.findings:
            key = (
                finding.value,
                finding.evidence_sha256,
                finding.artifact,
                finding.offset,
            )
            if key in seen:
                continue
            seen.add(key)
            findings.append(finding)
    return sorted(findings, key=lambda item: (item.value, item.evidence_path, item.offset))


def _build_report(
    results: list[tuple[Path, ScanResult]],
    findings: list[Finding],
    *,
    mode: str,
    formats: list[str],
    patterns: list[str],
    ignore_case: bool,
    min_length: int,
    max_length: int,
    started_at: datetime,
) -> dict[str, Any]:
    finished_at = datetime.now(UTC)
    evidence = []
    for path, result in results:
        entry = {"path": str(path), **result.to_dict()}
        evidence.append(entry)
    return {
        "nvox": __version__,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration": round((finished_at - started_at).total_seconds(), 6),
        "mode": mode,
        "formats": formats,
        "patterns": patterns,
        "options": {
            "ignore_case": ignore_case,
            "min_length": min_length,
            "max_length": max_length,
        },
        "evidence": evidence,
        "findings": [finding.to_dict() for finding in findings],
        "summary": {
            "files": len(results),
            "artifacts": sum(len(result.artifacts) for _path, result in results),
            "findings": len(findings),
            "skips": sum(len(result.skips) for _path, result in results),
            "plugin_runs": sum(result.plugin_runs for _path, result in results),
            "duration": round((finished_at - started_at).total_seconds(), 6),
        },
    }


_ARTIFACT_CSV_FIELDS = (
    "evidence_path",
    "evidence_sha256",
    "artifact_sha256",
    "producer",
    "kind",
    "name",
    "media_type",
    "size",
    "depth",
    "parents",
    "params",
)


def _write_artifacts_csv(report: dict[str, Any], path: Path) -> None:
    """Write one CSV row per artifact for spreadsheet-friendly triage."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_ARTIFACT_CSV_FIELDS)
        writer.writeheader()
        for entry in report.get("evidence", []):
            evidence_path = entry.get("path", "")
            evidence = entry.get("evidence") or {}
            evidence_sha256 = evidence.get("sha256", "")
            for artifact in entry.get("artifacts", []):
                writer.writerow(
                    {
                        "evidence_path": evidence_path,
                        "evidence_sha256": evidence_sha256,
                        "artifact_sha256": artifact.get("sha256", ""),
                        "producer": artifact.get("producer", ""),
                        "kind": artifact.get("kind", ""),
                        "name": artifact.get("name", ""),
                        "media_type": artifact.get("media_type", ""),
                        "size": artifact.get("size", 0),
                        "depth": artifact.get("depth", 0),
                        "parents": jsonlib.dumps(
                            artifact.get("parents", []), separators=(",", ":")
                        ),
                        "params": jsonlib.dumps(
                            artifact.get("params", {}), separators=(",", ":"), sort_keys=True
                        ),
                    }
                )


def _render_scan(
    report: dict[str, Any],
    results: list[tuple[Path, ScanResult]],
    findings: list[Finding],
    report_path: Path,
    *,
    verbose: bool,
    target: Path,
    flag_search: bool,
    csv_path: Path | None,
    html_path: Path | None,
) -> None:
    console.print(f"[bold]Nvox scan[/] {escape(str(target))}")
    if not flag_search:
        console.print(
            "[bold]Artifact-only mode[/] — no flag formats supplied; "
            "full artifact data is written to the report."
        )
    elif findings:
        console.print(_findings_tree(findings))
    else:
        console.print("[green]No flags found.[/]")

    if verbose:
        skips = _all_skips(results)
        if skips:
            skip_table = Table(title=f"Skips ({len(skips)})", title_justify="left")
            skip_table.add_column("Plugin", no_wrap=True)
            skip_table.add_column("Kind", no_wrap=True)
            skip_table.add_column("Artifact", no_wrap=True)
            skip_table.add_column("Reason", overflow="fold")
            for skip in skips:
                skip_table.add_row(
                    escape(skip.plugin or "-"),
                    skip.kind,
                    skip.artifact[:12] or "-",
                    escape(skip.reason),
                )
            console.print(skip_table)

    summary = report["summary"]
    console.print(
        f"[dim]{summary['files']} file(s), {summary['artifacts']} artifact(s), "
        f"{summary['plugin_runs']} plugin run(s), {summary['skips']} skip(s), "
        f"{summary['findings']} finding(s) in {summary['duration']:.2f}s[/]"
    )
    console.print(f"[dim]report: {escape(str(report_path))}[/]")
    if csv_path is not None:
        console.print(f"[dim]artifacts CSV: {escape(str(csv_path))}[/]")
    if html_path is not None:
        console.print(f"[dim]HTML report: {escape(str(html_path))}[/]")


def _findings_tree(findings: list[Finding]) -> Tree:
    """Group findings by surface and provenance chain for terminal triage."""
    grouped: dict[str, dict[str, list[Finding]]] = defaultdict(lambda: defaultdict(list))
    for finding in findings:
        chain = " -> ".join(step.plugin for step in finding.chain) or "evidence"
        grouped[finding.surface][chain].append(finding)
    styles = {"high": "green", "medium": "yellow", "low": "dim"}
    tree = Tree(f"[bold]Findings ({len(findings)})[/]")
    for surface in sorted(grouped):
        chains = grouped[surface]
        count = sum(len(items) for items in chains.values())
        surface_node = tree.add(f"[bold cyan]{escape(surface)}[/] [dim]({count})[/]")
        for chain in sorted(chains):
            chain_node = surface_node.add(f"[dim]{escape(chain)}[/]")
            for finding in sorted(
                chains[chain],
                key=lambda item: (
                    {"high": 0, "medium": 1, "low": 2}.get(item.confidence, 3),
                    item.offset,
                    item.value,
                ),
            ):
                style = styles.get(finding.confidence, "white")
                chain_node.add(
                    f"[bold]{escape(finding.value)}[/] "
                    f"[{style}]{finding.confidence}[/] "
                    f"[dim]offset 0x{finding.offset:x} · {escape(finding.evidence_path)}[/]"
                )
    return tree


def _all_skips(results: list[tuple[Path, ScanResult]]) -> list[Skip]:
    return sorted(
        (skip for _path, result in results for skip in result.skips),
        key=lambda skip: (skip.kind, skip.plugin, skip.artifact),
    )


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
