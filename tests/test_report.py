"""Tests for self-contained HTML report generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fixture_builders import make_png
from nvox.api import ArtifactKind, make_artifact
from nvox.core.store import ArtifactStore
from nvox.report import (
    ReportError,
    default_html_path,
    findings_path,
    group_findings,
    load_report,
    write_html_report,
)


def _sample_report(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    root = tmp_path / "out"
    store = ArtifactStore(root=root)
    png = make_png(2, 1, [b"\xff\x00\x00\x00\xff\x00"])
    image = store.put(
        make_artifact(
            kind=ArtifactKind.IMAGE,
            name="challenge.png",
            payload=png,
            media_type="image/png",
        )
    )
    text = store.put(
        make_artifact(
            kind=ArtifactKind.TEXT,
            name="note",
            payload=b"prefix ctf{html_ok} suffix <script>alert(1)</script>",
            media_type="text/plain; charset=utf-8",
            parents=(image.sha256,),
            producer="test.text",
            depth=1,
        )
    )
    finding = {
        "value": "html_ok",
        "format": "ctf{}",
        "surface": "text",
        "offset": 7,
        "context": "prefix ctf{html_ok} suffix",
        "artifact": text.sha256,
        "confidence": "high",
        "chain": [
            {
                "plugin": "test.text",
                "name": "note",
                "sha256": text.sha256,
                "params": {},
            }
        ],
        "evidence": {"sha256": image.sha256, "path": str(tmp_path / "challenge.png")},
    }
    report: dict[str, object] = {
        "nvox": "0.1.0.dev0",
        "mode": "flags",
        "formats": ["ctf{}"],
        "patterns": [],
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:00:01+00:00",
        "duration": 1.0,
        "summary": {
            "files": 1,
            "artifacts": 2,
            "findings": 1,
            "skips": 1,
            "plugin_runs": 3,
            "duration": 1.0,
        },
        "evidence": [
            {
                "path": str(tmp_path / "challenge.png"),
                "evidence": {
                    "path": str(tmp_path / "challenge.png"),
                    "sha256": image.sha256,
                    "size": image.size,
                    "media_type": "image/png",
                },
                "artifacts": [image.to_dict(), text.to_dict()],
                "skips": [
                    {
                        "plugin": "test.skipped",
                        "kind": "requirement",
                        "artifact": image.sha256,
                        "reason": "missing external tool: test-tool",
                    }
                ],
            }
        ],
        "findings": [finding],
    }
    (root / "findings.json").write_text(json.dumps(report), encoding="utf-8")
    return root, report


def test_default_paths_and_load_report(tmp_path: Path) -> None:
    root, report = _sample_report(tmp_path)
    assert findings_path(root) == root / "findings.json"
    assert findings_path(root / "findings.json") == root / "findings.json"
    assert default_html_path(root) == root / "report.html"
    loaded, path = load_report(root)
    assert path == root / "findings.json"
    assert loaded["findings"] == report["findings"]


def test_load_report_errors(tmp_path: Path) -> None:
    with pytest.raises(ReportError, match="not found"):
        load_report(tmp_path / "missing")
    broken = tmp_path / "findings.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(ReportError, match="invalid JSON"):
        load_report(broken)
    broken.write_text("[]", encoding="utf-8")
    with pytest.raises(ReportError, match="JSON object"):
        load_report(broken)
    broken.write_text('{"evidence": []}', encoding="utf-8")
    with pytest.raises(ReportError, match="findings"):
        load_report(broken)


def test_html_report_is_self_contained_and_escaped(tmp_path: Path) -> None:
    root, report = _sample_report(tmp_path)
    output = write_html_report(report, root / "report.html", store_root=root)
    html = output.read_text(encoding="utf-8")
    assert "html_ok" in html
    assert "Artifact tree" in html
    assert "Findings" in html
    assert "data:image/png;base64," in html
    assert "prov-node" in html
    assert "finding-filter" in html
    assert "artifact-filter" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert '<link rel="stylesheet"' not in html
    assert "<script src=" not in html
    assert "missing external tool" in html
    assert "Self-contained forensic scan report" in html


def test_html_report_omits_large_image_previews(tmp_path: Path) -> None:
    root = tmp_path / "out"
    store = ArtifactStore(root=root)
    image = store.put(
        make_artifact(
            kind=ArtifactKind.IMAGE,
            name="large.png",
            payload=b"\x89PNG\r\n\x1a\n" + b"x" * 64,
            media_type="image/png",
        )
    )
    report = {
        "summary": {"files": 1, "artifacts": 1},
        "evidence": [{"path": "large.png", "artifacts": [image.to_dict()], "skips": []}],
        "findings": [],
    }
    output = write_html_report(
        report,
        root / "report.html",
        store_root=root,
        max_preview_bytes=16,
    )
    html = output.read_text(encoding="utf-8")
    assert "Preview omitted" in html
    assert "data:image/png;base64," not in html


def test_group_findings_orders_by_confidence_and_surface() -> None:
    findings = [
        {"value": "low", "surface": "bitstream", "confidence": "low", "offset": 2},
        {"value": "high", "surface": "text", "confidence": "high", "offset": 10},
        {"value": "medium", "surface": "text", "confidence": "medium", "offset": 1},
    ]
    grouped = group_findings(findings)
    assert list(grouped) == ["bitstream", "text"]
    assert [item["value"] for item in grouped["text"]] == ["high", "medium"]
