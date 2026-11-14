"""Plugin discovery and registration.

Sources, in load order (later sources override earlier ones by name):

1. built-in plugins shipped in ``nvox.plugins``;
2. plugin directories from the configuration;
3. installed distributions exposing the ``nvox.plugins`` entry point;
4. classes declared with ``@nvox.api.register``.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import pkgutil
import re
import shutil
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from types import ModuleType
from typing import Any

from nvox.api import API_VERSION, Artifact, Plugin, declared_plugins
from nvox.core.command_plugin import CommandPluginError, parse_command_plugin
from nvox.core.config import Config

ENTRY_POINT_GROUP = "nvox.plugins"
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_BASE_CLASS_NAMES = frozenset({"Analyzer", "Extractor", "Decoder", "CommandPlugin"})


class PluginLoadError(Exception):
    """Raised when a plugin cannot be registered."""


@dataclass(frozen=True)
class RegisteredPlugin:
    """A plugin instance together with its provenance."""

    plugin: Plugin
    origin: str
    source: str

    @property
    def name(self) -> str:
        return self.plugin.name


class PluginRegistry:
    """Holds plugin instances and loads them from all supported sources."""

    def __init__(self) -> None:
        self._records: dict[str, RegisteredPlugin] = {}
        self.warnings: list[str] = []
        self._loaded = False

    @property
    def records(self) -> tuple[RegisteredPlugin, ...]:
        return tuple(
            sorted(
                self._records.values(),
                key=lambda record: (-record.plugin.priority, record.name),
            )
        )

    @property
    def plugins(self) -> tuple[Plugin, ...]:
        return tuple(record.plugin for record in self.records)

    def get(self, name: str) -> Plugin | None:
        record = self._records.get(name)
        return record.plugin if record is not None else None

    def record(self, name: str) -> RegisteredPlugin | None:
        return self._records.get(name)

    def register(
        self,
        candidate: Plugin | type[Plugin],
        *,
        origin: str = "manual",
        source: str = "",
    ) -> RegisteredPlugin:
        """Validate and register one plugin class or instance."""
        if isinstance(candidate, type):
            try:
                plugin = candidate()
            except Exception as exc:
                raise PluginLoadError(
                    f"{candidate.__module__}.{candidate.__qualname__}: "
                    f"cannot instantiate plugin: {exc}"
                ) from exc
        elif isinstance(candidate, Plugin):
            plugin = candidate
        else:
            raise PluginLoadError(f"not a plugin: {candidate!r}")
        self._validate(plugin, source or origin)
        previous = self._records.get(plugin.name)
        if previous is not None:
            if type(previous.plugin) is type(plugin):
                return previous
            self.warnings.append(
                f"plugin {plugin.name!r} from {source or origin} overrides "
                f"{previous.source or previous.origin}"
            )
        record = RegisteredPlugin(plugin=plugin, origin=origin, source=source or origin)
        self._records[plugin.name] = record
        return record

    def load_all(self, config: Config) -> PluginRegistry:
        """Load every configured plugin source (idempotent)."""
        if self._loaded:
            return self
        self.load_builtin()
        for directory in config.plugin_dirs:
            self.load_directory(directory)
        self.load_entry_points()
        self.load_declared()
        self._loaded = True
        return self

    def load_builtin(self) -> None:
        """Import built-in plugin modules and register their classes."""
        import nvox.plugins as builtin_package

        for info in pkgutil.walk_packages(
            builtin_package.__path__, prefix=builtin_package.__name__ + "."
        ):
            try:
                module = importlib.import_module(info.name)
            except Exception as exc:
                self.warnings.append(f"cannot import built-in module {info.name}: {exc}")
                continue
            self._scan_module(module, origin="builtin", source=info.name)

    def load_directory(self, directory: Path) -> None:
        """Load ``*.py`` and ``*.toml`` plugins from a directory tree."""
        if not directory.is_dir():
            return
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix == ".py" and path.name != "__init__.py":
                self._load_python_file(path)
            elif path.suffix == ".toml":
                self._load_toml_file(path)

    def load_entry_points(self) -> None:
        """Load plugins exposed through the ``nvox.plugins`` entry point group."""
        for entry_point in entry_points(group=ENTRY_POINT_GROUP):
            try:
                loaded = entry_point.load()
            except Exception as exc:
                self.warnings.append(f"cannot load entry point {entry_point.name}: {exc}")
                continue
            if isinstance(loaded, ModuleType):
                self._scan_module(loaded, origin="entry-point", source=entry_point.value)
            else:
                try:
                    self.register(loaded, origin="entry-point", source=entry_point.value)
                except PluginLoadError as exc:
                    self.warnings.append(str(exc))

    def load_declared(self) -> None:
        """Register classes declared with ``@register``."""
        for plugin_class in declared_plugins():
            try:
                self.register(
                    plugin_class,
                    origin="declared",
                    source=f"{plugin_class.__module__}.{plugin_class.__qualname__}",
                )
            except PluginLoadError as exc:
                self.warnings.append(str(exc))

    def matching(self, artifact: Artifact) -> tuple[Plugin, ...]:
        """Return plugins accepting ``artifact``, highest priority first."""
        return tuple(plugin for plugin in self.plugins if plugin.accepts(artifact))

    def requirements(self, plugin: Plugin) -> dict[str, bool]:
        """Return availability of each external requirement of ``plugin``."""
        return {name: shutil.which(name) is not None for name in plugin.requires}

    def _validate(self, plugin: Plugin, source: str) -> None:
        if not plugin.name or not _NAME_PATTERN.match(plugin.name):
            raise PluginLoadError(f"{source}: invalid plugin name {plugin.name!r}")
        if not isinstance(plugin.api_version, int) or plugin.api_version < 1:
            raise PluginLoadError(f"{plugin.name}: api_version must be a positive integer")
        if plugin.api_version > API_VERSION:
            raise PluginLoadError(
                f"{plugin.name}: requires plugin API {plugin.api_version}, "
                f"this Nvox supports {API_VERSION}"
            )
        if plugin.api_version < API_VERSION:
            self.warnings.append(
                f"plugin {plugin.name!r} targets API {plugin.api_version} (current {API_VERSION})"
            )
        if not isinstance(plugin.targets, Sequence) or isinstance(plugin.targets, (str, bytes)):
            raise PluginLoadError(f"{plugin.name}: targets must be a list of media type patterns")
        if not plugin.targets or not all(isinstance(item, str) for item in plugin.targets):
            raise PluginLoadError(f"{plugin.name}: targets must be a non-empty list of strings")
        if not isinstance(plugin.requires, Sequence) or isinstance(plugin.requires, (str, bytes)):
            raise PluginLoadError(f"{plugin.name}: requires must be a list of strings")
        if not all(isinstance(item, str) for item in plugin.requires):
            raise PluginLoadError(f"{plugin.name}: requires must be a list of strings")

    def _scan_module(self, module: ModuleType, *, origin: str, source: str) -> None:
        seen: set[int] = set()
        for value in vars(module).values():
            if not isinstance(value, type) or not issubclass(value, Plugin):
                continue
            if value is Plugin or value.__name__ in _BASE_CLASS_NAMES:
                continue
            if id(value) in seen:
                continue
            seen.add(id(value))
            if not value.name:
                continue
            try:
                self.register(value, origin=origin, source=source)
            except PluginLoadError as exc:
                self.warnings.append(str(exc))

    def _load_python_file(self, path: Path) -> None:
        digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:10]
        module_name = f"nvox_ext_{digest}_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            self.warnings.append(f"cannot load plugin file {path}")
            return
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        parent = str(path.parent)
        added = parent not in sys.path
        if added:
            sys.path.insert(0, parent)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            self.warnings.append(f"cannot import plugin file {path}: {exc}")
            sys.modules.pop(module_name, None)
            return
        self._scan_module(module, origin="directory", source=str(path))

    def _load_toml_file(self, path: Path) -> None:
        try:
            raw: Mapping[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            self.warnings.append(f"cannot read plugin file {path}: {exc}")
            return
        try:
            plugin_class = parse_command_plugin(raw, source=path)
        except CommandPluginError as exc:
            self.warnings.append(str(exc))
            return
        try:
            self.register(plugin_class, origin="directory", source=str(path))
        except PluginLoadError as exc:
            self.warnings.append(str(exc))


__all__ = [
    "ENTRY_POINT_GROUP",
    "PluginLoadError",
    "PluginRegistry",
    "RegisteredPlugin",
]
