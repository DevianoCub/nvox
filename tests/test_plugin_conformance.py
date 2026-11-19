"""Conformance checks for the shipped plugin templates and built-in manifests."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from nvox.api import API_VERSION, Analyzer, ArtifactKind, Context, PluginSkip, make_artifact
from nvox.core.isolation import can_isolate
from nvox.core.registry import PluginRegistry

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "examples" / "plugins"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def test_python_and_command_templates_load() -> None:
    registry = PluginRegistry()
    registry.load_directory(TEMPLATE_DIR)

    python_template = registry.get("example.template")
    command_template = registry.get("example.command_template")
    assert isinstance(python_template, Analyzer)
    assert command_template is not None
    assert python_template.api_version == API_VERSION
    assert command_template.api_version == API_VERSION
    assert registry.record("example.template") is not None
    assert can_isolate(registry.record("example.template"))


def test_python_template_run_contract() -> None:
    registry = PluginRegistry()
    registry.load_directory(TEMPLATE_DIR)
    plugin = registry.get("example.template")
    assert plugin is not None

    artifact = make_artifact(
        kind=ArtifactKind.TEXT,
        name="sample.txt",
        payload=b"prefix ctf{template} suffix",
        media_type="text/plain",
    )
    ctx = Context(
        artifact,
        plugin.name,
        max_artifact_bytes=4096,
        depth=1,
        options={"preview_bytes": 8},
    )
    plugin.run(artifact, ctx)

    metadata = next(item for item in ctx.artifacts if item.name == "example/template")
    preview = next(item for item in ctx.artifacts if item.name == "example/preview")
    assert json.loads(metadata.text)["offset"] == 7
    assert preview.payload == b"ctf{temp"
    assert preview.parents == (artifact.sha256,)
    assert preview.producer == plugin.name

    missing = make_artifact(
        kind=ArtifactKind.TEXT,
        name="empty.txt",
        payload=b"nothing here",
        media_type="text/plain",
    )
    with pytest.raises(PluginSkip, match="not found"):
        plugin.run(missing, Context(missing, plugin.name, max_artifact_bytes=4096, depth=1))


def test_builtin_manifests_follow_the_contract() -> None:
    registry = PluginRegistry()
    registry.load_builtin()
    assert registry.records
    for record in registry.records:
        plugin = record.plugin
        assert _NAME.match(plugin.name), plugin.name
        assert plugin.version
        assert plugin.api_version == API_VERSION
        assert plugin.targets
        assert plugin.description
        assert plugin.requires == tuple(plugin.requires)
