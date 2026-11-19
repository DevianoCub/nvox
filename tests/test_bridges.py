"""Tests for optional external-tool bridges using fake executables."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from fixture_builders import make_jpeg_with_exif, make_png
from nvox.api import ArtifactKind, Context, PluginSkip, make_artifact
from nvox.core import engine as engine_module
from nvox.core.config import Config
from nvox.core.doctor import collect
from nvox.core.engine import Engine
from nvox.core.registry import PluginRegistry
from nvox.plugins.bridges import stego as stego_module
from nvox.plugins.bridges.audio import FfprobeBridge, SoxBridge
from nvox.plugins.bridges.carving import BinwalkBridge, ForemostBridge
from nvox.plugins.bridges.metadata import ExiftoolBridge
from nvox.plugins.bridges.ocr import TesseractBridge, ZbarBridge
from nvox.plugins.bridges.stego import (
    OutguessBridge,
    SteghideBridge,
    StegseekBridge,
    StegSolveBridge,
    ZstegBridge,
)
from nvox.plugins.bridges.validation import PngcheckBridge


def _install_tool(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
    body: str,
) -> Path:
    directory = tmp_path / "bin"
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text(f"#!{sys.executable}\n{textwrap.dedent(body).strip()}\n", encoding="utf-8")
    path.chmod(0o755)
    monkeypatch.setenv("PATH", str(directory))
    return path


def _context(
    plugin: object,
    *,
    payload: bytes = b"sample",
    media_type: str = "image/png",
    name: str = "sample.png",
    options: dict[str, object] | None = None,
) -> Context:
    if media_type.startswith("image/"):
        kind = ArtifactKind.IMAGE
    elif media_type.startswith("audio/"):
        kind = ArtifactKind.AUDIO
    elif media_type in {"application/zip", "application/gzip"}:
        kind = ArtifactKind.ARCHIVE
    else:
        kind = ArtifactKind.BYTES
    artifact = make_artifact(kind=kind, name=name, payload=payload, media_type=media_type)
    return Context(
        artifact,
        getattr(plugin, "name", "test.bridge"),
        max_artifact_bytes=16 * 1024 * 1024,
        depth=0,
        options=options or {},
    )


def _run(plugin: object, ctx: Context) -> None:
    plugin.run(ctx.artifact, ctx)  # type: ignore[attr-defined]


def test_bridge_plugins_are_discovered_by_the_registry() -> None:
    registry = PluginRegistry()
    registry.load_builtin()
    assert {
        "bridge.exiftool",
        "bridge.pngcheck",
        "bridge.zsteg",
        "bridge.steghide",
        "bridge.stegseek",
        "bridge.outguess",
        "bridge.binwalk",
        "bridge.foremost",
        "bridge.stegsolve",
        "bridge.zbar",
        "bridge.tesseract",
        "bridge.ffprobe",
        "bridge.sox",
    } <= {record.name for record in registry.records}


def test_doctor_reports_bridge_names() -> None:
    report = collect(Config(plugin_dirs=()))
    tools = {tool["name"]: tool for tool in report["tools"]}
    assert tools["zsteg"]["bridges"] == ["bridge.zsteg"]
    assert tools["StegSolve"]["bridges"] == ["bridge.stegsolve"]
    assert tools["ffprobe"]["bridges"] == ["bridge.ffprobe"]
    assert tools["sox"]["bridges"] == ["bridge.sox"]
    assert tools["file"]["bridges"] == []


def test_exiftool_bridge_emits_json_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_tool(
        monkeypatch,
        tmp_path,
        "exiftool",
        """
        import json, sys
        print(json.dumps([{"SourceFile": sys.argv[-1], "EXIF:Make": "ctf{meta}"}]))
        """,
    )
    plugin = ExiftoolBridge()
    ctx = _context(plugin)
    _run(plugin, ctx)
    assert ctx.artifacts[0].kind is ArtifactKind.META
    assert "ctf{meta}" in ctx.artifacts[0].text


def test_ffprobe_bridge_emits_json_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_tool(
        monkeypatch,
        tmp_path,
        "ffprobe",
        """
        import json, sys
        print(json.dumps({
            "format": {"format_name": "wav", "duration": "1.0"},
            "streams": [{"codec_type": "audio", "sample_rate": "8000"}],
            "input": sys.argv[-1],
        }))
        """,
    )
    plugin = FfprobeBridge()
    ctx = _context(plugin, media_type="audio/wav", name="sample.wav")
    _run(plugin, ctx)
    assert ctx.artifacts[0].kind is ArtifactKind.META
    assert '"format_name": "wav"' in ctx.artifacts[0].text


def test_ffprobe_bridge_rejects_invalid_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_tool(monkeypatch, tmp_path, "ffprobe", "print('not json')")
    plugin = FfprobeBridge()
    ctx = _context(plugin, media_type="audio/wav", name="sample.wav")
    with pytest.raises(PluginSkip, match="invalid JSON"):
        _run(plugin, ctx)


def test_sox_bridge_emits_stat_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_tool(
        monkeypatch,
        tmp_path,
        "sox",
        """
        import sys
        print("Samples read: 8000")
        print("Maximum amplitude: 0.5")
        print("sox ", " ".join(sys.argv[1:]))
        """,
    )
    plugin = SoxBridge()
    ctx = _context(plugin, media_type="audio/wav", name="sample.wav")
    _run(plugin, ctx)
    assert ctx.artifacts[0].name == "sox/report"
    assert "Samples read: 8000" in ctx.artifacts[0].text
    assert "-n stat" in ctx.artifacts[0].text


def test_sox_bridge_info_mode_and_invalid_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_tool(monkeypatch, tmp_path, "sox", "import sys; print(' '.join(sys.argv[1:]))")
    plugin = SoxBridge()
    ctx = _context(plugin, media_type="audio/wav", name="sample.wav", options={"mode": "info"})
    _run(plugin, ctx)
    assert "--i" in ctx.artifacts[0].text

    ctx = _context(plugin, media_type="audio/wav", name="sample.wav", options={"mode": "raw"})
    with pytest.raises(PluginSkip, match="mode must be"):
        _run(plugin, ctx)


def test_pngcheck_bridge_emits_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_tool(monkeypatch, tmp_path, "pngcheck", "print('OK: sample.png')")
    plugin = PngcheckBridge()
    ctx = _context(plugin)
    _run(plugin, ctx)
    assert ctx.artifacts[0].text == "OK: sample.png\n"


def test_zsteg_bridge_emits_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_tool(
        monkeypatch,
        tmp_path,
        "zsteg",
        'print("b1,rgb,lsb,xy .. text \\"ctf{lsb}\\"")',
    )
    plugin = ZstegBridge()
    ctx = _context(plugin)
    _run(plugin, ctx)
    assert "ctf{lsb}" in ctx.artifacts[0].text


def test_steghide_bridge_extracts_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_tool(
        monkeypatch,
        tmp_path,
        "steghide",
        """
        import sys
        from pathlib import Path
        if sys.argv[1] == "info":
            print("embedded data")
        else:
            output = Path(sys.argv[sys.argv.index("-xf") + 1])
            output.write_bytes(b"ctf{steghide}")
            print("extracted")
        """,
    )
    plugin = SteghideBridge()
    ctx = _context(plugin, media_type="image/jpeg", name="sample.jpg")
    _run(plugin, ctx)
    names = {artifact.name for artifact in ctx.artifacts}
    assert "steghide/info" in names
    assert "steghide/steghide.out" in names
    extracted = next(
        artifact for artifact in ctx.artifacts if artifact.name.endswith("steghide.out")
    )
    assert extracted.payload == b"ctf{steghide}"


def test_stegseek_bridge_requires_and_uses_wordlist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_tool(
        monkeypatch,
        tmp_path,
        "stegseek",
        """
        import sys
        from pathlib import Path
        if not Path(sys.argv[-1]).is_file():
            raise SystemExit(2)
        output = Path(sys.argv[sys.argv.index("-o") + 1])
        output.write_bytes(b"ctf{stegseek}")
        print("Found passphrase")
        """,
    )
    wordlist = tmp_path / "words.txt"
    wordlist.write_text("password\n", encoding="utf-8")
    plugin = StegseekBridge()
    ctx = _context(plugin, options={"wordlist": str(wordlist)})

    monkeypatch.setattr(stego_module, "default_wordlist", lambda: None)
    missing = StegseekBridge()
    missing_ctx = _context(missing)
    with pytest.raises(PluginSkip, match="install-wordlist"):
        _run(missing, missing_ctx)

    _run(plugin, ctx)
    names = {artifact.name for artifact in ctx.artifacts}
    assert "stegseek/report" in names
    assert "stegseek/stegseek.out" in names

    monkeypatch.chdir(tmp_path)
    relative = StegseekBridge()
    relative_ctx = _context(relative, options={"wordlist": "words.txt"})
    _run(relative, relative_ctx)
    assert "stegseek/stegseek.out" in {artifact.name for artifact in relative_ctx.artifacts}


def test_default_wordlist_uses_xdg_data_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    wordlist = tmp_path / "nvox" / "wordlists" / "rockyou.txt"
    wordlist.parent.mkdir(parents=True)
    wordlist.write_text("password\n", encoding="utf-8")
    assert stego_module.default_wordlist() == wordlist


def test_stegseek_auto_detects_wordlist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_tool(
        monkeypatch,
        tmp_path,
        "stegseek",
        """
        import sys
        from pathlib import Path
        if not Path(sys.argv[-1]).is_file():
            raise SystemExit(2)
        output = Path(sys.argv[sys.argv.index("-o") + 1])
        output.write_bytes(b"ctf{auto_wordlist}")
        print("Found passphrase")
        """,
    )
    wordlist = tmp_path / "auto-words.txt"
    wordlist.write_text("password\n", encoding="utf-8")
    monkeypatch.setattr(stego_module, "default_wordlist", lambda: wordlist)

    plugin = StegseekBridge()
    ctx = _context(plugin, media_type="image/jpeg", name="sample.jpg")
    _run(plugin, ctx)
    assert any(artifact.name.endswith("stegseek.out") for artifact in ctx.artifacts)


def test_outguess_bridge_extracts_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_tool(
        monkeypatch,
        tmp_path,
        "outguess",
        """
        import sys
        from pathlib import Path
        Path(sys.argv[-1]).write_bytes(b"ctf{outguess}")
        print("extracted")
        """,
    )
    plugin = OutguessBridge()
    ctx = _context(plugin, media_type="image/jpeg", name="sample.jpg")
    _run(plugin, ctx)
    assert any(artifact.name.endswith("outguess.out") for artifact in ctx.artifacts)


def test_binwalk_bridge_scans_and_extracts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_tool(
        monkeypatch,
        tmp_path,
        "binwalk",
        """
        import sys
        from pathlib import Path
        if "-C" in sys.argv:
            output = Path(sys.argv[sys.argv.index("-C") + 1])
            (output / "carved.bin").write_bytes(b"ctf{binwalk}")
        print("DECIMAL       HEXADECIMAL     DESCRIPTION")
        """,
    )
    plugin = BinwalkBridge()
    ctx = _context(plugin, options={"extract": True})
    _run(plugin, ctx)
    names = {artifact.name for artifact in ctx.artifacts}
    assert "binwalk/report" in names
    assert "binwalk/carved.bin" in names


def test_foremost_bridge_collects_carved_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_tool(
        monkeypatch,
        tmp_path,
        "foremost",
        """
        import sys
        from pathlib import Path
        output = Path(sys.argv[sys.argv.index("-o") + 1])
        (output / "jpg").mkdir(parents=True, exist_ok=True)
        (output / "jpg" / "00000000.jpg").write_bytes(b"ctf{foremost}")
        print("Foremost finished")
        """,
    )
    plugin = ForemostBridge()
    ctx = _context(plugin)
    _run(plugin, ctx)
    assert "foremost/jpg/00000000.jpg" in {artifact.name for artifact in ctx.artifacts}


def test_zbar_and_tesseract_bridges(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_tool(monkeypatch, tmp_path, "zbarimg", "print('QR-Code:ctf{zbar}')")
    zbar = ZbarBridge()
    zctx = _context(zbar)
    _run(zbar, zctx)
    assert "ctf{zbar}" in zctx.artifacts[0].text

    _install_tool(monkeypatch, tmp_path, "tesseract", "print('ctf{tesseract}')")
    tesseract = TesseractBridge()
    tctx = _context(tesseract)
    _run(tesseract, tctx)
    assert "ctf{tesseract}" in tctx.artifacts[0].text


def test_stegsolve_requires_a_headless_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plugin = StegSolveBridge()
    ctx = _context(plugin)
    with pytest.raises(PluginSkip, match="jar or command"):
        _run(plugin, ctx)

    _install_tool(monkeypatch, tmp_path, "stegsolve", "print('ctf{stegsolve}')")
    command = [sys.executable, str(tmp_path / "bin" / "stegsolve"), "{input}", "{outdir}"]
    configured = StegSolveBridge()
    configured_ctx = _context(configured, options={"command": command})
    _run(configured, configured_ctx)
    assert "ctf{stegsolve}" in configured_ctx.artifacts[0].text


def test_engine_runs_a_bridge_when_its_tool_is_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_tool(
        monkeypatch,
        tmp_path,
        "exiftool",
        """
        import json
        print(json.dumps([{"EXIF:Make": "ctf{engine_bridge}"}]))
        """,
    )
    registry = PluginRegistry()
    registry.load_builtin()
    path = tmp_path / "sample.png"
    path.write_bytes(make_png(1, 1, [b"\x00\x00\x00"]))
    result = Engine(Config(plugin_dirs=()), registry=registry, jobs=1).analyze(path)
    metadata = next(
        artifact for artifact in result.artifacts if artifact.producer == "bridge.exiftool"
    )
    assert "ctf{engine_bridge}" in metadata.text


def test_missing_bridges_are_requirement_skips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(engine_module.shutil, "which", lambda _name: None)
    registry = PluginRegistry()
    registry.load_builtin()
    engine = Engine(Config(plugin_dirs=()), registry=registry, jobs=1)

    png_path = tmp_path / "sample.png"
    png_path.write_bytes(make_png(1, 1, [b"\x00\x00\x00"]))
    jpeg_path = tmp_path / "sample.jpg"
    jpeg_path.write_bytes(make_jpeg_with_exif(image_description="ctf{jpeg}"))

    bridge_skips: dict[str, str] = {}
    for path in (png_path, jpeg_path):
        result = engine.analyze(path)
        assert result.root is not None
        for skip in result.skips:
            if skip.kind == "requirement":
                bridge_skips[skip.plugin] = skip.reason
    assert {
        "bridge.exiftool",
        "bridge.pngcheck",
        "bridge.zsteg",
        "bridge.steghide",
        "bridge.stegseek",
        "bridge.outguess",
        "bridge.binwalk",
        "bridge.foremost",
        "bridge.stegsolve",
        "bridge.zbar",
        "bridge.tesseract",
    } <= bridge_skips.keys()
    assert all("missing external tool" in reason for reason in bridge_skips.values())
