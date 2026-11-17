"""Tests for hardened declarative command plugins."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nvox.api import ArtifactKind, Context, Plugin, PluginSkip, make_artifact
from nvox.core.command_plugin import CommandPluginError, parse_command_plugin
from nvox.core.external import safe_input_name


def _plugin(raw: dict[str, object]) -> Plugin:
    return parse_command_plugin(
        {"plugin": {"name": "test.command", "targets": ["*/*"], **raw}},
        source=Path("test.toml"),
    )()


def _context(plugin: Plugin, payload: bytes = b"") -> Context:
    artifact = make_artifact(kind=ArtifactKind.BYTES, name="sample.bin", payload=payload)
    return Context(artifact, plugin.name, max_artifact_bytes=1024 * 1024, depth=1)


def test_command_plugin_uses_a_minimal_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVOX_TEST_SECRET", "inherited")
    plugin = _plugin({"command": ["sh", "-c", 'printf %s "$NVOX_TEST_SECRET"'], "output": "text"})
    ctx = _context(plugin)
    with pytest.raises(PluginSkip, match="no output"):
        plugin.run(ctx.artifact, ctx)

    configured = _plugin(
        {
            "command": ["sh", "-c", 'printf %s "$NVOX_TEST_SECRET"'],
            "output": "text",
            "env": {"NVOX_TEST_SECRET": "configured"},
        }
    )
    ctx = _context(configured)
    configured.run(ctx.artifact, ctx)
    assert ctx.artifacts[0].text == "configured"


def test_command_plugin_can_inherit_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVOX_TEST_SECRET", "inherited")
    plugin = _plugin(
        {
            "command": ["sh", "-c", 'printf %s "$NVOX_TEST_SECRET"'],
            "output": "text",
            "sandbox": {"inherit_env": True},
        }
    )
    ctx = _context(plugin)
    plugin.run(ctx.artifact, ctx)
    assert ctx.artifacts[0].text == "inherited"


def test_command_executable_is_implicitly_required() -> None:
    plugin = _plugin({"command": ["sh", "-c", "true"]})
    assert plugin.requires == ("sh",)
    explicit = _plugin({"command": ["sh", "-c", "true"], "requires": ["custom"]})
    assert explicit.requires == ("custom",)


def test_command_output_is_capped() -> None:
    plugin = _plugin(
        {
            "command": ["sh", "-c", "printf '%01000d' 0"],
            "output": "text",
            "sandbox": {"max_output_bytes": 17},
        }
    )
    ctx = _context(plugin)
    plugin.run(ctx.artifact, ctx)
    assert len(ctx.artifacts[0].payload) == 17
    assert ctx.artifacts[0].params["truncated"] is True


def test_truncated_json_is_rejected() -> None:
    plugin = _plugin(
        {
            "command": ["sh", "-c", "printf '{\"a\": 1}'"],
            "output": "json",
            "sandbox": {"max_output_bytes": 3},
        }
    )
    ctx = _context(plugin)
    with pytest.raises(PluginSkip, match="truncated"):
        plugin.run(ctx.artifact, ctx)


def test_output_files_ignore_symlinks(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("symlink test is POSIX-specific")
    target = tmp_path / "secret"
    target.write_text("do not collect", encoding="utf-8")
    plugin = _plugin(
        {
            "command": [
                "sh",
                "-c",
                f"printf data > {{outdir}}/a.txt; ln -s {target} {{outdir}}/link",
            ],
            "output": "files",
        }
    )
    ctx = _context(plugin)
    plugin.run(ctx.artifact, ctx)
    assert [artifact.name for artifact in ctx.artifacts] == ["test.command/a.txt"]
    assert ctx.artifacts[0].payload == b"data"


def test_output_file_count_and_total_size_are_bounded() -> None:
    plugin = _plugin(
        {
            "command": ["sh", "-c", "printf a > {outdir}/a; printf b > {outdir}/b"],
            "output": "files",
            "sandbox": {"max_files": 1, "max_file_bytes": 10, "max_total_bytes": 10},
        }
    )
    ctx = _context(plugin)
    plugin.run(ctx.artifact, ctx)
    assert [artifact.name for artifact in ctx.artifacts] == ["test.command/a"]
    assert "retained 1 of 2 files" in ctx.artifacts[0].params["limits"]


def test_nonzero_exit_with_output_is_still_emitted() -> None:
    plugin = _plugin(
        {
            "command": ["sh", "-c", "printf useful; exit 7"],
            "output": "text",
        }
    )
    ctx = _context(plugin)
    plugin.run(ctx.artifact, ctx)
    assert ctx.artifacts[0].text == "useful"
    assert ctx.artifacts[0].params["returncode"] == 7


def test_output_files_are_kept_on_nonzero_exit() -> None:
    plugin = _plugin(
        {
            "command": ["sh", "-c", "printf recovered > {outdir}/a.txt; exit 3"],
            "output": "files",
        }
    )
    ctx = _context(plugin)
    plugin.run(ctx.artifact, ctx)
    assert ctx.artifacts[0].name == "test.command/a.txt"
    assert ctx.artifacts[0].payload == b"recovered"
    assert ctx.artifacts[0].params["returncode"] == 3


def test_safe_input_names_cannot_escape_the_input_directory() -> None:
    assert safe_input_name("..") == "artifact.bin"
    assert safe_input_name(".", "image/png") == "artifact.bin"
    assert safe_input_name("photo", "image/png") == "photo.png"


def test_invalid_sandbox_settings_are_rejected() -> None:
    with pytest.raises(CommandPluginError, match="max_output_bytes"):
        parse_command_plugin(
            {"plugin": {"name": "x", "command": ["true"], "sandbox": {"max_output_bytes": 0}}},
            source=Path("x.toml"),
        )
    with pytest.raises(CommandPluginError, match="env"):
        parse_command_plugin(
            {"plugin": {"name": "x", "command": ["true"], "env": {"BAD-NAME": "x"}}},
            source=Path("x.toml"),
        )
    with pytest.raises(CommandPluginError, match="inherit_env"):
        parse_command_plugin(
            {"plugin": {"name": "x", "command": ["true"], "inherit_env": "yes"}},
            source=Path("x.toml"),
        )
    with pytest.raises(CommandPluginError, match="output"):
        parse_command_plugin(
            {"plugin": {"name": "x", "command": ["true"], "output": []}},
            source=Path("x.toml"),
        )


def test_command_with_nul_is_rejected() -> None:
    with pytest.raises(CommandPluginError, match="NUL"):
        parse_command_plugin(
            {"plugin": {"name": "x", "command": ["echo", "bad\x00arg"]}},
            source=Path("x.toml"),
        )
