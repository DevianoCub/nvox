"""Rich live progress for terminal scans."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)

from nvox.core.engine import ScanResult


class ScanProgress:
    """Render live scan counters while the engine emits events.

    The progress display is intentionally ephemeral: the final summary and
    grouped findings are printed after the scan completes. It is disabled for
    JSON stdout mode and for non-interactive output so CI logs stay clean.
    """

    def __init__(self, console: Console, *, total_files: int, enabled: bool = True) -> None:
        self.console = console
        self.total_files = total_files
        self.enabled = enabled
        self.artifacts = 0
        self.plugins = 0
        self.findings = 0
        self.skips = 0
        self.current = ""
        self._progress: Progress | None = None
        self._files_task: TaskID | None = None

    def __enter__(self) -> ScanProgress:
        if not self.enabled:
            return self
        self._progress = Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[bold]{task.description}"),
            BarColumn(bar_width=24),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TextColumn("[dim]{task.fields[detail]}"),
            console=self.console,
            transient=True,
        )
        self._progress.__enter__()
        self._files_task = self._progress.add_task(
            "scanning",
            total=max(1, self.total_files),
            detail="artifacts 0 · plugins 0 · findings 0",
        )
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._progress is not None:
            self._progress.__exit__(exc_type, exc, traceback)  # type: ignore[arg-type]
            self._progress = None

    def start_file(self, path: Path) -> None:
        """Reset per-file counters and show the current evidence name."""
        self.current = path.name
        self.artifacts = 0
        self.plugins = 0
        self.findings = 0
        self.skips = 0
        self._update(f"scanning {path.name}")

    def on_event(self, name: str, payload: Mapping[str, Any]) -> None:
        """Consume an engine event and refresh the live counters."""
        if not self.enabled:
            return
        if name == "artifact":
            self.artifacts += 1
        elif name == "plugin_start":
            self.plugins += 1
        elif name == "finding":
            self.findings += 1
        elif name == "plugin_skip":
            self.skips += 1
        elif name == "scan_end":
            self.artifacts = int(payload.get("artifacts", self.artifacts))
            self.findings = int(payload.get("findings", self.findings))
            self.skips = int(payload.get("skips", self.skips))
        self._update()

    def finish_file(self, result: ScanResult) -> None:
        """Advance the file counter and leave a compact result description."""
        self.artifacts = len(result.artifacts)
        self.plugins = result.plugin_runs
        self.findings = len(result.findings)
        self.skips = len(result.skips)
        if self._progress is not None and self._files_task is not None:
            self._progress.advance(self._files_task)
        self._update(f"{self.current} · {len(result.artifacts)} artifacts")

    def _update(self, description: str | None = None) -> None:
        if self._progress is None or self._files_task is None:
            return
        detail = (
            f"artifacts {self.artifacts} · plugins {self.plugins} · "
            f"findings {self.findings} · skips {self.skips}"
        )
        if description is not None:
            self._progress.update(
                self._files_task,
                description=description,
                detail=detail,
            )
        else:
            self._progress.update(self._files_task, detail=detail)


__all__ = ["ScanProgress"]
