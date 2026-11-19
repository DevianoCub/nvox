"""Tests for configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from nvox.core.config import Config, ConfigError, ScanLimits, default_plugin_dirs


def test_defaults_without_config(tmp_path: Path) -> None:
    config = Config.load(cwd=tmp_path)
    assert config.path is None
    assert config.flag_formats == ()
    assert config.plugin_dirs == default_plugin_dirs(tmp_path)
    assert config.limits == ScanLimits()
    assert config.limits.isolation == "thread"


def test_loads_isolation_mode(tmp_path: Path) -> None:
    (tmp_path / "nvox.toml").write_text(
        '[scan]\nisolation = "process"\n',
        encoding="utf-8",
    )
    config = Config.load(cwd=tmp_path)
    assert config.limits.isolation == "process"


def test_rejects_unknown_isolation_mode(tmp_path: Path) -> None:
    (tmp_path / "nvox.toml").write_text(
        '[scan]\nisolation = "green-threads"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="scan.isolation"):
        Config.load(cwd=tmp_path)


def test_loads_project_config(tmp_path: Path) -> None:
    (tmp_path / "nvox.toml").write_text(
        """
[scan]
max_depth = 7
timeout = 3.5
jobs = 2

[flags]
formats = ["ctf{}", "htb{}"]

[plugins]
dirs = ["shared/plugins", "/opt/nvox/plugins"]

[tools]
disable = ["steghide"]
""",
        encoding="utf-8",
    )
    config = Config.load(cwd=tmp_path)
    assert config.path == tmp_path / "nvox.toml"
    assert config.flag_formats == ("ctf{}", "htb{}")
    assert config.plugin_dirs == (tmp_path / "shared/plugins", Path("/opt/nvox/plugins"))
    assert config.limits.max_depth == 7
    assert config.limits.timeout == 3.5
    assert config.limits.jobs == 2
    assert config.disabled_tools == ("steghide",)


def test_explicit_missing_config_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        Config.load(cwd=tmp_path, explicit=tmp_path / "nope.toml")


def test_plugin_dirs_are_relative_to_the_config_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "nvox.toml").write_text('[plugins]\ndirs = ["plugins"]\n', encoding="utf-8")
    config = Config.load(cwd=tmp_path, explicit=project / "nvox.toml")
    assert config.plugin_dirs == (project / "plugins",)


def test_invalid_toml_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "nvox.toml").write_text("[scan\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid TOML"):
        Config.load(cwd=tmp_path)


@pytest.mark.parametrize(
    ("snippet", "message"),
    [
        ("[flags]\nformats = 'ctf{}'\n", "flags.formats"),
        ("[scan]\nmax_depth = 0\n", "scan.max_depth"),
        ("[scan]\ntimeout = -1\n", "scan.timeout"),
        ("[scan]\njobs = -2\n", "scan.jobs"),
    ],
)
def test_config_validation(tmp_path: Path, snippet: str, message: str) -> None:
    (tmp_path / "nvox.toml").write_text(snippet, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        Config.load(cwd=tmp_path)
