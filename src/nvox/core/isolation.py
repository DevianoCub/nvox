"""Optional process isolation for Python plugin execution.

The default engine runner executes plugins in a cooperative worker thread and
relies on :class:`nvox.api.Context` deadlines.  A plugin that blocks in C code
or ignores the deadline cannot be interrupted that way.  This module adds an
opt-in ``scan.isolation = "process"`` mode that executes eligible Python
plugins in a spawned child process and kills the child when it exceeds its time
budget.

Only plugins that can be discovered again from the configured plugin
directories are eligible.  Ad-hoc plugins registered directly on a registry,
and external command plugins, stay on the thread runner.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any, Literal

from nvox.api import Artifact, ArtifactKind, Context, PluginSkip, make_artifact
from nvox.core.registry import PluginRegistry, RegisteredPlugin, is_external

IsolatedStatus = Literal["ok", "skip", "error", "timeout"]


@dataclass(frozen=True)
class IsolatedResult:
    """Serialised outcome of one isolated plugin invocation."""

    status: IsolatedStatus
    artifacts: tuple[Artifact, ...] = ()
    reason: str = ""


def can_isolate(record: RegisteredPlugin | None) -> bool:
    """Whether a plugin can be reloaded by the isolated worker."""
    if record is None or is_external(record):
        return False
    return record.origin in {"builtin", "directory", "entry-point"}


def run_isolated(
    *,
    plugin_name: str,
    artifact: Artifact,
    options: Mapping[str, Any],
    max_artifact_bytes: int,
    depth: int,
    timeout: float,
    plugin_dirs: Iterable[Path],
) -> IsolatedResult:
    """Run one plugin in a spawned process, killing it on timeout.

    The parent waits on a pipe rather than relying on the child to honour a
    deadline.  This also contains crashes such as ``os._exit`` or a fatal
    interpreter error.
    """
    context = mp.get_context("spawn")
    parent_conn, child_conn = context.Pipe(duplex=False)
    request = {
        "plugin_name": plugin_name,
        "artifact": _artifact_to_dict(artifact),
        "options": dict(options),
        "max_artifact_bytes": max_artifact_bytes,
        "depth": depth,
        "timeout": timeout,
        "plugin_dirs": [str(path) for path in plugin_dirs],
    }
    process = context.Process(target=_worker_main, args=(child_conn, request), daemon=True)
    started = time.monotonic()
    try:
        process.start()
        child_conn.close()
        remaining = max(0.0, timeout - (time.monotonic() - started))
        if not parent_conn.poll(remaining):
            _terminate(process)
            return IsolatedResult("timeout", reason=f"exceeded timeout of {timeout:g}s")
        try:
            response = parent_conn.recv()
        except EOFError:
            return IsolatedResult(
                "error",
                reason=f"isolated worker exited without a result (code {process.exitcode})",
            )
    finally:
        parent_conn.close()
        _terminate(process)

    status = response.get("status")
    if status == "ok":
        return IsolatedResult(
            "ok",
            artifacts=tuple(_artifact_from_dict(item) for item in response["artifacts"]),
        )
    if status == "skip":
        return IsolatedResult("skip", reason=str(response.get("reason", "plugin requested a skip")))
    return IsolatedResult("error", reason=str(response.get("reason", "isolated plugin failed")))


def _terminate(process: BaseProcess) -> None:
    if process.pid is None:
        return
    if process.is_alive():
        process.terminate()
        process.join(1.0)
    if process.is_alive():  # pragma: no cover - only on a wedged process
        process.kill()
        process.join(1.0)
    else:
        process.join()


def _worker_main(conn: Connection, request: dict[str, Any]) -> None:
    try:
        registry = PluginRegistry()
        registry.load_builtin()
        for directory in request["plugin_dirs"]:
            registry.load_directory(Path(directory))
        registry.load_entry_points()
        registry.load_declared()
        plugin = registry.get(request["plugin_name"])
        if plugin is None:
            conn.send(
                {"status": "error", "reason": "plugin is not available in the isolated worker"}
            )
            return
        artifact = _artifact_from_dict(request["artifact"])
        ctx = Context(
            artifact,
            plugin.name,
            max_artifact_bytes=request["max_artifact_bytes"],
            depth=request["depth"],
            options=request["options"],
            deadline=time.monotonic() + request["timeout"],
        )
        try:
            result = plugin.run(artifact, ctx)
            if isinstance(result, Iterable) and not isinstance(result, (str, bytes, bytearray)):
                for item in result:
                    if isinstance(item, Artifact):
                        ctx.adopt(item)
        finally:
            ctx.close()
        conn.send(
            {
                "status": "ok",
                "artifacts": [_artifact_to_dict(item) for item in ctx.artifacts],
            }
        )
    except PluginSkip as exc:
        conn.send({"status": "skip", "reason": exc.reason})
    except BaseException as exc:  # report child failures to the parent
        conn.send({"status": "error", "reason": f"{type(exc).__name__}: {exc}"})
    finally:
        conn.close()


def _artifact_to_dict(artifact: Artifact) -> dict[str, Any]:
    return {
        "kind": artifact.kind.value,
        "name": artifact.name,
        "payload": artifact.payload,
        "media_type": artifact.media_type,
        "parents": list(artifact.parents),
        "producer": artifact.producer,
        "params": json.dumps(dict(artifact.params), default=str),
        "depth": artifact.depth,
    }


def _artifact_from_dict(data: Mapping[str, Any]) -> Artifact:
    return make_artifact(
        kind=ArtifactKind(str(data["kind"])),
        name=str(data["name"]),
        payload=bytes(data["payload"]),
        media_type=str(data["media_type"]),
        parents=tuple(str(item) for item in data["parents"]),
        producer=str(data["producer"]),
        params=json.loads(str(data["params"])),
        depth=int(data["depth"]),
    )
