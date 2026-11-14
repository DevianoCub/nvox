"""Tests for the artifact store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nvox.api import ArtifactKind, make_artifact
from nvox.core.store import ArtifactStore, ArtifactStoreError


def _artifact(name: str, payload: bytes, depth: int = 0) -> object:
    return make_artifact(
        kind=ArtifactKind.BYTES,
        name=name,
        payload=payload,
        depth=depth,
    )


def test_store_deduplicates_by_hash() -> None:
    store = ArtifactStore()
    first = store.put(_artifact("a", b"same"))
    second = store.put(_artifact("b", b"same"))
    assert first is second
    assert len(store) == 1
    assert store.get(first.sha256) is first


def test_store_orders_by_depth_and_name() -> None:
    store = ArtifactStore()
    store.put(_artifact("b", b"two", depth=1))
    store.put(_artifact("a", b"one", depth=1))
    store.put(_artifact("root", b"zero", depth=0))
    assert [artifact.name for artifact in store.all()] == ["root", "a", "b"]


def test_store_rejects_oversized_artifacts() -> None:
    store = ArtifactStore(max_bytes=2)
    with pytest.raises(ArtifactStoreError):
        store.put(_artifact("big", b"123"))


def test_store_persists_payloads_and_index(tmp_path: Path) -> None:
    root = tmp_path / "out"
    store = ArtifactStore(root=root)
    artifact = store.put(_artifact("sample", b"payload"))
    path = store.path_for(artifact.sha256)
    assert path is not None
    assert path.read_bytes() == b"payload"
    lines = (root / "index.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["sha256"] == artifact.sha256

    # Duplicates do not append a second index entry.
    store.put(_artifact("sample-again", b"payload"))
    lines = (root / "index.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_store_removes_stale_temporary_files(tmp_path: Path) -> None:
    root = tmp_path / "out"
    artifacts = root / "artifacts" / "ab"
    artifacts.mkdir(parents=True)
    stale = artifacts / "abandoned.tmp"
    stale.write_bytes(b"partial")
    real = artifacts / "real-artifact"
    real.write_bytes(b"evidence")

    store = ArtifactStore(root=root)
    assert not stale.exists()
    assert real.read_bytes() == b"evidence"
    assert store.cleanup() == 0


def test_store_cleanup_reports_removed_files(tmp_path: Path) -> None:
    store = ArtifactStore(root=tmp_path / "out")
    stale = store.root / "artifacts" / "cd" / "interrupted.tmp" if store.root else None
    assert stale is not None
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"partial")
    assert store.cleanup() == 1
    assert not stale.exists()


def test_store_by_kind_and_require() -> None:
    store = ArtifactStore()
    text = store.put(make_artifact(kind=ArtifactKind.TEXT, name="t", payload=b"hello"))
    assert store.by_kind(ArtifactKind.TEXT) == (text,)
    assert store.require(text.sha256) is text
    with pytest.raises(KeyError):
        store.require("missing")
