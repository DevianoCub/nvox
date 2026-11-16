"""Tests for the ``nvox scan`` command and its JSON report."""

from __future__ import annotations

import base64
import csv
import json
from pathlib import Path

from typer.testing import CliRunner

from fixture_builders import xor_bytes
from nvox.cli import app

runner = CliRunner()


def _invoke(*args: str) -> object:
    return runner.invoke(app, ["scan", *args])


def test_scan_finds_flag_and_writes_report(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    evidence = tmp_path / "note.txt"
    evidence.write_bytes(b"hello ctf{cli_scan_ok}")
    out = tmp_path / "out"
    result = _invoke(str(evidence), "--flag-format", "ctf{}", "--out", str(out))
    assert result.exit_code == 2, result.output
    assert "cli_scan_ok" in result.output
    report = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    assert report["mode"] == "flags"
    assert report["summary"]["findings"] == 1
    assert report["findings"][0]["value"] == "cli_scan_ok"
    assert report["evidence"][0]["path"].endswith("note.txt")
    html = (out / "report.html").read_text(encoding="utf-8")
    assert "cli_scan_ok" in html
    assert "Artifact tree" in html


def test_scan_json_mode_and_clean_exit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    clean = tmp_path / "clean.txt"
    clean.write_text("nothing to find", encoding="utf-8")
    out = tmp_path / "out"
    result = _invoke(str(clean), "--flag-format", "ctf{}", "--out", str(out), "--json")
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["findings"] == []
    assert report["summary"]["files"] == 1

    dirty = tmp_path / "dirty.txt"
    dirty.write_text("ctf{json_flag}", encoding="utf-8")
    result = _invoke(str(dirty), "--flag-format", "ctf{}", "--out", str(out), "--json")
    assert result.exit_code == 2, result.output
    report = json.loads(result.output)
    assert report["findings"][0]["value"] == "json_flag"


def test_scan_raw_regex(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    evidence = tmp_path / "raw.txt"
    evidence.write_bytes(b"FLAG{raw_cli}")
    result = _invoke(
        str(evidence),
        "--flag",
        r"FLAG\{[A-Za-z0-9_]+\}",
        "--out",
        str(tmp_path / "out"),
    )
    assert result.exit_code == 2, result.output
    assert "raw_cli" in result.output


def test_scan_directory_and_excludes_output(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "evidence"
    root.mkdir()
    (root / "a.txt").write_bytes(b"ctf{dir_one}")
    (root / "nested").mkdir()
    (root / "nested" / "b.txt").write_bytes(b"ctf{dir_two}")
    out = root / "out"
    result = _invoke(
        str(root),
        "--flag-format",
        "ctf{}",
        "--out",
        str(out),
        "--json",
    )
    assert result.exit_code == 2, result.output
    report = json.loads(result.output)
    values = {finding["value"] for finding in report["findings"]}
    assert values == {"dir_one", "dir_two"}
    assert report["summary"]["files"] == 2


def test_scan_plugin_filter_and_no_xor(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    encoded = base64.b64encode(b"ctf{filtered_base64}")
    evidence = tmp_path / "encoded.txt"
    evidence.write_bytes(encoded)
    out = tmp_path / "out"
    result = _invoke(
        str(evidence),
        "--flag-format",
        "ctf{}",
        "--plugins",
        "generic.strings",
        "--out",
        str(out),
    )
    assert result.exit_code == 0, result.output

    result = _invoke(
        str(evidence),
        "--flag-format",
        "ctf{}",
        "--plugins",
        "decoder.base64",
        "--out",
        str(out),
    )
    assert result.exit_code == 2, result.output
    assert "filtered_base64" in result.output

    xored = tmp_path / "xored.bin"
    xored.write_bytes(xor_bytes(b"ctf{no_xor}", 0x37))
    result = _invoke(
        str(xored),
        "--flag-format",
        "ctf{}",
        "--out",
        str(out),
        "--no-xor-brute",
    )
    assert result.exit_code == 0, result.output


def test_scan_without_flags_is_artifact_only(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    evidence = tmp_path / "note.txt"
    evidence.write_bytes(b"ctf{not_searched}")
    out = tmp_path / "out"
    result = _invoke(str(evidence), "--out", str(out), "--csv")
    assert result.exit_code == 0, result.output
    assert "Artifact-only mode" in result.output
    assert "findings.json" in result.output
    assert "artifacts.csv" in result.output
    report = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    assert report["mode"] == "artifacts"
    assert report["formats"] == []
    assert report["patterns"] == []
    assert report["findings"] == []
    assert report["summary"]["artifacts"] > 0
    assert report["evidence"][0]["artifacts"]
    with (out / "artifacts.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert rows[0]["evidence_path"].endswith("note.txt")
    assert "artifact_sha256" in rows[0]
    assert "producer" in rows[0]


def test_scan_can_disable_html(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    evidence = tmp_path / "note.txt"
    evidence.write_bytes(b"ctf{no_html}")
    out = tmp_path / "out"
    result = _invoke(str(evidence), "--flag-format", "ctf{}", "--out", str(out), "--no-html")
    assert result.exit_code == 2, result.output
    assert not (out / "report.html").exists()


def test_scan_without_flags_json_mode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    evidence = tmp_path / "note.txt"
    evidence.write_bytes(b"ctf{json_not_searched}")
    result = _invoke(str(evidence), "--out", str(tmp_path / "out"), "--json")
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["mode"] == "artifacts"
    assert report["findings"] == []
    assert report["evidence"][0]["artifacts"]


def test_scan_errors(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    evidence = tmp_path / "note.txt"
    evidence.write_bytes(b"ctf{x}")

    result = _invoke(str(evidence), "--flag-format", "not-a-format")
    assert result.exit_code == 1
    assert "placeholder" in result.output

    result = _invoke(str(tmp_path / "missing"), "--flag-format", "ctf{}")
    assert result.exit_code == 1
    assert "does not exist" in result.output

    result = _invoke(str(evidence), "--flag-format", "ctf{}", "--plugins", "no.such")
    assert result.exit_code == 1
    assert "unknown plugin" in result.output

    result = _invoke(str(evidence), "--flag-format", "ctf{}", "--depth", "0")
    assert result.exit_code == 1
    assert "--depth" in result.output
