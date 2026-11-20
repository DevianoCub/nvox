"""Integration tests for the real-case corpus runner."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-corpus.py"


def _run(
    manifest: Path,
    out: Path,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--manifest",
            str(manifest),
            "--out",
            str(out),
            "--no-external",
            *extra,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_check_corpus_finds_expected_token(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    task_dir = corpus / "binary-token"
    task_dir.mkdir(parents=True)
    token = base64.b64encode(b"ctf{corpus_ok}")
    (task_dir / "challenge.bin").write_bytes(b"\x00\xff noise " + token + b" \x00\xff")
    manifest = corpus / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "name": "binary-token",
                    "path": "binary-token",
                    "formats": ["ctf{}"],
                    "expected": ["ctf{corpus_ok}"],
                    "category": "bin",
                }
            ]
        ),
        encoding="utf-8",
    )

    completed = _run(manifest, tmp_path / "results", "--json")
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["summary"]["passed"] == 1
    result = payload["results"][0]
    assert result["status"] == "ok"
    assert "corpus_ok" in result["found"]
    assert result["unexpected"] == []
    assert Path(result["out"], "findings.json").is_file()


def test_check_corpus_reports_missing_and_unexpected(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "challenge.bin").write_bytes(b"ctf{present}\x00")
    manifest = corpus / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "defaults": {"formats": ["ctf{}"]},
                "tasks": [
                    {
                        "name": "missing",
                        "path": "challenge.bin",
                        "expected": ["ctf{absent}"],
                    },
                    {
                        "name": "unexpected",
                        "path": "challenge.bin",
                        "expected": [],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    completed = _run(manifest, tmp_path / "results", "--json")
    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["summary"]["missing"] == 1
    assert payload["results"][0]["status"] == "missing"
    assert payload["results"][1]["unexpected"] == ["present"]

    strict = _run(manifest, tmp_path / "results-strict", "--json", "--fail-unexpected")
    assert strict.returncode == 1
    strict_payload = json.loads(strict.stdout)
    assert strict_payload["summary"]["failed"] == 2
