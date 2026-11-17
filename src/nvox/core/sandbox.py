"""Bounded subprocess execution for command plugins and external bridges.

Command plugins are untrusted by nature: they are arbitrary binaries, may hang,
may fork children and may write an unbounded amount of output. This module keeps
the engine responsive with a wall-clock timeout, process-group termination,
streamed output caps and best-effort POSIX resource limits.

The implementation is deliberately conservative. It is not a security boundary
against a hostile local user; it is a cooperative containment layer for tools
invoked on CTF evidence.
"""

from __future__ import annotations

import contextlib
import math
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

try:  # POSIX only; Windows has no resource module.
    import resource as _resource
except ImportError:  # pragma: no cover - exercised on Windows
    _resource = None  # type: ignore[assignment]


DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_OUTPUT_BYTES = 1_000_000
DEFAULT_MAX_FILE_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_MEMORY_BYTES = 0
DEFAULT_MAX_CPU_SECONDS = 0


class SandboxError(Exception):
    """Raised when a command cannot be started or its limits are invalid."""


@dataclass(frozen=True)
class SandboxLimits:
    """Limits applied to one command invocation.

    ``max_memory_bytes`` and ``max_cpu_seconds`` of zero use the wall-clock
    timeout for CPU time and leave address-space limits disabled, which keeps
    JVM-based tools usable. File-size and core-dump limits are always applied
    where the platform supports them.
    """

    timeout: float = DEFAULT_TIMEOUT
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_memory_bytes: int = DEFAULT_MAX_MEMORY_BYTES
    max_cpu_seconds: int = DEFAULT_MAX_CPU_SECONDS

    def validate(self) -> None:
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise SandboxError("timeout must be a positive finite number")
        if self.max_output_bytes < 1:
            raise SandboxError("max_output_bytes must be greater than zero")
        if self.max_file_bytes < 1:
            raise SandboxError("max_file_bytes must be greater than zero")
        if self.max_memory_bytes < 0:
            raise SandboxError("max_memory_bytes must be non-negative")
        if self.max_cpu_seconds < 0:
            raise SandboxError("max_cpu_seconds must be non-negative")


@dataclass(frozen=True)
class CommandResult:
    """A completed or forcibly terminated command."""

    argv: tuple[str, ...]
    returncode: int | None
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    timed_out: bool = False
    duration: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.timed_out and self.returncode == 0

    @property
    def stdout_text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")

    @property
    def stderr_text(self) -> str:
        return self.stderr.decode("utf-8", errors="replace")

    def stderr_tail(self, limit: int = 300) -> str:
        return self.stderr_text[-limit:].strip()

    @property
    def output_truncated(self) -> bool:
        return self.stdout_truncated or self.stderr_truncated


@dataclass
class _Capture:
    data: bytes = b""
    truncated: bool = False
    error: str | None = None


def _validate_argv(argv: tuple[str, ...]) -> None:
    if not argv:
        raise SandboxError("command must not be empty")
    if not argv[0]:
        raise SandboxError("command executable must not be empty")
    for index, part in enumerate(argv):
        if "\x00" in part:
            raise SandboxError(f"command argument {index} contains a NUL byte")


def _read_pipe(stream: BinaryIO, limit: int, capture: _Capture) -> None:
    remaining = limit
    chunks: list[bytes] = []
    try:
        while True:
            raw = stream.read(8192)
            if not raw:
                break
            if remaining <= 0:
                capture.truncated = True
                continue
            kept = raw[:remaining]
            chunks.append(kept)
            remaining -= len(kept)
            if len(kept) < len(raw):
                capture.truncated = True
    except (OSError, ValueError) as exc:  # pipe closed while draining
        capture.error = f"{type(exc).__name__}: {exc}"
    finally:
        capture.data = b"".join(chunks)


def _apply_prlimit(pid: int, limits: SandboxLimits) -> None:
    """Apply limits after spawning a child (Linux: no unsafe preexec_fn)."""
    if _resource is None or not hasattr(_resource, "prlimit"):
        return
    cpu = limits.max_cpu_seconds or max(1, math.ceil(limits.timeout))
    values: list[tuple[int, tuple[int, int]]] = []
    if hasattr(_resource, "RLIMIT_CPU"):
        values.append((_resource.RLIMIT_CPU, (cpu, cpu + 1)))
    if hasattr(_resource, "RLIMIT_FSIZE"):
        values.append((_resource.RLIMIT_FSIZE, (limits.max_file_bytes, limits.max_file_bytes)))
    if hasattr(_resource, "RLIMIT_CORE"):
        values.append((_resource.RLIMIT_CORE, (0, 0)))
    if limits.max_memory_bytes and hasattr(_resource, "RLIMIT_AS"):
        values.append((_resource.RLIMIT_AS, (limits.max_memory_bytes, limits.max_memory_bytes)))
    for resource_id, value in values:
        try:
            _resource.prlimit(pid, resource_id, value)
        except (OSError, ValueError):
            # Best effort: a sandbox limit must not turn a useful tool into a
            # hard failure. Wall-clock timeout and output caps still apply.
            continue


