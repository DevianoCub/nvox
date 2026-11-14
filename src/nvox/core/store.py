"""Content-addressed artifact storage with optional disk persistence."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from nvox.api import Artifact, ArtifactKind


class ArtifactStoreError(Exception):
    """Raised when an artifact cannot be stored."""


class ArtifactStore:
    """In-memory artifact index, optionally mirrored to disk.

    Artifacts are keyed by SHA-256, so the same payload produced by different
    plugins is stored once. When ``root`` is set, payloads live under
    ``root/artifacts/<first two hex chars>/<sha256>`` and metadata is appended
    to ``root/index.jsonl``.
    """

    def __init__(self, *, root: Path | None = None, max_bytes: int = 64 * 1024 * 1024) -> None:
        self.root = root
        self.max_bytes = max_bytes
        self._items: dict[str, Artifact] = {}
        self._lock = threading.RLock()
        if root is not None:
            try:
                (root / "artifacts").mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ArtifactStoreError(f"cannot create store at {root}: {exc}") from exc
            self.cleanup()

    def cleanup(self) -> int:
        """Remove stale temporary files left by an interrupted write.

        Stored artifacts are evidence and are never deleted automatically.
        Returns the number of removed files.
        """
        if self.root is None:
            return 0
        removed = 0
        for temporary in (self.root / "artifacts").rglob("*.tmp"):
            try:
                temporary.unlink()
            except OSError:
                continue
            removed += 1
        return removed

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def __contains__(self, sha256: object) -> bool:
        with self._lock:
            return sha256 in self._items

    def put(self, artifact: Artifact) -> Artifact:
        """Store ``artifact`` and return the stored instance.

        Duplicate content is deduplicated: the first artifact with a given
        SHA-256 wins and is returned for later producers.
        """
        stored, _ = self.add(artifact)
        return stored

    def add(self, artifact: Artifact) -> tuple[Artifact, bool]:
        """Store ``artifact`` and report whether it was new."""
        if artifact.size > self.max_bytes:
            raise ArtifactStoreError(
                f"artifact {artifact.name!r} is {artifact.size} bytes, limit is {self.max_bytes}"
            )
        with self._lock:
            existing = self._items.get(artifact.sha256)
            if existing is not None:
                return existing, False
            self._items[artifact.sha256] = artifact
            if self.root is not None:
                self._write(artifact)
            return artifact, True

    def get(self, sha256: str) -> Artifact | None:
        with self._lock:
            return self._items.get(sha256)

    def require(self, sha256: str) -> Artifact:
        artifact = self.get(sha256)
        if artifact is None:
            raise KeyError(f"artifact not found: {sha256}")
        return artifact

    def all(self) -> tuple[Artifact, ...]:
        """Return artifacts ordered by depth, then name."""
        with self._lock:
            items = list(self._items.values())
        return tuple(sorted(items, key=lambda item: (item.depth, item.name, item.sha256)))

    def by_kind(self, kind: ArtifactKind) -> tuple[Artifact, ...]:
        return tuple(artifact for artifact in self.all() if artifact.kind is kind)

    def path_for(self, sha256: str) -> Path | None:
        """Return the on-disk path of an artifact, if the store is persistent."""
        if self.root is None:
            return None
        return self.root / "artifacts" / sha256[:2] / sha256

    def _write(self, artifact: Artifact) -> None:
        assert self.root is not None
        target = self.path_for(artifact.sha256)
        assert target is not None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                temporary = target.with_suffix(".tmp")
                try:
                    temporary.write_bytes(artifact.payload)
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
            index = self.root / "index.jsonl"
            with index.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(artifact.to_dict(), sort_keys=True) + "\n")
        except OSError as exc:
            raise ArtifactStoreError(f"cannot write artifact {artifact.sha256}: {exc}") from exc


__all__ = ["ArtifactStore", "ArtifactStoreError"]
