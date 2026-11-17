"""Shared safety helpers for command plugins and external bridges."""

from __future__ import annotations

import mimetypes
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MEDIA_SUFFIX = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/webp": ".webp",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "application/pdf": ".pdf",
    "application/zip": ".zip",
}
_SAFE_ENV_KEYS = (
    "PATH",
    "LANG",
    "LC_ALL",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
)


class OutputError(Exception):
    """Raised when an output policy or environment override is invalid."""


@dataclass(frozen=True)
class OutputPolicy:
    """Limits for files produced by an external command."""

    max_files: int = 1000
    max_file_bytes: int = 16 * 1024 * 1024
    max_total_bytes: int = 64 * 1024 * 1024

    def validate(self) -> None:
        if self.max_files < 1:
            raise OutputError("max_files must be greater than zero")
        if self.max_file_bytes < 1:
            raise OutputError("max_file_bytes must be greater than zero")
        if self.max_total_bytes < 1:
            raise OutputError("max_total_bytes must be greater than zero")


@dataclass(frozen=True)
class CollectedFile:
    """A regular file found below a command output directory."""

    relative: str
    data: bytes
    media_type: str


@dataclass(frozen=True)
class OutputCollection:
    """Files retained after applying an :class:`OutputPolicy`."""

    files: tuple[CollectedFile, ...]
    notes: tuple[str, ...] = ()
    truncated: bool = False

    def __bool__(self) -> bool:
        return bool(self.files)


def safe_input_name(name: str, media_type: str = "") -> str:
    """Return a short file name with a useful extension for a tool."""
    cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in name)
    cleaned = cleaned[:120] or "artifact.bin"
    if cleaned in {".", ".."}:
        cleaned = "artifact.bin"
    if not Path(cleaned).suffix:
        suffix = _MEDIA_SUFFIX.get(media_type.split(";", 1)[0].strip().lower(), "")
        cleaned += suffix
    return cleaned


def validate_env(env: Mapping[str, object]) -> dict[str, str]:
    """Validate a user-supplied environment override table."""
    result: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(key, str) or _ENV_NAME.fullmatch(key) is None:
            raise OutputError(f"invalid environment variable name {key!r}")
        if not isinstance(value, str):
            raise OutputError(f"environment variable {key!r} must be a string")
        if "\x00" in value:
            raise OutputError(f"environment variable {key!r} contains a NUL byte")
        result[key] = value
    return result


def build_child_environment(
    workspace: Path,
    *,
    extra: Mapping[str, str] | None = None,
    inherit: bool = False,
) -> dict[str, str]:
    """Build a small, predictable environment for an external command.

    By default the child gets the platform path, locale and process settings
    but not the rest of the caller's environment. ``HOME`` and temp directories
    point into the command workspace so a tool cannot write into the user's
    profile through the usual environment variables. Plugins that genuinely
    need a variable can opt into it with ``extra`` or ``inherit``.
    """
    if inherit:
        environment = dict(os.environ)
    else:
        environment = {key: os.environ[key] for key in _SAFE_ENV_KEYS if key in os.environ}
    environment.setdefault("PATH", os.defpath)
    environment.setdefault("LANG", "C.UTF-8")
    environment["HOME"] = str(workspace)
    environment["TMPDIR"] = str(workspace)
    environment["TMP"] = str(workspace)
    environment["TEMP"] = str(workspace)
    environment["XDG_CONFIG_HOME"] = str(workspace / "config")
    environment["XDG_CACHE_HOME"] = str(workspace / "cache")
    if extra:
        environment.update(validate_env(extra))
    return environment


def collect_output_files(directory: Path, policy: OutputPolicy | None = None) -> OutputCollection:
    """Collect regular files below ``directory`` without following symlinks.

    Files that exceed the per-file or total byte cap are skipped and described
    in ``notes``. The caller can decide whether a note is a normal condition or
    worth recording as a plugin skip.
    """
    active = policy or OutputPolicy()
    active.validate()
    root = directory.resolve()
    if not root.is_dir():
        return OutputCollection(files=())

    notes: list[str] = []
    candidates: list[Path] = []
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        # os.walk does not follow directory links when followlinks=False, but
        # pruning them explicitly avoids surprising behaviour on older Pythons.
        directory_names[:] = sorted(
            name for name in directory_names if not (current_path / name).is_symlink()
        )
        for file_name in sorted(file_names):
            path = current_path / file_name
            if path.is_symlink():
                notes.append(f"skipped symlink {path.relative_to(root).as_posix()}")
                continue
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                notes.append(f"skipped unreadable path {path.relative_to(root).as_posix()}")
                continue
            if not resolved.is_relative_to(root):
                notes.append(f"skipped path outside output directory {path.name}")
                continue
            if not resolved.is_file():
                continue
            candidates.append(resolved)

    candidates.sort(key=lambda path: path.relative_to(root).as_posix())
    if len(candidates) > active.max_files:
        notes.append(f"retained {active.max_files} of {len(candidates)} files")
        candidates = candidates[: active.max_files]
        truncated = True
    else:
        truncated = False

    collected: list[CollectedFile] = []
    total = 0
    for path in candidates:
        relative = path.relative_to(root).as_posix()
        try:
            size = path.stat().st_size
        except OSError:
            notes.append(f"skipped unreadable file {relative}")
            continue
        if size > active.max_file_bytes:
            notes.append(f"skipped oversized file {relative} ({size} bytes)")
            truncated = True
            continue
        if total + size > active.max_total_bytes:
            notes.append(f"skipped file {relative} after total output cap")
            truncated = True
            continue
        try:
            data = path.read_bytes()
        except OSError:
            notes.append(f"skipped unreadable file {relative}")
            continue
        if len(data) > active.max_file_bytes:
            notes.append(f"skipped oversized file {relative} after read")
            truncated = True
            continue
        total += len(data)
        media_type, _ = mimetypes.guess_type(path.name)
        collected.append(
            CollectedFile(
                relative=relative,
                data=data,
                media_type=media_type or "application/octet-stream",
            )
        )

    return OutputCollection(files=tuple(collected), notes=tuple(notes), truncated=truncated)


__all__ = [
    "CollectedFile",
    "OutputCollection",
    "OutputError",
    "OutputPolicy",
    "build_child_environment",
    "collect_output_files",
    "safe_input_name",
    "validate_env",
]