def _child_limits(limits: SandboxLimits) -> Any:
    """Build a preexec function for POSIX systems without ``prlimit``."""

    def apply() -> None:  # pragma: no cover - Linux uses prlimit instead
        if _resource is None:
            return
        cpu = limits.max_cpu_seconds or max(1, math.ceil(limits.timeout))
        pairs: list[tuple[int, tuple[int, int]]] = []
        if hasattr(_resource, "RLIMIT_CPU"):
            pairs.append((_resource.RLIMIT_CPU, (cpu, cpu + 1)))
        if hasattr(_resource, "RLIMIT_FSIZE"):
            pairs.append((_resource.RLIMIT_FSIZE, (limits.max_file_bytes, limits.max_file_bytes)))
        if hasattr(_resource, "RLIMIT_CORE"):
            pairs.append((_resource.RLIMIT_CORE, (0, 0)))
        if limits.max_memory_bytes and hasattr(_resource, "RLIMIT_AS"):
            pairs.append((_resource.RLIMIT_AS, (limits.max_memory_bytes, limits.max_memory_bytes)))
        for resource_id, value in pairs:
            try:
                _resource.setrlimit(resource_id, value)
            except (OSError, ValueError):
                continue

    return apply


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    with contextlib.suppress(ProcessLookupError, OSError):
        process.kill()


def run_command(
    argv: list[str] | tuple[str, ...],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    limits: SandboxLimits | None = None,
) -> CommandResult:
    """Run ``argv`` with streamed output caps, a timeout and process isolation.

    Raises:
        SandboxError: for an empty/invalid command or when the executable cannot
            be started. A timeout is reported through :attr:`CommandResult`.
    """
    args = tuple(str(part) for part in argv)
    _validate_argv(args)
    active = limits or SandboxLimits()
    active.validate()
    started = time.monotonic()

    popen_args: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "cwd": str(cwd) if cwd is not None else None,
        "env": env,
    }
    if os.name == "posix":
        # Create a new process group so a timeout can terminate grandchildren.
        popen_args["process_group"] = 0
    elif os.name == "nt":  # pragma: no cover - exercised on Windows
        popen_args["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    if _resource is not None and not hasattr(_resource, "prlimit") and os.name == "posix":
        popen_args["preexec_fn"] = _child_limits(active)

    try:
        process = subprocess.Popen(args, **popen_args)
    except OSError as exc:
        raise SandboxError(f"cannot execute {args[0]!r}: {exc}") from exc

    if _resource is not None and hasattr(_resource, "prlimit"):
        _apply_prlimit(process.pid, active)

    stdout_capture = _Capture()
    stderr_capture = _Capture()
    readers = [
        threading.Thread(
            target=_read_pipe,
            args=(process.stdout, active.max_output_bytes, stdout_capture),
            daemon=True,
        ),
        threading.Thread(
            target=_read_pipe,
            args=(process.stderr, active.max_output_bytes, stderr_capture),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    timed_out = False
    try:
        process.wait(timeout=active.timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_group(process)
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:  # pragma: no cover - SIGKILL should work
            process.kill()
            process.wait()

    for reader in readers:
        reader.join(timeout=2.0)
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            with contextlib.suppress(OSError):
                stream.close()

    reader_error = stdout_capture.error or stderr_capture.error
    return CommandResult(
        argv=args,
        returncode=process.returncode,
        stdout=stdout_capture.data,
        stderr=stderr_capture.data,
        stdout_truncated=stdout_capture.truncated,
        stderr_truncated=stderr_capture.truncated,
        timed_out=timed_out,
        duration=time.monotonic() - started,
        error=reader_error,
    )


__all__ = [
    "DEFAULT_MAX_FILE_BYTES",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_TIMEOUT",
    "CommandResult",
    "SandboxError",
    "SandboxLimits",
    "run_command",
]
