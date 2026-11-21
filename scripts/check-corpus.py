#!/usr/bin/env python3
"""Run Nvox against a manifest of real CTF or forensic challenge files."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class CorpusError(ValueError):
    """Raised when a corpus manifest is malformed."""


@dataclass(frozen=True)
class Task:
    """One challenge entry from a corpus manifest."""

    name: str
    path: Path
    formats: tuple[str, ...]
    patterns: tuple[str, ...]
    expected: tuple[str, ...]
    category: str
    external: bool
    ignore_case: bool
    plugins: tuple[str, ...]
    depth: int | None
    timeout: float | None
    jobs: int | None
    isolation: str | None


def _string_list(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CorpusError(f"{field} must be a string or a list of strings")
    return tuple(value)


def _optional_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise CorpusError(f"{field} must be an integer")
    return value


def _optional_float(value: object, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CorpusError(f"{field} must be a number")
    return float(value)


def load_tasks(manifest: Path, corpus: Path) -> list[Task]:
    """Load and validate tasks from ``manifest``.

    The top level may be a list of tasks or an object with ``defaults`` and a
    ``tasks`` list. Relative task paths are resolved against ``corpus``.
    """
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CorpusError(f"cannot read manifest {manifest}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CorpusError(f"invalid JSON in {manifest}: {exc}") from exc

    if isinstance(raw, list):
        entries = raw
        defaults: dict[str, Any] = {}
    elif isinstance(raw, dict):
        entries = raw.get("tasks")
        defaults = raw.get("defaults", {})
        if not isinstance(entries, list):
            raise CorpusError("manifest object must contain a 'tasks' list")
        if not isinstance(defaults, dict):
            raise CorpusError("manifest 'defaults' must be an object")
    else:
        raise CorpusError("manifest must be a list or an object with a 'tasks' list")

    tasks: list[Task] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise CorpusError(f"task {index} must be an object")
        merged: dict[str, Any] = {**defaults, **entry}
        name = str(merged.get("name") or f"task-{index}")
        raw_path = merged.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise CorpusError(f"task {name!r}: missing non-empty 'path'")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = corpus / path
        formats = _string_list(merged.get("formats"), f"task {name!r}: formats")
        patterns = _string_list(merged.get("patterns"), f"task {name!r}: patterns")
        expected = _string_list(merged.get("expected"), f"task {name!r}: expected")
        if expected and not (formats or patterns):
            raise CorpusError(f"task {name!r}: expected flags require formats or patterns")
        for fmt in formats:
            if fmt.count("{}") != 1:
                raise CorpusError(f"task {name!r}: format {fmt!r} must contain exactly one '{{}}'")
        isolation = merged.get("isolation")
        if isolation is not None and isolation not in {"thread", "process"}:
            raise CorpusError(f"task {name!r}: isolation must be 'thread' or 'process'")
        tasks.append(
            Task(
                name=name,
                path=path,
                formats=formats,
                patterns=patterns,
                expected=expected,
                category=str(merged.get("category") or ""),
                external=bool(merged.get("external", True)),
                ignore_case=bool(merged.get("ignore_case", False)),
                plugins=_string_list(merged.get("plugins"), f"task {name!r}: plugins"),
                depth=_optional_int(merged.get("depth"), f"task {name!r}: depth"),
                timeout=_optional_float(merged.get("timeout"), f"task {name!r}: timeout"),
                jobs=_optional_int(merged.get("jobs"), f"task {name!r}: jobs"),
                isolation=isolation,
            )
        )
    return tasks


def _unwrap(value: str, fmt: str) -> str | None:
    """Return the flag body if ``value`` matches ``fmt``, otherwise ``None``."""
    if "{}" not in fmt:
        return None
    prefix, suffix = fmt.split("{}", 1)
    if not value.startswith(prefix):
        return None
    if suffix and not value.endswith(suffix):
        return None
    end = len(value) - len(suffix) if suffix else len(value)
    body = value[len(prefix) : end]
    if len(body) >= 2 and body.startswith("{") and body.endswith("}"):
        body = body[1:-1]
    return body


def _expected_candidates(task: Task) -> set[str]:
    """Return exact and unwrapped forms accepted for the expected flags."""
    candidates: set[str] = set()
    for value in task.expected:
        candidates.add(value)
        for fmt in task.formats:
            body = _unwrap(value, fmt)
            if body:
                candidates.add(body)
    return candidates


def _match_expected(task: Task, findings: list[str]) -> tuple[list[str], list[str]]:
    """Return missing expected values and unexpected finding values."""
    ignore_case = task.ignore_case
    normalized = {value.casefold() if ignore_case else value for value in findings}
    missing: list[str] = []
    for expected in task.expected:
        forms = {expected}
        for fmt in task.formats:
            body = _unwrap(expected, fmt)
            if body:
                forms.add(body)
        if not any((form.casefold() if ignore_case else form) in normalized for form in forms):
            missing.append(expected)
    allowed = {value.casefold() if ignore_case else value for value in _expected_candidates(task)}
    unexpected = sorted(
        value for value in findings if (value.casefold() if ignore_case else value) not in allowed
    )
    return missing, unexpected


def _scan_command(task: Task, out: Path) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "nvox",
        "scan",
        str(task.path),
        "--out",
        str(out),
        "--no-html",
    ]
    for fmt in task.formats:
        command.extend(("--flag-format", fmt))
    for pattern in task.patterns:
        command.extend(("--flag", pattern))
    if task.ignore_case:
        command.append("--ignore-case")
    if task.plugins:
        command.extend(("--plugins", ",".join(task.plugins)))
    if task.depth is not None:
        command.extend(("--depth", str(task.depth)))
    if task.timeout is not None:
        command.extend(("--timeout", str(task.timeout)))
    if task.jobs is not None:
        command.extend(("--jobs", str(task.jobs)))
    if task.isolation is not None:
        command.extend(("--isolation", task.isolation))
    if not task.external:
        command.append("--no-external")
    return command


def _environment() -> dict[str, str]:
    env = os.environ.copy()
    source = str(ROOT / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = source if not existing else source + os.pathsep + existing
    return env


def _read_report(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CorpusError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CorpusError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CorpusError(f"{path} does not contain a report object")
    return value


def run_task(
    task: Task,
    out_root: Path,
    index: int,
    *,
    no_external: bool,
    task_timeout: float,
) -> dict[str, Any]:
    """Scan one task and return a serialisable result record."""
    task_out = out_root / f"{index:02d}-{_SAFE_NAME.sub('-', task.name).strip('-') or 'task'}"
    task_out.mkdir(parents=True, exist_ok=True)
    command = _scan_command(task, task_out)
    if no_external and "--no-external" not in command:
        command.append("--no-external")
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=_environment(),
            capture_output=True,
            text=True,
            timeout=task_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "task": task.name,
            "category": task.category,
            "path": str(task.path),
            "status": "error",
            "error": f"scan exceeded task timeout ({task_timeout:g}s)",
            "expected": list(task.expected),
            "found": [],
            "missing": list(task.expected),
            "unexpected": [],
            "duration_seconds": round(time.perf_counter() - started, 3),
            "out": str(task_out),
        }
    duration = round(time.perf_counter() - started, 3)
    if completed.returncode not in (0, 2):
        detail = (completed.stderr or completed.stdout or "").strip()
        if len(detail) > 1200:
            detail = detail[-1200:]
        return {
            "task": task.name,
            "category": task.category,
            "path": str(task.path),
            "status": "error",
            "error": f"nvox exited with {completed.returncode}" + (f": {detail}" if detail else ""),
            "expected": list(task.expected),
            "found": [],
            "missing": list(task.expected),
            "unexpected": [],
            "duration_seconds": duration,
            "out": str(task_out),
        }

    try:
        report = _read_report(task_out / "findings.json")
    except CorpusError as exc:
        return {
            "task": task.name,
            "category": task.category,
            "path": str(task.path),
            "status": "error",
            "error": str(exc),
            "expected": list(task.expected),
            "found": [],
            "missing": list(task.expected),
            "unexpected": [],
            "duration_seconds": duration,
            "out": str(task_out),
        }

    raw_findings = report.get("findings")
    findings: list[str] = []
    if isinstance(raw_findings, list):
        findings = [
            str(item["value"])
            for item in raw_findings
            if isinstance(item, dict) and isinstance(item.get("value"), str)
        ]
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    missing, unexpected = _match_expected(task, findings)
    status = "missing" if missing else "ok"
    return {
        "task": task.name,
        "category": task.category,
        "path": str(task.path),
        "status": status,
        "expected": list(task.expected),
        "found": findings,
        "missing": missing,
        "unexpected": unexpected,
        "artifacts": summary.get("artifacts"),
        "findings": summary.get("findings", len(findings)),
        "skips": summary.get("skips"),
        "duration_seconds": duration,
        "returncode": completed.returncode,
        "out": str(task_out),
    }


def _summary(results: list[dict[str, Any]], *, fail_unexpected: bool) -> dict[str, Any]:
    failed = 0
    for result in results:
        if result["status"] != "ok" or (fail_unexpected and result.get("unexpected")):
            failed += 1
    return {
        "tasks": len(results),
        "passed": len(results) - failed,
        "failed": failed,
        "expected": sum(len(result.get("expected", ())) for result in results),
        "missing": sum(len(result.get("missing", ())) for result in results),
        "unexpected": sum(len(result.get("unexpected", ())) for result in results),
    }


def _print_table(results: list[dict[str, Any]], summary: dict[str, Any], out_root: Path) -> None:
    print(
        f"{'task':<28} {'category':<10} {'expected':>8} {'found':>6} "
        f"{'missing':>7} {'extra':>6} {'artifacts':>10} {'seconds':>8}  status"
    )
    for result in results:
        name = result["task"][:28]
        category = (result.get("category") or "-")[:10]
        artifacts = result.get("artifacts")
        print(
            f"{name:<28} {category:<10} {len(result.get('expected', ())):>8} "
            f"{len(result.get('found', ())):>6} {len(result.get('missing', ())):>7} "
            f"{len(result.get('unexpected', ())):>6} "
            f"{str(artifacts) if artifacts is not None else '-':>10} "
            f"{result.get('duration_seconds', 0):>8.3f}  {result['status']}"
        )
        if result.get("error"):
            print(f"  error: {result['error']}")
        if result.get("missing"):
            print(f"  missing: {', '.join(result['missing'])}")
    print(
        f"\n{summary['passed']}/{summary['tasks']} tasks passed; "
        f"{summary['missing']} expected missing; "
        f"{summary['unexpected']} unexpected; results in {out_root}"
    )


def main(argv: list[str] | None = None) -> int:
    """Run the corpus and return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("corpus/manifest.json"),
        help="Corpus manifest (default: corpus/manifest.json).",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        help="Corpus root for relative task paths (default: manifest directory).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Results directory (default: a new temporary directory).",
    )
    parser.add_argument(
        "--task-timeout",
        type=float,
        default=300.0,
        help="Maximum seconds per task scan (default: 300).",
    )
    parser.add_argument(
        "--no-external",
        action="store_true",
        help="Disable command plugins and external-tool bridges for every task.",
    )
    parser.add_argument(
        "--fail-unexpected",
        action="store_true",
        help="Fail when a task finds flags outside its expected set.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the result summary as JSON instead of a table.",
    )
    args = parser.parse_args(argv)
    if args.task_timeout <= 0:
        parser.error("--task-timeout must be greater than zero")

    manifest = args.manifest.expanduser()
    corpus = args.corpus.expanduser() if args.corpus else manifest.parent
    out_root = args.out.expanduser() if args.out else Path(tempfile.mkdtemp(prefix="nvox-corpus-"))
    try:
        tasks = load_tasks(manifest, corpus)
    except CorpusError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not tasks:
        print("error: manifest contains no tasks", file=sys.stderr)
        return 1
    out_root.mkdir(parents=True, exist_ok=True)

    results = [
        run_task(
            task,
            out_root,
            index,
            no_external=args.no_external,
            task_timeout=args.task_timeout,
        )
        for index, task in enumerate(tasks, start=1)
    ]
    summary = _summary(results, fail_unexpected=args.fail_unexpected)
    if args.json:
        print(json.dumps({"results": results, "summary": summary}, indent=2))
    else:
        _print_table(results, summary, out_root)
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
