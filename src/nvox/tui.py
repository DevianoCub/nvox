"""Textual triage interface for Nvox.

The TUI is intentionally a thin layer over the same engine and report data used
by the CLI. It can load an existing ``findings.json`` or run a new scan with a
chosen subset of plugins.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Static,
    TabbedContent,
    TabPane,
    Tree,
)
from textual.widgets.tree import TreeNode

from nvox.api import Artifact
from nvox.core.config import Config
from nvox.core.engine import Engine, ScanResult
from nvox.core.findings import Finding
from nvox.core.flags import FlagFormatError, FlagScanner, build_flag_specs
from nvox.core.registry import PluginRegistry
from nvox.core.store import ArtifactStore, ArtifactStoreError
from nvox.report import ReportError, load_report

PREVIEW_LIMIT = 4000
HEX_LIMIT = 256


@dataclass(frozen=True)
class TuiArtifact:
    """Artifact metadata plus an optional payload for the preview pane."""

    sha256: str
    name: str
    kind: str
    media_type: str
    producer: str
    depth: int
    size: int
    parents: tuple[str, ...]
    params: dict[str, Any]
    payload: bytes | None = None


@dataclass(frozen=True)
class TuiFinding:
    """Finding data needed by the table and preview pane."""

    value: str
    confidence: str
    surface: str
    offset: int
    evidence_path: str
    chain: str
    context: str


def _artifact_from_object(artifact: Artifact) -> TuiArtifact:
    return TuiArtifact(
        sha256=artifact.sha256,
        name=artifact.name,
        kind=artifact.kind.value,
        media_type=artifact.media_type,
        producer=artifact.producer,
        depth=artifact.depth,
        size=artifact.size,
        parents=tuple(artifact.parents),
        params=dict(artifact.params),
        payload=artifact.payload,
    )


def artifacts_from_results(results: list[tuple[Path, ScanResult]]) -> list[TuiArtifact]:
    """Convert live scan results to the TUI model, deduplicating by hash."""
    seen: dict[str, TuiArtifact] = {}
    for _path, result in results:
        for artifact in result.artifacts:
            seen.setdefault(artifact.sha256, _artifact_from_object(artifact))
    return list(seen.values())


def findings_from_results(results: list[tuple[Path, ScanResult]]) -> list[TuiFinding]:
    """Convert live scan findings to the TUI model."""
    findings: list[TuiFinding] = []
    seen: set[tuple[str, str, int]] = set()
    for _path, result in results:
        for finding in result.findings:
            key = (finding.value, finding.artifact, finding.offset)
            if key in seen:
                continue
            seen.add(key)
            findings.append(_finding_from_object(finding))
    return findings


def _finding_from_object(finding: Finding) -> TuiFinding:
    return TuiFinding(
        value=finding.value,
        confidence=finding.confidence,
        surface=finding.surface,
        offset=finding.offset,
        evidence_path=finding.evidence_path,
        chain=" -> ".join(step.plugin for step in finding.chain) or "evidence",
        context=finding.context,
    )


def artifacts_from_report(report: dict[str, Any], store_root: Path) -> list[TuiArtifact]:
    """Convert report JSON artifacts to the TUI model."""
    artifacts: dict[str, TuiArtifact] = {}
    for entry in report.get("evidence", []):
        if not isinstance(entry, dict):
            continue
        for artifact in entry.get("artifacts", []):
            if not isinstance(artifact, dict):
                continue
            sha = str(artifact.get("sha256", ""))
            if not sha or sha in artifacts:
                continue
            artifacts[sha] = TuiArtifact(
                sha256=sha,
                name=str(artifact.get("name", sha[:12])),
                kind=str(artifact.get("kind", "bytes")),
                media_type=str(artifact.get("media_type", "application/octet-stream")),
                producer=str(artifact.get("producer", "?")),
                depth=int(artifact.get("depth", 0)),
                size=int(artifact.get("size", 0)),
                parents=tuple(str(parent) for parent in artifact.get("parents", [])),
                params=dict(artifact.get("params") or {}),
                payload=_read_store_payload(store_root, sha),
            )
    return list(artifacts.values())


def findings_from_report(report: dict[str, Any]) -> list[TuiFinding]:
    """Convert report JSON findings to the TUI model."""
    findings: list[TuiFinding] = []
    for item in report.get("findings", []):
        if not isinstance(item, dict):
            continue
        evidence_raw = item.get("evidence")
        evidence: dict[str, Any] = evidence_raw if isinstance(evidence_raw, dict) else {}
        findings.append(
            TuiFinding(
                value=str(item.get("value", "")),
                confidence=str(item.get("confidence", "low")),
                surface=str(item.get("surface", "unknown")),
                offset=int(item.get("offset", 0)),
                evidence_path=str(evidence.get("path", "")),
                chain=" -> ".join(
                    str(step.get("plugin", "?"))
                    for step in item.get("chain", [])
                    if isinstance(step, dict)
                )
                or "evidence",
                context=str(item.get("context", "")),
            )
        )
    return findings


def _read_store_payload(store_root: Path, sha: str, limit: int = 2 * 1024 * 1024) -> bytes | None:
    path = store_root / "artifacts" / sha[:2] / sha
    try:
        with path.open("rb") as handle:
            return handle.read(limit + 1)
    except OSError:
        return None


def artifact_preview(artifact: TuiArtifact) -> Text:
    """Create a Rich preview for an artifact in the TUI."""
    text = Text()
    text.append(f"{artifact.name}\n", style="bold cyan")
    text.append(f"{artifact.kind} · {artifact.media_type}\n", style="dim")
    text.append(f"sha256  {artifact.sha256}\n")
    text.append(f"producer {artifact.producer} · depth {artifact.depth} · {artifact.size} bytes\n")
    if artifact.parents:
        text.append("parents  " + ", ".join(parent[:12] for parent in artifact.parents) + "\n")
    if artifact.params:
        text.append("params   " + json.dumps(artifact.params, sort_keys=True, default=str) + "\n")
    if artifact.payload is None:
        text.append("\nPreview unavailable: payload is not in the artifact store.", style="yellow")
        return text
    payload = artifact.payload
    text.append("\n")
    if artifact.kind in {"text", "meta"} or artifact.media_type.startswith("text/"):
        decoded = payload.decode("utf-8", errors="replace")
        text.append(decoded[:PREVIEW_LIMIT])
        if len(decoded) > PREVIEW_LIMIT:
            text.append("\n… preview truncated", style="dim")
    elif artifact.kind == "image" and artifact.media_type.split(";", 1)[0] == "image/png":
        size = _png_size(payload)
        if size is not None:
            text.append(f"PNG image: {size[0]} x {size[1]} pixels\n", style="green")
        text.append(payload[:HEX_LIMIT].hex(" "))
        if len(payload) > HEX_LIMIT:
            text.append("\n… binary preview truncated", style="dim")
    else:
        text.append(payload[:HEX_LIMIT].hex(" "))
        if len(payload) > HEX_LIMIT:
            text.append("\n… binary preview truncated", style="dim")
    return text


def _png_size(payload: bytes) -> tuple[int, int] | None:
    if len(payload) < 24 or payload[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return int.from_bytes(payload[16:20], "big"), int.from_bytes(payload[20:24], "big")


def finding_preview(finding: TuiFinding) -> Text:
    text = Text()
    text.append(finding.value + "\n", style="bold cyan")
    text.append(
        f"{finding.surface} · confidence {finding.confidence} · offset 0x{finding.offset:x}\n",
        style="dim",
    )
    text.append(f"evidence {finding.evidence_path or 'unknown'}\n")
    text.append(f"chain    {finding.chain}\n\n")
    text.append(finding.context)
    return text


class NvoxTui(App[None]):
    """Interactive artifact and finding browser."""

    TITLE = "Nvox TUI"
    SUB_TITLE = "artifact triage"
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("q", "quit", "Quit"),
        Binding("s", "scan", "Scan"),
        Binding("r", "reload", "Reload report"),
    ]
    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #tree-pane { width: 42%; min-width: 30; border: round $accent; }
    #right-pane { width: 1fr; }
    #artifact-tree { height: 1fr; }
    #preview { height: 1fr; padding: 1 2; overflow: auto; }
    #findings-table { height: 1fr; }
    #plugin-list { height: 1fr; padding: 0 1; }
    #plugins-tab { padding: 1; }
    #path-input { margin: 1 0; }
    #flag-input, #pattern-input { margin: 0 0 1 0; }
    #plugin-buttons { height: auto; margin: 1 0; }
    #status { height: 1; padding: 0 1; background: $panel; color: $text-muted; }
    Button { margin-right: 1; }
    """

    def __init__(
        self,
        *,
        path: Path | None = None,
        report_source: Path | None = None,
        out: Path = Path("out"),
        config: Config | None = None,
        auto_scan: bool = True,
    ) -> None:
        super().__init__()
        self.path = path
        self.report_source = report_source
        self.out = out
        self.config = config if config is not None else Config.default()
        self.auto_scan = auto_scan and path is not None and report_source is None
        self.registry = PluginRegistry().load_all(self.config)
        self._artifacts: list[TuiArtifact] = []
        self._findings: list[TuiFinding] = []
        self._findings_by_key: dict[str, TuiFinding] = {}
        self._artifact_by_sha: dict[str, TuiArtifact] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="tree-pane"):
                yield Tree[TuiArtifact]("Artifacts", id="artifact-tree")
            with Vertical(id="right-pane"), TabbedContent(initial="preview-tab"):
                with TabPane("Preview", id="preview-tab"):
                    yield Static("Select an artifact or finding.", id="preview")
                with TabPane("Findings", id="findings-tab"):
                    table: DataTable[str] = DataTable(id="findings-table", cursor_type="row")
                    table.add_columns("Flag", "Conf", "Surface", "Chain", "Evidence")
                    yield table
                with TabPane("Plugins", id="plugins-tab"):
                    with VerticalScroll(id="plugin-list"):
                        yield from self._plugin_checkboxes()
                    yield Input(
                        value=str(self.path) if self.path is not None else "",
                        placeholder="Evidence file or directory",
                        id="path-input",
                    )
                    yield Input(
                        value=", ".join(self.config.flag_formats),
                        placeholder="Flag format, e.g. ctf{}",
                        id="flag-input",
                    )
                    yield Input(
                        placeholder="Raw flag regex (optional)",
                        id="pattern-input",
                    )
                    with Horizontal(id="plugin-buttons"):
                        yield Button("Scan", id="scan", variant="primary")
                        yield Button("All plugins", id="select-all")
                        yield Button("No plugins", id="select-none")
        yield Static("Ready", id="status")
        yield Footer()

    def _plugin_checkboxes(self) -> list[Checkbox]:
        checkboxes: list[Checkbox] = []
        for index, record in enumerate(self.registry.records):
            plugin = record.plugin
            enabled = plugin.name not in self.config.disabled_plugins
            checkboxes.append(
                Checkbox(
                    f"{plugin.name}  [{plugin.kind.value}]",
                    value=enabled,
                    id=f"plugin-{index}",
                    name=plugin.name,
                )
            )
        return checkboxes

    def on_mount(self) -> None:
        tree = self.query_one("#artifact-tree", Tree)
        tree.root.expand()
        if self.report_source is not None:
            self._load_report(self.report_source)
        elif self.auto_scan and self.path is not None:
            self._start_scan()
        else:
            self._set_status("Ready. Enter an evidence path and press Scan.")

    def _load_report(self, source: Path) -> None:
        try:
            report, report_path = load_report(source)
        except ReportError as exc:
            self._set_status(str(exc))
            return
        self._artifacts = artifacts_from_report(report, report_path.parent)
        self._findings = findings_from_report(report)
        self._refresh_views()
        summary_raw = report.get("summary")
        summary: dict[str, Any] = summary_raw if isinstance(summary_raw, dict) else {}
        self._set_status(
            f"Loaded {report_path} · {summary.get('artifacts', len(self._artifacts))} artifacts · "
            f"{summary.get('findings', len(self._findings))} findings"
        )

    def action_scan(self) -> None:
        self._start_scan()

    def action_reload(self) -> None:
        if self.report_source is not None:
            self._load_report(self.report_source)
        else:
            self._set_status("No report source to reload.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "scan":
            self._start_scan()
        elif event.button.id == "select-all":
            self._set_all_plugins(True)
        elif event.button.id == "select-none":
            self._set_all_plugins(False)

    def _set_all_plugins(self, value: bool) -> None:
        for checkbox in self.query(Checkbox):
            checkbox.value = value

    def _selected_plugins(self) -> list[str]:
        return [checkbox.name or "" for checkbox in self.query(Checkbox) if checkbox.value]

    def _start_scan(self) -> None:
        path_text = self.query_one("#path-input", Input).value.strip()
        if not path_text:
            self._set_status("Enter an evidence path first.")
            return
        selected = [name for name in self._selected_plugins() if name]
        if not selected:
            self._set_status("Select at least one plugin.")
            return
        flag_text = self.query_one("#flag-input", Input).value.strip()
        pattern_text = self.query_one("#pattern-input", Input).value.strip()
        formats = [part.strip() for part in flag_text.split(",") if part.strip()]
        patterns = [part.strip() for part in pattern_text.split(",") if part.strip()]
        self._set_status(f"Scanning {path_text} with {len(selected)} plugin(s)…")
        self._scan_worker(Path(path_text).expanduser(), selected, formats, patterns)

    @work(thread=True, exclusive=True, group="scan")
    def _scan_worker(
        self,
        target: Path,
        plugins: list[str],
        formats: list[str],
        patterns: list[str],
    ) -> None:
        try:
            files = _collect_inputs(target, self.out)
            if not files:
                raise ValueError(f"no files found in {target}")
            registry = self.registry.subset(plugins)
            scanner: FlagScanner | None = None
            if formats or patterns:
                scanner = FlagScanner(build_flag_specs(formats, patterns))
            store = ArtifactStore(
                root=self.out,
                max_bytes=self.config.limits.max_artifact_bytes,
            )
            results: list[tuple[Path, ScanResult]] = []
            for evidence_path in files:
                engine = Engine(
                    self.config,
                    registry=registry,
                    store=store,
                    flag_scanner=scanner,
                    jobs=self.config.limits.effective_jobs(),
                )
                result = engine.analyze(evidence_path)
                results.append((evidence_path, result))
                self.call_from_thread(self._show_partial, evidence_path, result)
            self.call_from_thread(self._scan_finished, results)
        except (OSError, ValueError, ArtifactStoreError, FlagFormatError) as exc:
            self.call_from_thread(self._set_status, f"Scan failed: {exc}")
        except Exception as exc:  # pragma: no cover - defensive TUI boundary
            self.call_from_thread(self._set_status, f"Scan failed: {type(exc).__name__}: {exc}")

    def _show_partial(self, path: Path, result: ScanResult) -> None:
        self._set_status(
            f"{path.name}: {len(result.artifacts)} artifacts, "
            f"{len(result.findings)} findings, {len(result.skips)} skips"
        )

    def _scan_finished(self, results: list[tuple[Path, ScanResult]]) -> None:
        self._artifacts = artifacts_from_results(results)
        self._findings = findings_from_results(results)
        self._refresh_views()
        findings = len(self._findings)
        self._set_status(
            f"Scan complete: {len(self._artifacts)} artifacts, {findings} finding(s). "
            "Press s to rescan."
        )

    def _refresh_views(self) -> None:
        self._populate_artifacts()
        self._populate_findings()
        if self._findings:
            self._show_finding(self._findings[0])
        elif self._artifacts:
            self._show_artifact(self._artifacts[0])

    def _populate_artifacts(self) -> None:
        tree = self.query_one("#artifact-tree", Tree)
        tree.clear()
        self._artifact_by_sha = {artifact.sha256: artifact for artifact in self._artifacts}
        children: dict[str, list[TuiArtifact]] = {}
        for artifact in self._artifacts:
            if artifact.parents:
                children.setdefault(artifact.parents[0], []).append(artifact)
        roots = [
            artifact
            for artifact in self._artifacts
            if not artifact.parents or artifact.parents[0] not in self._artifact_by_sha
        ]
        for root in sorted(roots, key=lambda item: (item.name, item.sha256)):
            self._add_artifact_node(tree.root, root, children, set())
        tree.root.expand()

    def _add_artifact_node(
        self,
        parent: TreeNode[TuiArtifact],
        artifact: TuiArtifact,
        children: dict[str, list[TuiArtifact]],
        seen: set[str],
    ) -> None:
        if artifact.sha256 in seen:
            return
        seen = set(seen)
        seen.add(artifact.sha256)
        label = f"{artifact.kind:7} {artifact.name}  [{artifact.producer}]"
        node = parent.add(label, data=artifact, expand=artifact.depth < 1)
        for child in sorted(children.get(artifact.sha256, []), key=lambda item: item.name):
            self._add_artifact_node(node, child, children, seen)

    def _populate_findings(self) -> None:
        table = self.query_one("#findings-table", DataTable)
        table.clear()
        self._findings_by_key = {}
        for index, finding in enumerate(self._findings):
            key = str(index)
            self._findings_by_key[key] = finding
            table.add_row(
                finding.value,
                finding.confidence,
                finding.surface,
                finding.chain,
                finding.evidence_path,
                key=key,
            )

    def on_tree_node_selected(self, event: Tree.NodeSelected[TuiArtifact]) -> None:
        if event.node.data is not None:
            self._show_artifact(event.node.data)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        finding = self._findings_by_key.get(str(event.row_key.value))
        if finding is not None:
            self._show_finding(finding)

    def _show_artifact(self, artifact: TuiArtifact) -> None:
        self.query_one("#preview", Static).update(artifact_preview(artifact))

    def _show_finding(self, finding: TuiFinding) -> None:
        self.query_one("#preview", Static).update(finding_preview(finding))

    def _set_status(self, message: str) -> None:
        self.query_one("#status", Static).update(message)


def _collect_inputs(target: Path, out_dir: Path) -> list[Path]:
    if not target.exists():
        raise ValueError(f"input does not exist: {target}")
    if target.is_file():
        return [target]
    if not target.is_dir():
        raise ValueError(f"input is neither a file nor a directory: {target}")
    out_resolved = out_dir.resolve()
    files: list[Path] = []
    for candidate in sorted(target.rglob("*")):
        if not candidate.is_file():
            continue
        try:
            candidate.resolve().relative_to(out_resolved)
        except ValueError:
            files.append(candidate)
    return files


__all__ = [
    "NvoxTui",
    "TuiArtifact",
    "TuiFinding",
    "artifact_preview",
    "artifacts_from_report",
    "artifacts_from_results",
    "finding_preview",
    "findings_from_report",
    "findings_from_results",
]
