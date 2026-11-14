"""Stable plugin API surface.

Plugins import from this module so the import path stays fixed across
releases. The API version is bumped only for breaking changes.

The model is an artifact graph: a plugin receives one artifact, emits zero or
more derived artifacts through :class:`Context`, and the engine feeds those
artifacts back into the pipeline.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, NoReturn

API_VERSION = 1

# Media types treated as archive containers when inferring an artifact kind.
_ARCHIVE_MEDIA_TYPES = frozenset(
    {
        "application/zip",
        "application/gzip",
        "application/x-gzip",
        "application/x-bzip2",
        "application/x-xz",
        "application/x-tar",
        "application/x-7z-compressed",
        "application/vnd.rar",
        "application/x-rar-compressed",
    }
)
_TEXT_MEDIA_TYPES = frozenset({"application/json", "application/xml"})


class ArtifactKind(StrEnum):
    """Kind of payload carried by an artifact."""

    BYTES = "bytes"
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    ARCHIVE = "archive"
    META = "meta"


class PluginKind(StrEnum):
    """Role a plugin plays in the pipeline."""

    ANALYZER = "analyzer"
    EXTRACTOR = "extractor"
    DECODER = "decoder"


def _base_media_type(value: str) -> str:
    """Strip MIME parameters and normalise case."""
    return value.split(";", 1)[0].strip().lower()


def infer_kind(media_type: str) -> ArtifactKind:
    """Map a media type to an artifact kind."""
    lowered = _base_media_type(media_type)
    if lowered.startswith("image/"):
        return ArtifactKind.IMAGE
    if lowered.startswith("audio/"):
        return ArtifactKind.AUDIO
    if lowered.startswith("text/") or lowered in _TEXT_MEDIA_TYPES:
        return ArtifactKind.TEXT
    if lowered in _ARCHIVE_MEDIA_TYPES:
        return ArtifactKind.ARCHIVE
    return ArtifactKind.BYTES


@dataclass(frozen=True)
class Artifact:
    """An immutable, content-addressed payload produced by the pipeline."""

    sha256: str
    kind: ArtifactKind
    name: str
    payload: bytes
    media_type: str = "application/octet-stream"
    parents: tuple[str, ...] = ()
    producer: str = "evidence"
    params: Mapping[str, Any] = field(default_factory=dict)
    depth: int = 0

    @property
    def size(self) -> int:
        return len(self.payload)

    @property
    def text(self) -> str:
        return self.payload.decode("utf-8", errors="replace")

    def to_dict(self) -> dict[str, Any]:
        """Serialise metadata for indexes and reports (payload excluded)."""
        return {
            "sha256": self.sha256,
            "kind": self.kind.value,
            "name": self.name,
            "size": self.size,
            "media_type": self.media_type,
            "parents": list(self.parents),
            "producer": self.producer,
            "params": dict(self.params),
            "depth": self.depth,
        }


def make_artifact(
    *,
    kind: ArtifactKind,
    name: str,
    payload: bytes,
    media_type: str = "application/octet-stream",
    parents: Sequence[str] = (),
    producer: str = "evidence",
    params: Mapping[str, Any] | None = None,
    depth: int = 0,
) -> Artifact:
    """Create an artifact, computing its content hash."""
    return Artifact(
        sha256=hashlib.sha256(payload).hexdigest(),
        kind=kind,
        name=name,
        payload=payload,
        media_type=media_type,
        parents=tuple(parents),
        producer=producer,
        params=MappingProxyType(dict(params or {})),
        depth=depth,
    )


class PluginSkip(Exception):
    """Raised by ``Context.skip`` to stop a plugin with a recorded reason."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class PluginTimeout(Exception):
    """Raised when a plugin exceeds its deadline while emitting artifacts."""


