"""Tests for plugin discovery, validation and declarative plugins."""

from __future__ import annotations

from pathlib import Path

import pytest

from nvox.api import Analyzer, ArtifactKind, Context, PluginSkip, make_artifact
from nvox.core import registry as registry_module
from nvox.core.command_plugin import (
    CommandPluginError,
    parse_command_plugin,
)
from nvox.core.config import Config
from nvox.core.registry import PluginLoadError, PluginRegistry

PY_PLUGIN = """
from nvox.api import Analyzer, Artifact, Context


class LocalPlugin(Analyzer):
    name = "local.example"
    version = "1.2.3"
    priority = 10
    targets = ("*/*",)

    def run(self, artifact: Artifact, ctx: Context) -> None:
        ctx.text("local", "from-local")
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_load_builtin_plugins() -> None:
    registry = PluginRegistry()
    registry.load_builtin()
    assert registry.get("generic.strings") is not None
    assert registry.warnings == []


def test_load_directory_python_plugin(tmp_path: Path) -> None:
    _write(tmp_path / "plugins" / "local.py", PY_PLUGIN)
    registry = PluginRegistry().load_all(Config(plugin_dirs=(tmp_path / "plugins",)))
    plugin = registry.get("local.example")
    assert plugin is not None
    assert plugin.version == "1.2.3"
    assert registry.record("local.example").origin == "directory"


def test_load_directory_reports_broken_plugins(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    _write(plugin_dir / "broken.py", "raise RuntimeError('boom')\n")
    _write(
        plugin_dir / "future.py",
        "from nvox.api import Analyzer\n"
        "class Future(Analyzer):\n"
        "    name = 'local.future'\n"
        "    api_version = 999\n"
        "    targets = ('*/*',)\n",
    )
    _write(plugin_dir / "invalid.toml", "[plugin]\nname = 'local.invalid'\n")
    registry = PluginRegistry().load_all(Config(plugin_dirs=(plugin_dir,)))
    assert registry.get("local.future") is None
    assert registry.get("local.invalid") is None
    assert any("cannot import plugin file" in warning for warning in registry.warnings)
    assert any("supports 1" in warning or "API" in warning for warning in registry.warnings)
    assert any("command" in warning for warning in registry.warnings)


def test_toml_plugin_is_loaded(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    _write(
        plugin_dir / "echo.toml",
        """
[plugin]
name = "local.toml"
kind = "analyzer"
targets = ["*/*"]
command = ["sh", "-c", "printf hello"]
output = "text"

[plugin.options]
mode = { type = "string", default = "fast" }
""",
    )
    registry = PluginRegistry().load_all(Config(plugin_dirs=(plugin_dir,)))
    plugin = registry.get("local.toml")
    assert plugin is not None
    assert dict(plugin.options) == {"mode": "fast"}

    artifact = make_artifact(kind=ArtifactKind.BYTES, name="x.bin", payload=b"")
    ctx = Context(artifact, plugin.name, max_artifact_bytes=1024, depth=1)
    plugin.run(artifact, ctx)
    assert ctx.artifacts[0].text == "hello"


def test_toml_plugin_rejects_invalid_definitions() -> None:
    with pytest.raises(CommandPluginError, match="command"):
        parse_command_plugin({"plugin": {"name": "x"}}, source=Path("x.toml"))
    with pytest.raises(CommandPluginError, match="output"):
        parse_command_plugin(
            {"plugin": {"name": "x", "command": ["true"], "output": "nope"}},
            source=Path("x.toml"),
        )
    with pytest.raises(CommandPluginError, match="kind"):
        parse_command_plugin(
            {"plugin": {"name": "x", "command": ["true"], "kind": "nope"}},
            source=Path("x.toml"),
        )


def test_command_plugin_output_modes() -> None:
    def build(command: list[str], output: str):
        return parse_command_plugin(
            {
                "plugin": {
                    "name": "local.cmd",
                    "command": command,
                    "output": output,
                    "targets": ["*/*"],
                }
            },
            source=Path("x.toml"),
        )()

    artifact = make_artifact(kind=ArtifactKind.BYTES, name="x.bin", payload=b"")
    files_plugin = build(["sh", "-c", "printf data > {outdir}/a.txt"], "files")
    ctx = Context(artifact, files_plugin.name, max_artifact_bytes=1024, depth=1)
    files_plugin.run(artifact, ctx)
    assert ctx.artifacts[0].name == "local.cmd/a.txt"
    assert ctx.artifacts[0].payload == b"data"

    json_plugin = build(["sh", "-c", "printf '{\"a\": 1}'"], "json")
    ctx = Context(artifact, json_plugin.name, max_artifact_bytes=1024, depth=1)
    json_plugin.run(artifact, ctx)
    assert ctx.artifacts[0].kind is ArtifactKind.META
    assert ctx.artifacts[0].text == '{"a": 1}'

    literal = build(["sh", "-c", "printf '{missing}'"], "text")
    ctx = Context(artifact, literal.name, max_artifact_bytes=1024, depth=1)
    literal.run(artifact, ctx)
    assert ctx.artifacts[0].text == "{missing}"


def test_command_plugin_timeout() -> None:
    plugin = parse_command_plugin(
        {
            "plugin": {
                "name": "local.slow",
                "command": ["sh", "-c", "sleep 5"],
                "output": "text",
                "targets": ["*/*"],
                "timeout": 0.1,
            }
        },
        source=Path("x.toml"),
    )()
    artifact = make_artifact(kind=ArtifactKind.BYTES, name="x.bin", payload=b"")
    ctx = Context(artifact, plugin.name, max_artifact_bytes=1024, depth=1)
    with pytest.raises(PluginSkip, match="timed out"):
        plugin.run(artifact, ctx)


def test_registry_override_warns() -> None:
    registry = PluginRegistry()

    class First(Analyzer):
        name = "test.override"
        targets = ("*/*",)

    class Second(Analyzer):
        name = "test.override"
        targets = ("*/*",)

    registry.register(First)
    registry.register(Second)
    assert type(registry.get("test.override")) is Second
    assert len(registry.warnings) == 1
    assert "overrides" in registry.warnings[0]


def test_registering_the_same_class_twice_is_silent() -> None:
    registry = PluginRegistry()

    class Once(Analyzer):
        name = "test.once"
        targets = ("*/*",)

    registry.register(Once)
    registry.register(Once)
    assert registry.warnings == []
    assert len(registry.records) == 1


def test_registry_refuses_future_api_version() -> None:
    class Future(Analyzer):
        name = "test.future-api"
        api_version = 999
        targets = ("*/*",)

    with pytest.raises(PluginLoadError, match="supports 1"):
        PluginRegistry().register(Future)


def test_requirements_report() -> None:
    class NeedsTools(Analyzer):
        name = "test.needs"
        targets = ("*/*",)
        requires = ("sh", "nvox-tool-that-does-not-exist")

    registry = PluginRegistry()
    registry.register(NeedsTools)
    requirements = registry.requirements(registry.get("test.needs"))
    assert requirements["sh"] is True
    assert requirements["nvox-tool-that-does-not-exist"] is False


def test_entry_point_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    class EntryPlugin(Analyzer):
        name = "test.entry"
        targets = ("*/*",)

    class FakeEntryPoint:
        name = "fake"
        value = "fake:EntryPlugin"

        def load(self) -> type[Analyzer]:
            return EntryPlugin

    monkeypatch.setattr(registry_module, "entry_points", lambda group: [FakeEntryPoint()])
    registry = PluginRegistry()
    registry.load_entry_points()
    assert registry.get("test.entry") is not None
    assert registry.record("test.entry").origin == "entry-point"
