#!/usr/bin/env python3
"""Repeatable full-pipeline benchmark used for accelerator decisions."""

from __future__ import annotations

import argparse
import io
import json
import struct
import tempfile
import time
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from nvox.core.config import Config, ScanLimits
from nvox.core.engine import Engine
from nvox.core.registry import PluginRegistry
from nvox.core.store import ArtifactStore


@dataclass(frozen=True)
class Scenario:
    name: str
    payload: bytes


def _png(width: int, height: int, channels: int = 3) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    raw = b"".join(
        b"\x00" + bytes((x * 17 + y * 31) & 0xFF for x in range(width * channels))
        for y in range(height)
    )
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index in range(8):
            archive.writestr(
                f"member-{index}.txt",
                ("ctf{benchmark_%d} " % index).encode() + b"x" * 2048,
            )
    return buffer.getvalue()


def scenarios() -> list[Scenario]:
    return [
        Scenario("text-64k", (b"line of benchmark text ctf{bench}\n" * 2048)[:65536]),
        Scenario(
            "entropy-1m",
            bytes((index * 73 + (index >> 8) * 19) & 0xFF for index in range(1024 * 1024)),
        ),
        Scenario("png-256", _png(256, 256)),
        Scenario("zip-8", _zip()),
    ]


def run(*, repeat: int) -> list[dict[str, object]]:
    config = Config(
        plugin_dirs=(),
        limits=ScanLimits(max_depth=3, timeout=60.0, max_artifact_bytes=64 * 1024 * 1024, jobs=1),
    )
    registry = PluginRegistry().load_all(config)
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="nvox-bench-") as directory:
        root = Path(directory)
        for scenario in scenarios():
            path = root / f"{scenario.name}.bin"
            path.write_bytes(scenario.payload)
            samples: list[float] = []
            for _ in range(repeat + 1):  # first pass warms imports and caches
                engine = Engine(config, registry=registry, store=ArtifactStore())
                started = time.perf_counter()
                result = engine.analyze(path)
                samples.append(time.perf_counter() - started)
            measured = samples[1:]
            median = sorted(measured)[len(measured) // 2]
            results.append(
                {
                    "scenario": scenario.name,
                    "bytes": len(scenario.payload),
                    "median_seconds": round(median, 6),
                    "mib_per_second": round((len(scenario.payload) / (1024 * 1024)) / median, 2),
                    "artifacts": len(result.artifacts),
                    "plugin_runs": result.plugin_runs,
                    "skips": len(result.skips),
                }
            )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=3, help="measured repetitions per scenario")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    rows = run(repeat=args.repeat)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    print(f"{'scenario':<14} {'bytes':>9} {'median s':>10} {'MiB/s':>9} {'artifacts':>10}")
    for row in rows:
        print(
            f"{row['scenario']:<14} {row['bytes']:>9} {row['median_seconds']:>10.4f} "
            f"{row['mib_per_second']:>9.2f} {row['artifacts']:>10}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
