"""Tests for the ``nvox report`` command."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from nvox.cli import app

runner = CliRunner()


def _make_scan(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    evidence = tmp_path / "note.txt"
    evidence.write_bytes(b"hello ctf{report_cli}")
    out = tmp_path / "out"
    result = runner.invoke(
        app,
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
    assert not (out / "report.html").exists()
    return out


def test_report_command_rerenders_saved_scan(tmp_path: Path) -> None:
    out = _make_scan(tmp_path)
    result = runner.invoke(app, ["report", str(out)])
    assert result.exit_code == 0, result.output
    assert "wrote" in result.output
    html = (out / "report.html").read_text(encoding="utf-8")
    assert "report_cli" in html
    assert "Artifact tree" in html


def test_report_command_accepts_findings_file_and_custom_output(tmp_path: Path) -> None:
    out = _make_scan(tmp_path)
    target = tmp_path / "custom" / "index.html"
    result = runner.invoke(
        app,
        ["report", str(out / "findings.json"), "--out", str(target)],
    )
    assert result.exit_code == 0, result.output
    assert target.is_file()
    assert "report_cli" in target.read_text(encoding="utf-8")


def test_report_command_errors(tmp_path: Path) -> None:
    result = runner.invoke(app, ["report", str(tmp_path / "missing")])
    assert result.exit_code == 1
    assert "findings report not found" in result.output

    broken = tmp_path / "findings.json"
    broken.write_text(json.dumps({"evidence": []}), encoding="utf-8")
    result = runner.invoke(app, ["report", str(broken)])
    assert result.exit_code == 1
    assert "missing findings" in result.output

    out = _make_scan(tmp_path / "scan")
    result = runner.invoke(
        app,
        ["report", str(out), "--max-preview-bytes", "0"],
    )
    assert result.exit_code == 1
    assert "greater than zero" in result.output
