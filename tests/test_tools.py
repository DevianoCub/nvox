"""Tests for the external tool registry."""

from __future__ import annotations

from nvox.core.doctor import _human_bytes
from nvox.core.tools import TOOLS


def test_tool_names_are_unique() -> None:
    names = [tool.name for tool in TOOLS]
    assert len(names) == len(set(names))


def test_tools_have_metadata() -> None:
    for tool in TOOLS:
        assert tool.name
        assert tool.binary
        assert tool.category
        assert tool.purpose
        assert tool.stage.startswith("M")
        if tool.nix is not None:
            assert tool.nix.startswith("nixpkgs#")


def test_bridge_names_are_declared_for_optional_tools() -> None:
    for tool in TOOLS:
        for bridge in tool.bridges:
            assert bridge.startswith("bridge.")


def test_expected_categories_are_covered() -> None:
    categories = {tool.category for tool in TOOLS}
    assert {"generic", "image", "audio", "metadata", "stego"} <= categories


def test_human_bytes() -> None:
    assert _human_bytes(512) == "512 B"
    assert _human_bytes(2048) == "2.0 KiB"
    assert _human_bytes(64 * 1024 * 1024) == "64.0 MiB"
