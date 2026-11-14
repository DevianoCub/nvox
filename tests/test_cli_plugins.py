"""Tests for the plugin inspection and artifact extraction commands."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from nvox.api import ArtifactKind, make_artifact
from nvox.cli import app
from nvox.core.store import ArtifactStore

runner = CliRunner()


def test_plugins_list_json(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["plugins", "list", "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    names = {row["name"] for row in rows}
    assert "generic.strings" in names
    strings = next(row for row in rows if row["name"] == "generic.strings")
    assert strings["status"] == "ok"
    assert strings["kind"] == "analyzer"
    assert strings["origin"] == "builtin"
    assert strings["description"]


def test_plugins_list_human(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["plugins", "list"])
    assert result.exit_code == 0, result.output
    assert "generic.strings" in result.output
    assert "Status" in result.output


def test_plugins_info(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["plugins", "info", "generic.strings"])
    assert result.exit_code == 0, result.output
    assert "generic.strings" in result.output
    assert "analyzer" in result.output
    assert "requirements" in result.output


def test_plugins_info_unknown(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["plugins", "info", "does.not.exist"])
    assert result.exit_code == 1
    assert "unknown plugin" in result.output


def test_plugins_dirs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["plugins", "dirs"])
    assert result.exit_code == 0, result.output
    assert "builtin" in result.output
    assert "entry points" in result.output
    assert "directory" in result.output


def _store_with_text(tmp_path: Path, payload: bytes = b"hello") -> tuple[Path, str]:
    root = tmp_path / "out"
    store = ArtifactStore(root=root)
    artifact = store.put(make_artifact(kind=ArtifactKind.TEXT, name="note", payload=payload))
    return root, artifact.sha256


def test_extract_writes_file(tmp_path: Path) -> None:
    root, sha = _store_with_text(tmp_path)
    out = tmp_path / "note.txt"
    result = runner.invoke(app, ["extract", sha, "--store", str(root), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == b"hello"
    assert hashlib.sha256(out.read_bytes()).hexdigest() == sha


def test_extract_prints_path(tmp_path: Path) -> None:
    root, sha = _store_with_text(tmp_path)
    result = runner.invoke(app, ["extract", sha, "--store", str(root)])
    assert result.exit_code == 0, result.output
    assert sha in result.output


def test_extract_refuses_overwrite(tmp_path: Path) -> None:
    root, sha = _store_with_text(tmp_path)
    out = tmp_path / "note.txt"
    out.write_bytes(b"keep me")
    result = runner.invoke(app, ["extract", sha, "--store", str(root), "--out", str(out)])
    assert result.exit_code == 1
    assert "refusing to overwrite" in result.output
    assert out.read_bytes() == b"keep me"


def test_extract_missing_store(tmp_path: Path) -> None:
    result = runner.invoke(app, ["extract", "abcd", "--store", str(tmp_path / "absent")])
    assert result.exit_code == 1
    assert "no artifact store" in result.output


def test_extract_unknown_artifact(tmp_path: Path) -> None:
    root, _ = _store_with_text(tmp_path)
    result = runner.invoke(app, ["extract", "deadbeef", "--store", str(root)])
    assert result.exit_code == 1
    assert "no artifact matching" in result.output


def test_extract_detects_store_corruption(tmp_path: Path) -> None:
    root = tmp_path / "out"
    fake_sha = "a" * 64
    artifact_dir = root / "artifacts" / fake_sha[:2]
    artifact_dir.mkdir(parents=True)
    (artifact_dir / fake_sha).write_bytes(b"not the real payload")
    result = runner.invoke(app, ["extract", fake_sha, "--store", str(root)])
    assert result.exit_code == 1
    assert "integrity error" in result.output
