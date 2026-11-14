"""Tests for the stable plugin API."""

from __future__ import annotations

import hashlib
import time

import pytest

from nvox.api import (
    Analyzer,
    Artifact,
    ArtifactKind,
    Context,
    PluginKind,
    PluginSkip,
    PluginTimeout,
    infer_kind,
    make_artifact,
    register,
)


def _artifact(payload: bytes = b"payload") -> Artifact:
    return make_artifact(kind=ArtifactKind.BYTES, name="input.bin", payload=payload)


def test_make_artifact_hashes_payload() -> None:
    artifact = _artifact(b"abc")
    assert artifact.sha256 == hashlib.sha256(b"abc").hexdigest()
    assert artifact.size == 3
    assert artifact.depth == 0
    assert artifact.producer == "evidence"


def test_to_dict_excludes_payload() -> None:
    artifact = _artifact(b"abc")
    data = artifact.to_dict()
    assert "payload" not in data
    assert data["sha256"] == artifact.sha256
    assert data["kind"] == "bytes"


@pytest.mark.parametrize(
    ("media_type", "expected"),
    [
        ("image/png", ArtifactKind.IMAGE),
        ("audio/wav", ArtifactKind.AUDIO),
        ("text/plain; charset=utf-8", ArtifactKind.TEXT),
        ("application/json", ArtifactKind.TEXT),
        ("application/zip", ArtifactKind.ARCHIVE),
        ("application/octet-stream", ArtifactKind.BYTES),
    ],
)
def test_infer_kind(media_type: str, expected: ArtifactKind) -> None:
    assert infer_kind(media_type) is expected


def test_context_emits_children_with_provenance() -> None:
    parent = _artifact()
    ctx = Context(parent, "test.plugin", max_artifact_bytes=1024, depth=1)
    text = ctx.text("note", "hello", channel=1)
    blob = ctx.blob("raw", b"\x00\x01")
    ctx.meta("meta", {"a": 1})
    assert ctx.artifacts == (text, blob, ctx.artifacts[2])
    assert text.parents == (parent.sha256,)
    assert text.producer == "test.plugin"
    assert text.depth == 1
    assert text.params["channel"] == 1
    assert ctx.artifacts[2].kind is ArtifactKind.META
    assert ctx.artifacts[2].text == '{"a": 1}'


def test_context_adopt_normalises_manual_artifact() -> None:
    parent = _artifact()
    ctx = Context(parent, "test.plugin", max_artifact_bytes=1024, depth=2)
    child = make_artifact(kind=ArtifactKind.TEXT, name="manual", payload=b"hi")
    adopted = ctx.adopt(child)
    assert adopted.parents == (parent.sha256,)
    assert adopted.producer == "test.plugin"
    assert adopted.depth == 2
    assert adopted.sha256 == hashlib.sha256(b"hi").hexdigest()


def test_context_skip_raises() -> None:
    ctx = Context(_artifact(), "test.plugin", max_artifact_bytes=1024, depth=1)
    with pytest.raises(PluginSkip, match="nothing here"):
        ctx.skip("nothing here")


def test_context_size_limit() -> None:
    ctx = Context(_artifact(), "test.plugin", max_artifact_bytes=4, depth=1)
    with pytest.raises(PluginSkip, match="exceeds max_artifact_bytes"):
        ctx.text("too-big", "12345")


def test_context_deadline() -> None:
    ctx = Context(
        _artifact(),
        "test.plugin",
        max_artifact_bytes=1024,
        depth=1,
        deadline=time.monotonic() - 1,
    )
    with pytest.raises(PluginTimeout):
        ctx.text("late", "hello")


def test_closed_context_ignores_late_emissions() -> None:
    ctx = Context(_artifact(), "test.plugin", max_artifact_bytes=1024, depth=1)
    text = ctx.text("first", "one")
    ctx.close()
    ctx.text("second", "two")
    assert ctx.artifacts == (text,)


def test_plugin_matching_and_kind() -> None:
    class ImagePlugin(Analyzer):
        name = "test.image"
        targets = ("image/*", "application/pdf")

    plugin = ImagePlugin()
    assert plugin.kind is PluginKind.ANALYZER
    assert plugin.matches("image/png")
    assert plugin.matches("application/pdf")
    assert not plugin.matches("text/plain")
    assert plugin.accepts(
        make_artifact(kind=ArtifactKind.IMAGE, name="x", payload=b"", media_type="image/png")
    )


def test_register_requires_a_name() -> None:
    class Nameless(Analyzer):
        pass

    with pytest.raises(ValueError, match="no plugin name"):
        register(Nameless)


def test_register_returns_the_class() -> None:
    class Named(Analyzer):
        name = "test.registered"

    assert register(Named) is Named
