"""Tests for the Textual triage interface."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nvox.cli import app as cli_app
from nvox.core.config import Config
from nvox.tui import (
    NvoxTui,
    TuiArtifact,
    TuiFinding,
    artifact_preview,
    finding_preview,
)

pytest.importorskip("textual")

runner = CliRunner()


def _scan(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    evidence = tmp_path / "note.txt"
    evidence.write_bytes(b"hello ctf{tui_report}")
    out = tmp_path / "out"
    result = runner.invoke(
        cli_app,
        [
            "scan",
            str(evidence),
            "--flag-format",
            "ctf{}",
            "--out",
            str(out),
            "--no-html",
        ],
    )
    assert result.exit_code == 2, result.output
    return out


def test_preview_helpers() -> None:
    artifact = TuiArtifact(
        sha256="a" * 64,
        name="note",
        kind="text",
        media_type="text/plain",
        producer="test",
        depth=1,
        size=5,
        parents=(),
        params={"x": 1},
        payload=b"hello",
    )
    preview = artifact_preview(artifact)
    assert "hello" in preview.plain
    assert "params" in preview.plain

    finding = TuiFinding(
        value="flag",
        confidence="high",
        surface="text",
        offset=7,
        evidence_path="/tmp/evidence",
        chain="decoder.base64",
        context="ctx flag",
    )
    finding_text = finding_preview(finding)
    assert "flag" in finding_text.plain
    assert "decoder.base64" in finding_text.plain


def test_tui_loads_report_and_findings(tmp_path: Path) -> None:
    out = _scan(tmp_path)
    app = NvoxTui(report_source=out, auto_scan=False)

    async def run() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app._artifacts
            assert app._findings
            assert app.query_one("#findings-table").row_count == len(app._findings)
            assert app.query_one("#artifact-tree").root.children
            assert "Loaded" in app.query_one("#status").render_line(0).text
            assert len(app.query("Checkbox")) > 0

    asyncio.run(run())


def test_tui_plugin_toggles(tmp_path: Path) -> None:
    app = NvoxTui(out=tmp_path / "out", auto_scan=False)

    async def run() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app._selected_plugins()
            app._set_all_plugins(False)
            assert app._selected_plugins() == []
            app._set_all_plugins(True)
            assert app._selected_plugins()

    asyncio.run(run())


def test_tui_scan_worker(tmp_path: Path) -> None:
    evidence = tmp_path / "note.txt"
    evidence.write_bytes(b"hello ctf{tui_scan}")
    app = NvoxTui(
        path=evidence,
        out=tmp_path / "out",
        config=Config(flag_formats=("ctf{}",), plugin_dirs=()),
        auto_scan=True,
    )

    async def run() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(100):
                await pilot.pause(0.05)
                status = app.query_one("#status").render_line(0).text
                if "Scan complete" in status or "Scan failed" in status:
                    break
            assert "Scan complete" in app.query_one("#status").render_line(0).text
            assert app._artifacts
            assert app._findings

    asyncio.run(run())
