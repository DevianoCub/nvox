"""End-to-end M2 flag scanning through the engine and builtin plugins."""

from __future__ import annotations

import base64
import zlib
from dataclasses import replace
from pathlib import Path

from fixture_builders import (
    lsb_payload,
    make_jpeg_with_exif,
    png_with_lsb_text,
    xor_bytes,
)
from nvox.api import Plugin
from nvox.core.config import Config
from nvox.core.engine import Engine
from nvox.core.flags import FlagScanner, build_flag_specs
from nvox.core.registry import PluginRegistry
from nvox.core.store import ArtifactStore


def _engine(
    tmp_path: Path,
    *,
    formats: list[str] | None = None,
    patterns: list[str] | None = None,
) -> Engine:
    config = Config.default(cwd=tmp_path)
    config = replace(config, limits=replace(config.limits, jobs=1))
    specs = build_flag_specs(formats or ["ctf{}"], patterns or [])
    registry = PluginRegistry()
    registry.load_builtin()
    return Engine(
        config,
        registry=registry,
        store=ArtifactStore(),
        jobs=1,
        flag_scanner=FlagScanner(specs),
    )


def _write(tmp_path: Path, name: str, payload: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def test_plaintext_in_trailing_data(tmp_path: Path) -> None:
    payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16 + b"trailing ctf{trailing_data}"
    engine = _engine(tmp_path)
    result = engine.analyze(_write(tmp_path, "image.png", payload))
    assert [finding.value for finding in result.findings] == ["trailing_data"]
    assert result.findings[0].chain == ()
    assert result.findings[0].evidence_path.endswith("image.png")
    assert result.to_dict()["findings"][0]["value"] == "trailing_data"


def test_base64_text_chain(tmp_path: Path) -> None:
    payload = base64.b64encode(b"ctf{base64_chain}")
    engine = _engine(tmp_path)
    result = engine.analyze(_write(tmp_path, "note.txt", payload))
    finding = result.findings[0]
    assert finding.value == "base64_chain"
    assert [step.plugin for step in finding.chain] == ["decoder.base64"]
    assert finding.surface == "text"


def test_nested_base64_zlib_chain(tmp_path: Path) -> None:
    payload = base64.b64encode(zlib.compress(b"ctf{nested_chain}"))
    engine = _engine(tmp_path)
    result = engine.analyze(_write(tmp_path, "blob.bin", payload))
    finding = result.findings[0]
    assert finding.value == "nested_chain"
    assert [step.plugin for step in finding.chain] == ["decoder.base64", "decoder.zlib"]


def test_utf16_surface(tmp_path: Path) -> None:
    payload = b"\xff\xfe" + "ctf{utf16_chain}".encode("utf-16-le")
    engine = _engine(tmp_path)
    result = engine.analyze(_write(tmp_path, "utf16.txt", payload))
    assert [finding.value for finding in result.findings] == ["utf16_chain"]


def test_exif_base64_chain(tmp_path: Path) -> None:
    hidden = base64.b64encode(b"ctf{exif_chain}").decode()
    payload = make_jpeg_with_exif(image_description=hidden)
    engine = _engine(tmp_path)
    result = engine.analyze(_write(tmp_path, "photo.jpg", payload))
    finding = result.findings[0]
    assert finding.value == "exif_chain"
    assert [step.plugin for step in finding.chain] == ["image.exif", "decoder.base64"]


def test_png_lsb_base64_chain(tmp_path: Path) -> None:
    hidden = lsb_payload("ctf{lsb_chain}")
    payload = png_with_lsb_text(hidden, channel=1)
    engine = _engine(tmp_path)
    result = engine.analyze(_write(tmp_path, "pixels.png", payload))
    finding = result.findings[0]
    assert finding.value == "lsb_chain"
    assert [step.plugin for step in finding.chain] == ["image.lsb", "decoder.base64"]


def test_xor_payload_chain(tmp_path: Path) -> None:
    payload = xor_bytes(b"before ctf{xor_chain} after", 0x42)
    engine = _engine(tmp_path)
    result = engine.analyze(_write(tmp_path, "xor.bin", payload))
    finding = result.findings[0]
    assert finding.value == "xor_chain"
    assert finding.surface == "xor"
    assert [step.plugin for step in finding.chain] == ["decoder.xor"]
    assert finding.chain[0].params["key"] == 0x42


def test_raw_regex_and_no_false_positive(tmp_path: Path) -> None:
    engine = _engine(tmp_path, patterns=[r"FLAG\{[A-Za-z0-9_]+\}"])
    result = engine.analyze(_write(tmp_path, "raw.txt", b"nothing here"))
    assert result.findings == ()
    result = engine.analyze(_write(tmp_path, "raw2.txt", b"FLAG{raw_regex_ok}"))
    assert [finding.value for finding in result.findings] == ["FLAG{raw_regex_ok}"]


def test_engine_reuse_isolates_findings(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    first = engine.analyze(_write(tmp_path, "one.txt", b"ctf{first_file}"))
    second = engine.analyze(_write(tmp_path, "two.txt", b"ctf{second_file}"))
    assert [finding.value for finding in first.findings] == ["first_file"]
    assert [finding.value for finding in second.findings] == ["second_file"]


def test_on_finding_hook_can_drop(tmp_path: Path) -> None:
    class DropFindings(Plugin):
        name = "test.drop_findings"

        def on_finding(self, finding: object) -> None:
            return None

    config = Config.default(cwd=tmp_path)
    config = replace(config, limits=replace(config.limits, jobs=1))
    registry = PluginRegistry()
    registry.load_builtin()
    registry.register(DropFindings(), origin="test")
    engine = Engine(
        config,
        registry=registry,
        store=ArtifactStore(),
        jobs=1,
        flag_scanner=FlagScanner(build_flag_specs(["ctf{}"])),
    )
    result = engine.analyze(_write(tmp_path, "drop.txt", b"ctf{dropped}"))
    assert result.findings == ()
