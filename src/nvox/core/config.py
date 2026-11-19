"""Configuration discovery and loading for Nvox.

Configuration is read from a single ``nvox.toml`` file. The search order is:

1. an explicit path passed on the command line,
2. ``./nvox.toml`` in the current working directory,
3. ``$XDG_CONFIG_HOME/nvox/nvox.toml`` (``~/.config/nvox/nvox.toml`` by default).

Missing files are not an error: Nvox falls back to built-in defaults.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

IsolationMode = Literal["thread", "process"]

APP_NAME = "nvox"
CONFIG_FILENAME = "nvox.toml"
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_DEPTH = 4


class ConfigError(Exception):
    """Raised when a configuration file exists but cannot be used."""


def user_config_dir() -> Path:
    """Return the per-user configuration directory."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / APP_NAME


def default_plugin_dirs(cwd: Path | None = None) -> tuple[Path, ...]:
    """Return plugin directories used when the config does not override them."""
    root = cwd if cwd is not None else Path.cwd()
    return (root / "plugins", user_config_dir() / "plugins")


def config_candidates(cwd: Path | None = None) -> tuple[Path, ...]:
    """Return the configuration paths that are searched, in priority order."""
    root = cwd if cwd is not None else Path.cwd()
    return (root / CONFIG_FILENAME, user_config_dir() / CONFIG_FILENAME)


def find_config(cwd: Path | None = None) -> Path | None:
    """Return the first existing configuration file, if any."""
    for candidate in config_candidates(cwd):
        if candidate.is_file():
            return candidate
    return None


@dataclass(frozen=True)
class ScanLimits:
    """Resource limits applied to a scan run."""

    max_depth: int = DEFAULT_MAX_DEPTH
    timeout: float = DEFAULT_TIMEOUT
    max_artifact_bytes: int = MAX_ARTIFACT_BYTES
    jobs: int = 0  # 0 means "auto" (one worker per CPU).
    isolation: IsolationMode = "thread"

    def effective_jobs(self) -> int:
        if self.jobs > 0:
            return self.jobs
        return os.cpu_count() or 1


@dataclass(frozen=True)
class Config:
    """Resolved Nvox configuration."""

    path: Path | None = None
    flag_formats: tuple[str, ...] = ()
    plugin_dirs: tuple[Path, ...] = ()
    limits: ScanLimits = field(default_factory=ScanLimits)
    disabled_tools: tuple[str, ...] = ()
    disabled_plugins: tuple[str, ...] = ()
    plugin_options: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    @classmethod
    def default(cls, cwd: Path | None = None) -> Config:
        return cls(path=None, plugin_dirs=default_plugin_dirs(cwd))

    @classmethod
    def load(cls, cwd: Path | None = None, explicit: Path | None = None) -> Config:
        """Load configuration, returning defaults when no file is present.

        Raises:
            ConfigError: if the file exists but is unreadable or malformed.
        """
        root = cwd if cwd is not None else Path.cwd()
        path: Path | None
        if explicit is not None:
            path = explicit.expanduser()
            if not path.is_file():
                raise ConfigError(f"configuration file not found: {explicit}")
        else:
            path = find_config(root)
            if path is None:
                return cls.default(root)
        path = path.resolve()
        try:
            with path.open("rb") as handle:
                raw = tomllib.load(handle)
        except OSError as exc:
            raise ConfigError(f"cannot read {path}: {exc}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
        return cls._from_mapping(raw, path=path)

    @classmethod
    def _from_mapping(cls, raw: dict[str, Any], *, path: Path) -> Config:
        flags = _table(raw, "flags", path)
        plugins = _table(raw, "plugins", path)
        scan = _table(raw, "scan", path)
        tools = _table(raw, "tools", path)

        formats = _string_list(flags.get("formats", []), "flags.formats", path)
        plugin_dirs_raw = _string_list(plugins.get("dirs", []), "plugins.dirs", path)
        dirs = (
            tuple(_expand(path_value, path.parent) for path_value in plugin_dirs_raw)
            if plugin_dirs_raw
            else default_plugin_dirs(path.parent)
        )
        disabled = _string_list(tools.get("disable", []), "tools.disable", path)
        disabled_plugins = _string_list(plugins.get("disable", []), "plugins.disable", path)
        plugin_options = _plugin_options(plugins.get("options", {}), path)

        limits = ScanLimits(
            max_depth=_positive_int(
                scan.get("max_depth", DEFAULT_MAX_DEPTH), "scan.max_depth", path
            ),
            timeout=_positive_float(scan.get("timeout", DEFAULT_TIMEOUT), "scan.timeout", path),
            max_artifact_bytes=_positive_int(
                scan.get("max_artifact_bytes", MAX_ARTIFACT_BYTES),
                "scan.max_artifact_bytes",
                path,
            ),
            jobs=_non_negative_int(scan.get("jobs", 0), "scan.jobs", path),
            isolation=_isolation(scan.get("isolation", "thread"), "scan.isolation", path),
        )
        return cls(
            path=path,
            flag_formats=tuple(formats),
            plugin_dirs=dirs,
            limits=limits,
            disabled_tools=tuple(disabled),
            disabled_plugins=tuple(disabled_plugins),
            plugin_options=plugin_options,
        )


def _table(raw: dict[str, Any], key: str, path: Path) -> dict[str, Any]:
    value = raw.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: [{key}] must be a table")
    return value


def _expand(value: str, cwd: Path) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    return candidate


def _string_list(value: Any, key: str, path: Path) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{path}: {key} must be a list of strings")
    return value


def _positive_int(value: Any, key: str, path: Path) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{path}: {key} must be a positive integer")
    return value


def _non_negative_int(value: Any, key: str, path: Path) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ConfigError(f"{path}: {key} must be a non-negative integer")
    return value


def _positive_float(value: Any, key: str, path: Path) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{path}: {key} must be a positive number")
    return float(value)


def _isolation(value: Any, key: str, path: Path) -> IsolationMode:
    if value not in ("thread", "process"):
        raise ConfigError(f"{path}: {key} must be 'thread' or 'process'")
    return cast(IsolationMode, value)


def _plugin_options(value: Any, path: Path) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: plugins.options must be a table")
    options: dict[str, dict[str, Any]] = {}
    for name, table in value.items():
        if not isinstance(name, str) or not isinstance(table, dict):
            raise ConfigError(f"{path}: plugins.options.{name} must be a table")
        options[name] = dict(table)
    return options
