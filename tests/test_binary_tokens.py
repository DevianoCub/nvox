"""Tests for binary token extraction and decoder hand-off."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from nvox.api import ArtifactKind, Context, PluginSkip, infer_kind, make_artifact
from nvox.core.config import Config, ScanLimits
from nvox.core.engine import Engine
from nvox.core.flags import FlagScanner, build_flag_specs
from nvox.core.registry import PluginRegistry
from nvox.core.store import ArtifactStore
from nvox.plugins.decoders.text import Base64Decoder
from nvox.plugins.generic.binary_tokens import BinaryTokenPlugin


def _run(
    payload: bytes,
    *,
    options: dict[str, object] | None = None,
) -> tuple[object, ...]:
    plugin = BinaryTokenPlugin()
    artifact = make_artifact(
        kind=infer_kind("application/octet-stream"),
        name="sample.bin",
        payload=payload,
        media_type="application/octet-stream",
    )
    merged = dict(plugin.options)
    merged.update(options or {})
    ctx = Context(
        artifact,
        plugin.name,
        max_artifact_bytes=64 * 1024 * 1024,
        depth=0,
        options=merged,
    )
    plugin.run(artifact, ctx)
    return ctx.artifacts


def test_binary_tokens_split_base64_and_braced_text() -> None:
    encoded = base64.b64encode(b"ctf{token_ok}")
    payload = b"\x00\xff noise " + encoded + b" \x00\xffctf{direct}\x00"
    artifacts = _run(payload)
    values = {artifact.text for artifact in artifacts}  # type: ignore[attr-defined]
    assert encoded.decode() in values
    assert "ctf{direct}" in values
    assert all(artifact.name.startswith("binary/token/") for artifact in artifacts)  # type: ignore[attr-defined]


def test_binary_tokens_ignore_text_and_images() -> None:
    plugin = BinaryTokenPlugin()
    text = make_artifact(kind=ArtifactKind.TEXT, name="text", payload=b"ctf{text}")
    image = make_artifact(
        kind=ArtifactKind.IMAGE,
        name="image.png",
        payload=b"png",
        media_type="image/png",
    )
    binary = make_artifact(
        kind=ArtifactKind.BYTES,
        name="sample.bin",
        payload=b"data",
        media_type="application/octet-stream",
    )
    assert not plugin.accepts(text)
    assert not plugin.accepts(image)
    assert plugin.accepts(binary)


def test_binary_tokens_budgets_and_empty_input() -> None:
    with pytest.raises(PluginSkip, match="empty"):
        _run(b"")

    with pytest.raises(PluginSkip, match="max_input_bytes"):
        _run(b"A" * 16, options={"max_input_bytes": 8})

    with pytest.raises(PluginSkip, match="no encoded tokens"):
        _run(b"\x00\x01\x02\xff" * 8)

    artifacts = _run(
        b"\x00YWFhYWFhYWE= YWFhYWFhYmM= YWFhYWFhYmQ=\x00",
        options={"max_tokens": 2},
    )
    assert len(artifacts) == 2


def test_binary_tokens_feed_the_decoder_chain(tmp_path: Path) -> None:
    encoded = base64.b64encode(b"ctf{binary_token_ok}")
    path = tmp_path / "challenge.bin"
    path.write_bytes(b"\x00\xff unrelated " + encoded + b" \x00\xff trailing")

    config = Config(
        plugin_dirs=(),
        limits=ScanLimits(max_depth=3, jobs=1, timeout=30.0),
    )
    registry = PluginRegistry()
    registry.register(BinaryTokenPlugin, origin="test")
    registry.register(Base64Decoder, origin="test")
    engine = Engine(
        config,
        registry=registry,
        store=ArtifactStore(),
        jobs=1,
        flag_scanner=FlagScanner(build_flag_specs(["ctf{}"])),
    )
    result = engine.analyze(path)
    assert [finding.value for finding in result.findings] == ["binary_token_ok"]
    assert [step.plugin for step in result.findings[0].chain] == [
        "generic.binary_tokens",
        "decoder.base64",
    ]
