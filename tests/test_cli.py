"""Smoke tests for the CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nvox import __version__
from nvox.cli import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_lists_doctor() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "doctor" in result.output


def test_doctor_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    report = json.loads(result.output)
    assert report["environment"]["nvox"] == __version__
    assert report["summary"]["total"] == len(report["tools"])
    assert report["config"]["path"] is None
    assert {tool["category"] for tool in report["tools"]} >= {"image", "audio", "generic"}


def test_doctor_human_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Nvox doctor" in result.output
    assert "Configuration" in result.output


def test_module_entrypoint() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "nvox", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "nvox" in result.stdout


def test_invalid_config_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "nvox.toml").write_text("[scan]\nmax_depth = 0\n", encoding="utf-8")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "config error" in result.output
