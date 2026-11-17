"""Tests for bounded command execution."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from nvox.core.sandbox import SandboxError, SandboxLimits, run_command


def test_run_command_captures_stdout_and_stderr() -> None:
    result = run_command(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('out'); sys.stderr.write('err')",
        ]
    )
    assert result.ok
    assert result.returncode == 0
    assert result.stdout_text == "out"
    assert result.stderr_text == "err"
    assert not result.stdout_truncated
    assert not result.stderr_truncated


def test_output_is_capped_but_process_still_exits() -> None:
    result = run_command(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('x' * 200000); sys.stderr.write('y' * 200000)",
        ],
        limits=SandboxLimits(timeout=10, max_output_bytes=128),
    )
    assert result.ok
    assert result.stdout == b"x" * 128
    assert result.stderr == b"y" * 128
    assert result.stdout_truncated
    assert result.stderr_truncated


def test_timeout_kills_the_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "grandchild-survived"
    # The parent starts a grandchild and then sleeps. Killing only the direct
    # child would leave the grandchild alive to create the marker.
    code = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', "
        f"\"import time; time.sleep(0.6); open({str(marker)!r}, 'w').write('x')\"])\n"
        "time.sleep(5)\n"
    )
    started = time.monotonic()
    result = run_command([sys.executable, "-c", code], limits=SandboxLimits(timeout=0.15))
    elapsed = time.monotonic() - started
    assert result.timed_out
    assert elapsed < 2
    time.sleep(0.8)
    assert not marker.exists()


def test_timeout_is_reported_for_sleeping_command() -> None:
    result = run_command(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        limits=SandboxLimits(timeout=0.05),
    )
    assert result.timed_out
    assert result.returncode is not None


@pytest.mark.skipif(os.name != "posix", reason="RLIMIT_FSIZE is POSIX-specific")
def test_file_size_rlimit_bounds_output(tmp_path: Path) -> None:
    result = run_command(
        [sys.executable, "-c", "open('big.bin', 'wb').write(b'x' * 10000)"],
        cwd=tmp_path,
        limits=SandboxLimits(max_file_bytes=1024),
    )
    assert (tmp_path / "big.bin").stat().st_size <= 1024
    assert result.returncode != 0 or "File too large" in result.stderr_text


def test_missing_executable_raises() -> None:
    with pytest.raises(SandboxError, match="cannot execute"):
        run_command(["nvox-command-that-does-not-exist"])


def test_empty_command_is_rejected() -> None:
    with pytest.raises(SandboxError, match="empty"):
        run_command([])


def test_nul_byte_is_rejected() -> None:
    with pytest.raises(SandboxError, match="NUL"):
        run_command(["echo", "bad\x00argument"])


@pytest.mark.parametrize(
    ("limits", "message"),
    [
        (SandboxLimits(timeout=0), "timeout"),
        (SandboxLimits(max_output_bytes=0), "max_output_bytes"),
        (SandboxLimits(max_file_bytes=0), "max_file_bytes"),
        (SandboxLimits(max_memory_bytes=-1), "max_memory_bytes"),
        (SandboxLimits(max_cpu_seconds=-1), "max_cpu_seconds"),
    ],
)
def test_invalid_limits_are_rejected(limits: SandboxLimits, message: str) -> None:
    with pytest.raises(SandboxError, match=message):
        run_command([sys.executable, "-c", "pass"], limits=limits)


def test_cwd_and_environment_are_honoured(tmp_path: Path) -> None:
    result = run_command(
        [
            sys.executable,
            "-c",
            "import os; print(os.getcwd()); print(os.environ['NVOX_TEST_VALUE'])",
        ],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "NVOX_TEST_VALUE": "present"},
    )
    assert result.ok
    lines = result.stdout_text.splitlines()
    assert lines[0] == str(tmp_path)
    assert lines[1] == "present"
