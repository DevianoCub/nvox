"""Self-contained HTML reporting for Nvox scan results.

The HTML report is deliberately dependency-free: all CSS, JavaScript and
preview data are embedded in one file, so it works offline and can be attached
to a ticket or challenge write-up.
"""

from __future__ import annotations

# The self-contained report intentionally keeps long CSS/JS/HTML string lines.
# ruff: noqa: E501
import base64
import html
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

HTML_REPORT_FILENAME = "report.html"
DEFAULT_MAX_PREVIEW_BYTES = 2 * 1024 * 1024
TEXT_PREVIEW_CHARS = 4000
HEX_PREVIEW_BYTES = 256


class ReportError(Exception):
    """Raised when a findings report cannot be read or written."""


def findings_path(source: Path) -> Path:
    """Resolve a findings.json path from a file or scan output directory."""
    source = source.expanduser()
    return source / "findings.json" if source.is_dir() else source


def default_html_path(source: Path) -> Path:
    """Return the default HTML output path for a report source."""
    return findings_path(source).parent / HTML_REPORT_FILENAME


def load_report(source: Path) -> tuple[dict[str, Any], Path]:
    """Load a findings JSON document and return it with its resolved path."""
    path = findings_path(source)
    if not path.is_file():
        raise ReportError(f"findings report not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReportError(f"cannot read {path}: {exc}") from exc
    try:
        report = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReportError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(report, dict):
        raise ReportError(f"{path}: report root must be a JSON object")
    if not isinstance(report.get("evidence"), list):
        raise ReportError(f"{path}: missing evidence list")
    if not isinstance(report.get("findings"), list):
        raise ReportError(f"{path}: missing findings list")
    return report, path


def group_findings(findings: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group findings by surface, preserving useful ordering within groups."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for finding in findings:
        grouped[str(finding.get("surface", "unknown"))].append(finding)
    order = {"high": 0, "medium": 1, "low": 2}
    return {
        surface: sorted(
            items,
            key=lambda item: (
                order.get(str(item.get("confidence", "low")), 3),
                int(item.get("offset", 0)),
                str(item.get("value", "")),
            ),
        )
        for surface, items in sorted(grouped.items())
    }


def chain_label(finding: dict[str, Any]) -> str:
    """Return a compact provenance chain label for a finding."""
    chain = finding.get("chain") or []
    plugins = [str(step.get("plugin", "?")) for step in chain if isinstance(step, dict)]
    return " -> ".join(plugins) or "evidence"


def write_html_report(
    report: dict[str, Any],
    output: Path,
    *,
    store_root: Path | None = None,
    title: str = "Nvox report",
    max_preview_bytes: int = DEFAULT_MAX_PREVIEW_BYTES,
) -> Path:
    """Render ``report`` to a standalone HTML file and return its path."""
    output = output.expanduser()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            render_html_report(
                report,
                store_root=store_root,
                title=title,
                max_preview_bytes=max_preview_bytes,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        raise ReportError(f"cannot write {output}: {exc}") from exc
    return output


def render_html_report(
    report: dict[str, Any],
    *,
    store_root: Path | None = None,
    title: str = "Nvox report",
    max_preview_bytes: int = DEFAULT_MAX_PREVIEW_BYTES,
) -> str:
    """Build the complete standalone HTML document."""
    summary_raw = report.get("summary")
    summary: dict[str, Any] = summary_raw if isinstance(summary_raw, dict) else {}
    findings = [item for item in report.get("findings", []) if isinstance(item, dict)]
    evidence = [item for item in report.get("evidence", []) if isinstance(item, dict)]
    artifacts, children = _collect_artifacts(evidence)
    roots = _root_artifacts(artifacts, children)
    grouped = group_findings(findings)
    skipped = [
        skip for entry in evidence for skip in entry.get("skips", []) if isinstance(skip, dict)
    ]

    parts: list[str] = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{html.escape(title)}</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        '<header class="topbar">',
        f"<div><h1>{html.escape(title)}</h1>",
        '<p class="sub">Self-contained forensic scan report</p></div>',
        f'<div class="mode">{html.escape(str(report.get("mode", "flags")))} mode</div>',
        "</header>",
        "<main>",
        _render_summary(summary, report),
    ]

    if findings:
        parts.append(_render_findings(grouped))
    else:
        parts.append(
            '<section class="panel"><h2>Findings</h2>'
            '<p class="muted">No flags were found in this run.</p></section>'
        )

    parts.append(_render_evidence(evidence, summary))
    parts.append(_render_artifacts(roots, artifacts, children, store_root, max_preview_bytes))
    if skipped:
        parts.append(_render_skips(skipped))
    parts.extend(
        [
            "</main>",
            '<footer class="footer">Generated by Nvox. '
            "No external resources are required to view this report.</footer>",
            f"<script>{_JS}</script>",
            "</body>",
            "</html>",
        ]
    )
    return "\n".join(parts) + "\n"


def _collect_artifacts(
    evidence: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    artifacts: dict[str, dict[str, Any]] = {}
    children: dict[str, list[str]] = defaultdict(list)
    for entry in evidence:
        for artifact in entry.get("artifacts", []):
            if not isinstance(artifact, dict):
                continue
            sha = str(artifact.get("sha256", ""))
            if not sha:
                continue
            artifacts.setdefault(sha, artifact)
            parents = [str(parent) for parent in artifact.get("parents", [])]
            if parents and sha not in children[parents[0]]:
                children[parents[0]].append(sha)
    return artifacts, children


def _root_artifacts(
    artifacts: dict[str, dict[str, Any]], children: dict[str, list[str]]
) -> list[str]:
    roots = [
        sha
        for sha, artifact in artifacts.items()
        if not artifact.get("parents") or str(artifact.get("parents", [""])[0]) not in artifacts
    ]
    if not roots:
        roots = sorted(
            sha for sha in artifacts if not any(sha in group for group in children.values())
        )
    return sorted(roots, key=lambda sha: (str(artifacts[sha].get("name", "")), sha))


def _render_summary(summary: dict[str, Any], report: dict[str, Any]) -> str:
    cards = [
        ("Files", summary.get("files", 0)),
        ("Artifacts", summary.get("artifacts", 0)),
        ("Findings", summary.get("findings", 0)),
        ("Skips", summary.get("skips", 0)),
        ("Plugin runs", summary.get("plugin_runs", 0)),
        ("Duration", f"{float(summary.get('duration', report.get('duration', 0))):.2f}s"),
    ]
    formats = ", ".join(str(item) for item in report.get("formats", [])) or "—"
    patterns = ", ".join(str(item) for item in report.get("patterns", [])) or "—"
    card_html = "".join(
        f'<div class="card"><div class="card-value">{html.escape(str(value))}</div>'
        f'<div class="card-label">{html.escape(label)}</div></div>'
        for label, value in cards
    )
    return (
        '<section class="panel" id="summary">'
        "<h2>Summary</h2>"
        f'<div class="cards">{card_html}</div>'
        '<div class="kv-grid">'
        f"<div><span>Started</span><code>{html.escape(str(report.get('started_at', '—')))}</code></div>"
        f"<div><span>Finished</span><code>{html.escape(str(report.get('finished_at', '—')))}</code></div>"
        f"<div><span>Formats</span><code>{html.escape(formats)}</code></div>"
        f"<div><span>Patterns</span><code>{html.escape(patterns)}</code></div>"
        "</div></section>"
    )


def _render_findings(grouped: dict[str, list[dict[str, Any]]]) -> str:
    buttons = "".join(
        f'<button type="button" class="filter-button" data-confidence="{name}">'
        f"{html.escape(label)}</button>"
        for name, label in (
            ("all", "All"),
            ("high", "High"),
            ("medium", "Medium"),
            ("low", "Low"),
        )
    )
    groups: list[str] = []
    for surface, findings in grouped.items():
        cards = "".join(_render_finding(finding) for finding in findings)
        groups.append(
            f'<div class="finding-group" data-surface="{html.escape(surface, quote=True)}">'
            f'<h3>{html.escape(surface)} <span class="count">{len(findings)}</span></h3>'
            f"{cards}</div>"
        )
    return (
        '<section class="panel" id="findings">'
        "<h2>Findings</h2>"
        '<div class="toolbar">'
        '<input id="finding-filter" class="filter" type="search" '
        'placeholder="Filter findings by flag, chain, evidence or context">'
        f'<div class="filter-buttons">{buttons}</div>'
        "</div>"
        f'<div id="finding-groups">{"".join(groups)}</div>'
        '<p id="finding-empty" class="muted hidden">No findings match the filter.</p>'
        "</section>"
    )


def _render_finding(finding: dict[str, Any]) -> str:
    value = str(finding.get("value", ""))
    confidence = str(finding.get("confidence", "low"))
    surface = str(finding.get("surface", "unknown"))
    chain = chain_label(finding)
    evidence_raw = finding.get("evidence")
    evidence: dict[str, Any] = evidence_raw if isinstance(evidence_raw, dict) else {}
    evidence_path = str(evidence.get("path", ""))
    context = str(finding.get("context", ""))
    search = " ".join((value, confidence, surface, chain, evidence_path, context)).lower()
    graph = _render_provenance(finding)
    return (
        f'<article class="finding" data-confidence="{html.escape(confidence, quote=True)}" '
        f'data-search="{html.escape(search, quote=True)}">'
        f'<div class="finding-head"><code class="flag">{html.escape(value)}</code>'
        f'<span class="badge {html.escape(confidence, quote=True)}">'
        f"{html.escape(confidence)}</span></div>"
        f'<div class="finding-meta"><span>{html.escape(surface)}</span>'
        f"<span>offset 0x{int(finding.get('offset', 0)):x}</span>"
        f"<span>{html.escape(evidence_path or 'unknown evidence')}</span></div>"
        f"{graph}"
        f'<div class="finding-chain">chain: <code>{html.escape(chain)}</code></div>'
        f'<pre class="context">{html.escape(context)}</pre>'
        "</article>"
    )


def _render_provenance(finding: dict[str, Any]) -> str:
    evidence_raw = finding.get("evidence")
    evidence: dict[str, Any] = evidence_raw if isinstance(evidence_raw, dict) else {}
    nodes = [
        ("evidence", str(evidence.get("path", "evidence"))),
    ]
    for step in finding.get("chain", []):
        if isinstance(step, dict):
            label = f"{step.get('plugin', '?')} / {step.get('name', '?')}"
            nodes.append(("step", label))
    nodes.append(("flag", str(finding.get("value", "flag"))))
    chunks: list[str] = []
    for index, (kind, label) in enumerate(nodes):
        if index:
            chunks.append('<span class="arrow">→</span>')
        chunks.append(f'<span class="prov-node {kind}">{html.escape(label)}</span>')
    return f'<div class="prov">{"".join(chunks)}</div>'


def _render_evidence(evidence: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    if not evidence:
        return (
            '<section class="panel"><h2>Evidence</h2>'
            '<p class="muted">No evidence was ingested.</p></section>'
        )
    items: list[str] = []
    for entry in evidence:
        path = html.escape(str(entry.get("path", "unknown")))
        meta_raw = entry.get("evidence")
        meta: dict[str, Any] = meta_raw if isinstance(meta_raw, dict) else {}
        artifacts = entry.get("artifacts", []) if isinstance(entry.get("artifacts"), list) else []
        items.append(
            '<details class="evidence" open>'
            f"<summary><strong>{path}</strong></summary>"
            '<div class="kv-grid">'
            f"<div><span>SHA-256</span><code>{html.escape(str(meta.get('sha256', '—')))}</code></div>"
            f"<div><span>Media type</span><code>{html.escape(str(meta.get('media_type', '—')))}</code></div>"
            f"<div><span>Size</span><code>{html.escape(str(meta.get('size', '—')))} bytes</code></div>"
            f"<div><span>Artifacts</span><code>{len(artifacts)}</code></div>"
            "</div></details>"
        )
    return (
        '<section class="panel" id="evidence">'
        f"<h2>Evidence ({len(evidence)})</h2>"
        f'<p class="muted">{html.escape(str(summary.get("files", len(evidence))))} input file(s).</p>'
        f"{''.join(items)}</section>"
    )


def _render_artifacts(
    roots: list[str],
    artifacts: dict[str, dict[str, Any]],
    children: dict[str, list[str]],
    store_root: Path | None,
    max_preview_bytes: int,
) -> str:
    if not artifacts:
        return (
            '<section class="panel"><h2>Artifacts</h2>'
            '<p class="muted">No artifacts were produced.</p></section>'
        )
    rendered = "".join(
        _render_artifact(sha, artifacts, children, store_root, max_preview_bytes, set(), 0)
        for sha in roots
    )
    return (
        '<section class="panel" id="artifacts">'
        f"<h2>Artifact tree ({len(artifacts)})</h2>"
        '<div class="toolbar"><input id="artifact-filter" class="filter" type="search" '
        'placeholder="Filter artifacts by name, producer, kind or hash"></div>'
        f'<div id="artifact-tree">{rendered}</div>'
        '<p id="artifact-empty" class="muted hidden">No artifacts match the filter.</p>'
        "</section>"
    )


def _render_artifact(
    sha: str,
    artifacts: dict[str, dict[str, Any]],
    children: dict[str, list[str]],
    store_root: Path | None,
    max_preview_bytes: int,
    seen: set[str],
    depth: int,
) -> str:
    artifact = artifacts.get(sha)
    if artifact is None or sha in seen or depth > 64:
        return ""
    seen = set(seen)
    seen.add(sha)
    name = str(artifact.get("name", sha[:12]))
    kind = str(artifact.get("kind", "bytes"))
    producer = str(artifact.get("producer", "?"))
    media_type = str(artifact.get("media_type", "application/octet-stream"))
    size = int(artifact.get("size", 0))
    params = artifact.get("params") if isinstance(artifact.get("params"), dict) else {}
    search = " ".join(
        (name, kind, producer, media_type, sha, json.dumps(params, default=str))
    ).lower()
    preview = _payload_preview(artifact, store_root, max_preview_bytes)
    child_html = "".join(
        _render_artifact(child, artifacts, children, store_root, max_preview_bytes, seen, depth + 1)
        for child in children.get(sha, [])
    )
    param_html = (
        f'<pre class="params">{html.escape(json.dumps(params, indent=2, default=str, sort_keys=True))}</pre>'
        if params
        else '<span class="muted">none</span>'
    )
    return (
        f'<details class="artifact" data-search="{html.escape(search, quote=True)}">'
        "<summary>"
        f'<span class="kind-badge">{html.escape(kind)}</span>'
        f'<span class="artifact-name">{html.escape(name)}</span>'
        f'<span class="artifact-producer">{html.escape(producer)}</span>'
        f'<span class="artifact-size">{size} B</span>'
        f'<code class="short-hash">{html.escape(sha[:12])}</code>'
        "</summary>"
        '<div class="artifact-body">'
        f'<div class="kv-grid"><div><span>SHA-256</span><code>{html.escape(sha)}</code></div>'
        f"<div><span>Media type</span><code>{html.escape(media_type)}</code></div>"
        f"<div><span>Depth</span><code>{html.escape(str(artifact.get('depth', 0)))}</code></div>"
        f"<div><span>Parents</span><code>{html.escape(', '.join(str(p)[:12] for p in artifact.get('parents', [])) or '—')}</code></div>"
        "</div>"
        f'<div class="preview">{preview}</div>'
        f'<div class="params-block"><span>Params</span>{param_html}</div>'
        f"{child_html}"
        "</div></details>"
    )


def _payload_preview(
    artifact: dict[str, Any], store_root: Path | None, max_preview_bytes: int
) -> str:
    sha = str(artifact.get("sha256", ""))
    payload = _load_payload(store_root, sha, max_preview_bytes)
    if payload is None:
        return '<p class="muted">Preview unavailable: artifact payload is not in the store.</p>'
    media_type = str(artifact.get("media_type", "application/octet-stream")).split(";", 1)[0]
    kind = str(artifact.get("kind", "bytes"))
    name = str(artifact.get("name", sha[:12]))
    too_large = len(payload) > max_preview_bytes or int(artifact.get("size", 0)) > max_preview_bytes
    if too_large and (kind in {"image", "audio"} or media_type.startswith(("image/", "audio/"))):
        return (
            f'<p class="muted">Preview omitted: {int(artifact.get("size", 0))} bytes '
            f"exceeds the {max_preview_bytes}-byte embedding limit.</p>"
        )
    if kind == "image" and media_type.startswith("image/"):
        encoded = base64.b64encode(payload).decode("ascii")
        return (
            f'<img class="preview-image" src="data:{html.escape(media_type)};base64,{encoded}" '
            f'alt="{html.escape(name, quote=True)}">'
        )
    if kind == "audio" and media_type.startswith("audio/"):
        encoded = base64.b64encode(payload).decode("ascii")
        return (
            f'<audio class="preview-audio" controls src="data:{html.escape(media_type)};base64,{encoded}">'
            "Your browser does not support embedded audio.</audio>"
        )
    if (
        kind in {"text", "meta"}
        or media_type.startswith("text/")
        or media_type
        in {
            "application/json",
            "application/xml",
        }
    ):
        text = payload.decode("utf-8", errors="replace")
        suffix = "…" if len(text) > TEXT_PREVIEW_CHARS else ""
        return f'<pre class="preview-text">{html.escape(text[:TEXT_PREVIEW_CHARS])}{suffix}</pre>'
    hex_data = payload[:HEX_PREVIEW_BYTES].hex(" ")
    suffix = " …" if len(payload) > HEX_PREVIEW_BYTES else ""
    return f'<pre class="preview-hex">{html.escape(hex_data)}{suffix}</pre>'


def _load_payload(store_root: Path | None, sha: str, max_bytes: int) -> bytes | None:
    if store_root is None or not sha:
        return None
    path = store_root / "artifacts" / sha[:2] / sha
    try:
        with path.open("rb") as handle:
            return handle.read(max_bytes + 1)
    except OSError:
        return None


def _render_skips(skips: list[dict[str, Any]]) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(skip.get('plugin', '—')))}</td>"
        f"<td>{html.escape(str(skip.get('kind', '—')))}</td>"
        f"<td><code>{html.escape(str(skip.get('artifact', '—'))[:12])}</code></td>"
        f"<td>{html.escape(str(skip.get('reason', '—')))}</td>"
        "</tr>"
        for skip in skips
    )
    return (
        '<section class="panel" id="skips">'
        f"<details><summary><strong>Skips ({len(skips)})</strong></summary>"
        "<table><thead><tr><th>Plugin</th><th>Kind</th><th>Artifact</th><th>Reason</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></details></section>"
    )


_CSS = """
:root {
  color-scheme: dark;
  --bg: #0b1020;
  --panel: #131a2e;
  --panel-2: #1a2340;
  --text: #e8eefc;
  --muted: #8e9ab8;
  --line: #2a3556;
  --accent: #5eead4;
  --accent-2: #a78bfa;
  --high: #4ade80;
  --medium: #facc15;
  --low: #94a3b8;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
}
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.topbar {
  display: flex; justify-content: space-between; gap: 1rem; align-items: center;
  padding: 1.4rem 2rem; border-bottom: 1px solid var(--line);
  background: linear-gradient(120deg, #101832, #1b1535);
}
.topbar h1 { margin: 0; font-size: 1.55rem; letter-spacing: .02em; }
.sub { margin: .2rem 0 0; color: var(--muted); }
.mode { border: 1px solid var(--accent); color: var(--accent); padding: .3rem .65rem;
  border-radius: 999px; text-transform: uppercase; font-size: .72rem; letter-spacing: .08em; }
main { max-width: 1250px; margin: 0 auto; padding: 1.5rem; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 14px;
  padding: 1.2rem; margin: 0 0 1.2rem; }
h2 { margin: 0 0 1rem; font-size: 1.15rem; }
h3 { margin: 1rem 0 .6rem; font-size: .95rem; color: var(--accent); }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: .7rem; }
.card { background: var(--panel-2); border: 1px solid var(--line); border-radius: 10px; padding: .8rem; }
.card-value { font-size: 1.45rem; font-weight: 700; color: var(--accent); }
.card-label { color: var(--muted); font-size: .8rem; }
.kv-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: .5rem 1rem; margin-top: .9rem; }
.kv-grid span { display: block; color: var(--muted); font-size: .75rem; text-transform: uppercase; letter-spacing: .05em; }
.kv-grid code { overflow-wrap: anywhere; }
.toolbar { display: flex; gap: .7rem; flex-wrap: wrap; margin-bottom: .8rem; }
.filter { flex: 1 1 280px; background: var(--panel-2); color: var(--text);
  border: 1px solid var(--line); border-radius: 8px; padding: .65rem .8rem; }
.filter:focus { outline: 2px solid var(--accent-2); }
.filter-buttons { display: flex; gap: .4rem; }
.filter-button { background: var(--panel-2); color: var(--text); border: 1px solid var(--line);
  border-radius: 8px; padding: .55rem .75rem; cursor: pointer; }
.filter-button.active { border-color: var(--accent); color: var(--accent); }
.finding { border: 1px solid var(--line); border-left: 3px solid var(--accent-2);
  border-radius: 10px; padding: .8rem; margin: .6rem 0; background: #10162a; }
.finding-head { display: flex; gap: .6rem; align-items: center; justify-content: space-between; flex-wrap: wrap; }
.flag { font-size: 1rem; color: var(--accent); overflow-wrap: anywhere; }
.badge { border-radius: 999px; padding: .15rem .55rem; font-size: .72rem; text-transform: uppercase; }
.badge.high { background: #123b25; color: var(--high); }
.badge.medium { background: #423511; color: var(--medium); }
.badge.low { background: #26304a; color: var(--low); }
.finding-meta { display: flex; gap: .8rem; flex-wrap: wrap; color: var(--muted); font-size: .8rem; margin: .45rem 0; }
.finding-chain { color: var(--muted); font-size: .8rem; }
.context { background: #090d18; border: 1px solid var(--line); border-radius: 8px; padding: .6rem;
  color: #b8c4e0; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; }
.prov { display: flex; align-items: center; gap: .35rem; flex-wrap: wrap; margin: .6rem 0; }
.prov-node { border: 1px solid var(--line); background: var(--panel-2); border-radius: 7px;
  padding: .3rem .5rem; font-size: .76rem; max-width: 28rem; overflow-wrap: anywhere; }
.prov-node.flag { border-color: var(--accent); color: var(--accent); }
.prov-node.evidence { border-color: var(--accent-2); }
.arrow { color: var(--muted); }
.count { color: var(--muted); font-weight: 400; }
.muted { color: var(--muted); }
.hidden { display: none !important; }
.artifact { border: 1px solid var(--line); border-radius: 9px; margin: .45rem 0; background: #10162a; }
.artifact > summary { cursor: pointer; padding: .6rem .7rem; display: flex; gap: .55rem; align-items: center; flex-wrap: wrap; }
.artifact .artifact { margin-left: 1.2rem; }
.kind-badge { background: #252f50; color: #c7d2fe; border-radius: 5px; padding: .1rem .35rem; font-size: .7rem; }
.artifact-name { font-weight: 600; overflow-wrap: anywhere; }
.artifact-producer, .artifact-size, .short-hash { color: var(--muted); font-size: .78rem; }
.artifact-body { border-top: 1px solid var(--line); padding: .7rem; }
.preview { margin-top: .7rem; }
.preview-text, .preview-hex, .params { background: #090d18; border: 1px solid var(--line); border-radius: 7px;
  padding: .6rem; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; }
.preview-image { max-width: 100%; max-height: 420px; border-radius: 7px; border: 1px solid var(--line); }
.preview-audio { width: 100%; }
.params-block { margin-top: .7rem; }
.params-block > span { color: var(--muted); font-size: .75rem; text-transform: uppercase; }
.evidence { border: 1px solid var(--line); border-radius: 9px; padding: .6rem .8rem; margin: .5rem 0; }
.evidence > summary { cursor: pointer; }
table { width: 100%; border-collapse: collapse; margin-top: .8rem; }
th, td { text-align: left; border-bottom: 1px solid var(--line); padding: .45rem .4rem; vertical-align: top; }
th { color: var(--muted); font-size: .78rem; text-transform: uppercase; }
.footer { text-align: center; color: var(--muted); padding: 1rem 2rem 2rem; font-size: .8rem; }
@media (max-width: 700px) { .topbar { padding: 1rem; align-items: flex-start; flex-direction: column; } main { padding: .8rem; } }
"""


_JS = """
(() => {
  const lower = (value) => (value || "").toLowerCase();
  function filterNodes(input, selector, empty) {
    if (!input) return;
    const query = lower(input.value.trim());
    let visible = 0;
    document.querySelectorAll(selector).forEach((node) => {
      const match = !query || lower(node.dataset.search || "").includes(query);
      node.classList.toggle("hidden", !match);
      if (match) visible += 1;
    });
    if (empty) empty.classList.toggle("hidden", visible !== 0);
  }
  const findingInput = document.getElementById("finding-filter");
  const artifactInput = document.getElementById("artifact-filter");
  const findingEmpty = document.getElementById("finding-empty");
  const artifactEmpty = document.getElementById("artifact-empty");
  findingInput?.addEventListener("input", () => {
    filterNodes(findingInput, ".finding", findingEmpty);
    document.querySelectorAll(".finding-group").forEach((group) => {
      group.classList.toggle("hidden", !group.querySelector(".finding:not(.hidden)"));
    });
  });
  artifactInput?.addEventListener("input", () => {
    filterNodes(artifactInput, ".artifact", artifactEmpty);
    document.querySelectorAll(".artifact").forEach((node) => {
      if (!node.dataset.search) return;
      if (lower(node.dataset.search).includes(lower(artifactInput.value.trim()))) {
        node.classList.remove("hidden");
        let parent = node.parentElement?.closest(".artifact");
        while (parent) { parent.classList.remove("hidden"); parent = parent.parentElement?.closest(".artifact"); }
      }
    });
  });
  let confidence = "all";
  function applyConfidence() {
    document.querySelectorAll(".finding").forEach((node) => {
      const keep = confidence === "all" || node.dataset.confidence === confidence;
      node.classList.toggle("hidden", !keep);
    });
    document.querySelectorAll(".finding-group").forEach((group) => {
      group.classList.toggle("hidden", !group.querySelector(".finding:not(.hidden)"));
    });
    document.querySelectorAll(".filter-button").forEach((button) => {
      button.classList.toggle("active", button.dataset.confidence === confidence);
    });
    const visible = document.querySelectorAll(".finding:not(.hidden)").length;
    findingEmpty?.classList.toggle("hidden", visible !== 0);
  }
  document.querySelectorAll(".filter-button").forEach((button) => {
    button.addEventListener("click", () => {
      confidence = button.dataset.confidence || "all";
      applyConfidence();
      if (findingInput) findingInput.dispatchEvent(new Event("input"));
    });
  });
  applyConfidence();
})();
"""


__all__ = [
    "DEFAULT_MAX_PREVIEW_BYTES",
    "HTML_REPORT_FILENAME",
    "ReportError",
    "chain_label",
    "default_html_path",
    "findings_path",
    "group_findings",
    "load_report",
    "render_html_report",
    "write_html_report",
]