class Context:
    """Per-invocation interface between a plugin and the engine.

    Plugins never write to disk or mutate the store directly: they emit
    artifacts and record skips through this object.
    """

    def __init__(
        self,
        artifact: Artifact,
        plugin_name: str,
        *,
        max_artifact_bytes: int,
        depth: int,
        options: Mapping[str, Any] | None = None,
        deadline: float | None = None,
    ) -> None:
        self.artifact = artifact
        self.plugin_name = plugin_name
        self.depth = depth
        self.options: Mapping[str, Any] = MappingProxyType(dict(options or {}))
        self._max_artifact_bytes = max_artifact_bytes
        self._deadline = deadline
        self._artifacts: list[Artifact] = []
        self._closed = False

    @property
    def artifacts(self) -> tuple[Artifact, ...]:
        """Artifacts emitted so far."""
        return tuple(self._artifacts)

    @property
    def closed(self) -> bool:
        return self._closed

    def option(self, name: str, default: Any = None) -> Any:
        """Return a resolved plugin option."""
        return self.options.get(name, default)

    def skip(self, reason: str) -> NoReturn:
        """Abort the plugin and record ``reason`` as a skip."""
        raise PluginSkip(reason)

    def text(self, name: str, value: str, **params: Any) -> Artifact:
        """Emit a UTF-8 text artifact."""
        return self._emit(
            ArtifactKind.TEXT,
            name,
            value.encode("utf-8"),
            "text/plain; charset=utf-8",
            params,
        )

    def blob(
        self,
        name: str,
        data: bytes,
        *,
        media_type: str = "application/octet-stream",
        **params: Any,
    ) -> Artifact:
        """Emit a generic bytes artifact."""
        return self._emit(ArtifactKind.BYTES, name, data, media_type, params)

    def image(
        self,
        name: str,
        data: bytes,
        *,
        media_type: str = "image/png",
        **params: Any,
    ) -> Artifact:
        """Emit an image artifact."""
        return self._emit(ArtifactKind.IMAGE, name, data, media_type, params)

    def audio(
        self,
        name: str,
        data: bytes,
        *,
        media_type: str = "audio/wav",
        **params: Any,
    ) -> Artifact:
        """Emit an audio artifact."""
        return self._emit(ArtifactKind.AUDIO, name, data, media_type, params)

    def archive(
        self,
        name: str,
        data: bytes,
        *,
        media_type: str = "application/zip",
        **params: Any,
    ) -> Artifact:
        """Emit an archive artifact."""
        return self._emit(ArtifactKind.ARCHIVE, name, data, media_type, params)

    def meta(self, name: str, value: Mapping[str, Any], **params: Any) -> Artifact:
        """Emit structured metadata as a JSON artifact."""
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
        return self._emit(
            ArtifactKind.META,
            name,
            encoded.encode("utf-8"),
            "application/json",
            params,
        )

    def adopt(self, artifact: Artifact) -> Artifact:
        """Normalise an artifact built manually by a plugin."""
        producer = artifact.producer
        if not producer or producer == "evidence":
            producer = self.plugin_name
        normalised = make_artifact(
            kind=artifact.kind,
            name=artifact.name,
            payload=artifact.payload,
            media_type=artifact.media_type,
            parents=artifact.parents or (self.artifact.sha256,),
            producer=producer,
            params=artifact.params,
            depth=self.depth,
        )
        self._append(normalised)
        return normalised

    def close(self) -> None:
        """Stop accepting emissions (called by the engine)."""
        self._closed = True

    def _emit(
        self,
        kind: ArtifactKind,
        name: str,
        payload: bytes,
        media_type: str,
        params: Mapping[str, Any],
    ) -> Artifact:
        self._check_deadline()
        if len(payload) > self._max_artifact_bytes:
            raise PluginSkip(
                f"artifact {name!r} exceeds max_artifact_bytes "
                f"({len(payload)} > {self._max_artifact_bytes})"
            )
        artifact = make_artifact(
            kind=kind,
            name=name,
            payload=payload,
            media_type=media_type,
            parents=(self.artifact.sha256,),
            producer=self.plugin_name,
            params=params,
            depth=self.depth,
        )
        self._append(artifact)
        return artifact

    def _append(self, artifact: Artifact) -> None:
        if self._closed:
            return
        self._check_deadline()
        self._artifacts.append(artifact)

    def _check_deadline(self) -> None:
        if self._deadline is not None and time.monotonic() > self._deadline:
            raise PluginTimeout(f"plugin {self.plugin_name} exceeded its timeout")


