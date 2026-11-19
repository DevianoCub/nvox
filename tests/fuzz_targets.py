"""Deterministic malformed-input corpus for parser hardening.

The test suite runs a short pass; ``scripts/fuzz-parsers.py`` runs the same
targets for longer.  A failure means an input escaped the engine's plugin
error boundary rather than becoming a recorded skip.
"""

from __future__ import annotations

import random
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fixture_builders import (
    make_bmp,
    make_docx,
    make_dtmf_wav,
    make_gif,
    make_gzip,
    make_jpeg_with_metadata,
    make_pdf,
    make_png_text_chunks,
    make_tar,
    make_wav,
    make_zip_entries,
)
from nvox.core.config import Config, ScanLimits
from nvox.core.engine import Engine
from nvox.core.registry import PluginRegistry
from nvox.core.store import ArtifactStore


@dataclass(frozen=True)
class FuzzCase:
    name: str
    payload: bytes


def corpus() -> list[FuzzCase]:
    """Return seed inputs covering the binary parsers."""
    return [
        FuzzCase("png", make_png_text_chunks(text="ctf{fuzz}", compressed="x" * 32)),
        FuzzCase("jpeg", make_jpeg_with_metadata(comment="ctf{fuzz}")),
        FuzzCase("gif", make_gif(comment="ctf{fuzz}", application="NETSCAPE2.0")),
        FuzzCase("bmp", make_bmp()),
        FuzzCase("wav", make_wav([((index * 37) % 2000) - 1000 for index in range(2048)])),
        FuzzCase("dtmf", make_dtmf_wav("123456")),
        FuzzCase("zip", make_zip_entries([("flag.txt", b"ctf{fuzz}"), ("x", b"\x00" * 64)])),
        FuzzCase("tar", make_tar([("flag.txt", b"ctf{fuzz}")])),
        FuzzCase("gzip", make_gzip(b"ctf{fuzz}" * 32)),
        FuzzCase("pdf", make_pdf(text="ctf{fuzz}", compress=True)),
        FuzzCase("docx", make_docx(title="ctf{fuzz}")),
        FuzzCase("empty", b""),
        FuzzCase("header-only-png", b"\x89PNG\r\n\x1a\n"),
        FuzzCase("truncated-zip", b"PK\x03\x04\x14\x00\x00\x00"),
        FuzzCase("truncated-pdf", b"%PDF-1.7\n1 0 obj\n<<"),
    ]


def mutate(data: bytes, rng: random.Random) -> bytes:
    """Apply one to four deterministic structural mutations."""
    output = bytearray(data)
    for _ in range(rng.randint(1, 4)):
        operation = rng.randrange(6)
        if operation == 0 and output:
            output[rng.randrange(len(output))] ^= 1 << rng.randrange(8)
        elif operation == 1 and output:
            del output[rng.randrange(len(output)) :]
        elif operation == 2 and len(output) > 2:
            start = rng.randrange(len(output))
            del output[start : start + rng.randint(1, 16)]
        elif operation == 3:
            position = rng.randrange(len(output) + 1)
            output[position:position] = bytes(rng.randrange(256) for _ in range(rng.randint(1, 16)))
        elif operation == 4 and output:
            start = rng.randrange(len(output))
            output[start : start + rng.randint(1, 32)] = b"\x00" * min(len(output) - start, 32)
        elif operation == 5:
            output.extend(bytes(rng.randrange(256) for _ in range(rng.randint(1, 64))))
    return bytes(output)


def run_corpus(*, iterations: int = 2, seed: int = 0xC7F) -> int:
    """Run seeds and mutants through the plugin graph, returning case count."""
    config = Config(
        plugin_dirs=(),
        limits=ScanLimits(max_depth=3, timeout=10.0, max_artifact_bytes=4 * 1024 * 1024, jobs=1),
    )
    registry = PluginRegistry().load_all(config)
    ran = 0
    rng = random.Random(seed)
    with tempfile.TemporaryDirectory(prefix="nvox-fuzz-") as directory:
        root = Path(directory)
        engine = Engine(config, registry=registry, store=ArtifactStore(max_bytes=4 * 1024 * 1024))
        for case in corpus():
            inputs = [case.payload]
            inputs.extend(mutate(case.payload, rng) for _ in range(iterations))
            for index, payload in enumerate(inputs):
                path = root / f"{case.name}-{index}.bin"
                path.write_bytes(payload)
                try:
                    result = engine.analyze(path)
                except BaseException as exc:  # pragma: no cover - failure reporting
                    raise AssertionError(
                        f"fuzz target {case.name}[{index}] crashed: {type(exc).__name__}: {exc}"
                    ) from exc
                for skip in result.skips:
                    if skip.kind == "error" and not skip.reason:
                        raise AssertionError(
                            f"fuzz target {case.name}[{index}] produced an empty error skip"
                        )
                ran += 1
    return ran