class Plugin:
    """Base class for all plugins.

    Subclasses set the manifest attributes and implement :meth:`run`. Emitting
    through ``ctx`` is the normal style; a generator that yields artifacts is
    also supported.
    """

    name: ClassVar[str] = ""
    version: ClassVar[str] = "0.0.0"
    api_version: ClassVar[int] = API_VERSION
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[Sequence[str]] = ("*/*",)
    priority: ClassVar[int] = 50
    requires: ClassVar[Sequence[str]] = ()
    description: ClassVar[str] = ""
    options: ClassVar[Mapping[str, Any]] = {}
    timeout: ClassVar[float | None] = None

    def run(self, artifact: Artifact, ctx: Context) -> Iterable[Any] | None:
        """Analyse ``artifact`` and emit derived artifacts through ``ctx``."""
        raise NotImplementedError

    def matches(self, media_type: str) -> bool:
        """Return whether this plugin accepts ``media_type``.

        Parameters such as ``charset`` are ignored, so ``text/plain`` matches
        ``text/plain; charset=utf-8``.
        """
        base = _base_media_type(media_type)
        return any(fnmatch.fnmatchcase(base, _base_media_type(pattern)) for pattern in self.targets)

    def accepts(self, artifact: Artifact) -> bool:
        """Return whether this plugin wants to analyse ``artifact``.

        The default is based on the media type alone; plugins can narrow it
        further (for example to avoid re-processing text).
        """
        return self.matches(artifact.media_type)

    # --- hooks -----------------------------------------------------------
    def pre_ingest(self, path: Path) -> str | None:
        """Return a reason string to refuse an input, or ``None`` to keep it."""
        return None

    def on_artifact(self, artifact: Artifact, ctx: Context) -> None:
        """Observe an artifact after it has been stored."""

    def post_scan(self, result: Any) -> None:
        """Observe the completed scan result."""

    def on_finding(self, finding: Any) -> Any:
        """Inspect or drop a finding (used by the M2 flag engine)."""
        return finding


class Analyzer(Plugin):
    """Plugin that derives artifacts from one artifact."""

    kind: ClassVar[PluginKind] = PluginKind.ANALYZER


class Extractor(Plugin):
    """Plugin that carves files out of an artifact."""

    kind: ClassVar[PluginKind] = PluginKind.EXTRACTOR


class Decoder(Plugin):
    """Plugin that transforms text/bytes payloads (flag-engine support)."""

    kind: ClassVar[PluginKind] = PluginKind.DECODER


_DECLARED: list[type[Plugin]] = []


def register(cls: type[Plugin]) -> type[Plugin]:
    """Class decorator that declares a plugin for discovery.

    Discovery also scans plugin modules for :class:`Plugin` subclasses, so the
    decorator is optional. It validates the manifest early and records the
    class for the ``declared`` source.
    """
    if not isinstance(cls, type) or not issubclass(cls, Plugin):
        raise TypeError(f"@register expects a Plugin subclass, got {cls!r}")
    if not cls.name:
        raise ValueError(f"{cls.__module__}.{cls.__qualname__} has no plugin name")
    if not isinstance(cls.api_version, int) or cls.api_version < 1:
        raise ValueError(f"{cls.name}: api_version must be a positive integer")
    if cls not in _DECLARED:
        _DECLARED.append(cls)
    return cls


def declared_plugins() -> tuple[type[Plugin], ...]:
    """Return plugin classes registered with the decorator."""
    return tuple(_DECLARED)


__all__ = [
    "API_VERSION",
    "Analyzer",
    "Artifact",
    "ArtifactKind",
    "Context",
    "Decoder",
    "Extractor",
    "Plugin",
    "PluginKind",
    "PluginSkip",
    "PluginTimeout",
    "declared_plugins",
    "infer_kind",
    "make_artifact",
    "register",
]
