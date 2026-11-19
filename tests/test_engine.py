"""Tests for the scan engine: chains, limits, dedup, hooks and provenance."""

from __future__ import annotations

import time
from pathlib import Path
from typing import ClassVar

from nvox.api import Analyzer, Artifact, ArtifactKind, Context
from nvox.core.config import Config, ScanLimits
from nvox.core.engine import Engine, ScanResult
from nvox.core.registry import PluginRegistry
from nvox.core.store import ArtifactStore


def _write_sample(tmp_path: Path, payload: bytes = b"prefix ctf{chain} suffix") -> Path:
    path = tmp_path / "sample.bin"
    path.write_bytes(payload)
    return path


def _engine(
    plugins: list[type[Analyzer]],
    *,
    config: Config | None = None,
    store: ArtifactStore | None = None,
) -> Engine:
    registry = PluginRegistry()
    for plugin in plugins:
        registry.register(plugin, origin="test")
    return Engine(
        config if config is not None else Config(plugin_dirs=()),
        registry=registry,
        store=store,
    )


class ToText(Analyzer):
    name = "test.to_text"
    targets = ("application/octet-stream",)
    priority = 90

    def run(self, artifact: Artifact, ctx: Context) -> None:
        decoded = artifact.payload.decode("utf-8", errors="replace")
        ctx.text("decoded", "text:\n" + decoded)


class Finder(Analyzer):
    name = "test.finder"
    targets = ("text/plain",)
    priority = 10

    def run(self, artifact: Artifact, ctx: Context) -> None:
        if "ctf{" in artifact.text and not artifact.text.startswith("FOUND: "):
            ctx.text("match", "FOUND: " + artifact.text)


def test_end_to_end_chain(tmp_path: Path) -> None:
    engine = _engine([ToText, Finder])
    result = engine.analyze(_write_sample(tmp_path))

    assert result.evidence is not None
    assert result.plugin_runs == 3  # decoded text, match, then finder on "FOUND"
    assert result.skips == ()

    root = result.root
    assert root is not None
    assert root.kind is ArtifactKind.BYTES
    assert root.depth == 0

    decoded = next(a for a in result.artifacts if a.name == "decoded")
    match = next(a for a in result.artifacts if a.name == "match")
    assert decoded.parents == (root.sha256,)
    assert decoded.depth == 1
    assert match.parents == (decoded.sha256,)
    assert match.depth == 2
    assert match.producer == "test.finder"
    assert "ctf{chain}" in decoded.text

    assert result.find(root.sha256) is root
    assert result.find(root.sha256[:8]) is root
    assert result.find("deadbeef") is None
    data = result.to_dict()
    assert data["evidence"]["sha256"] == root.sha256
    assert len(data["artifacts"]) == 3


class SameOne(Analyzer):
    name = "test.same_one"
    targets = ("application/octet-stream",)

    def run(self, artifact: Artifact, ctx: Context) -> None:
        ctx.text("same", "identical")


class SameTwo(Analyzer):
    name = "test.same_two"
    targets = ("application/octet-stream",)

    def run(self, artifact: Artifact, ctx: Context) -> None:
        ctx.text("same", "identical")


def test_duplicate_payloads_are_deduplicated(tmp_path: Path) -> None:
    result = _engine([SameOne, SameTwo]).analyze(_write_sample(tmp_path))
    texts = result.by_kind(ArtifactKind.TEXT)
    assert len(texts) == 1
    assert texts[0].payload == b"identical"
    assert result.plugin_runs == 2


class Upper(Analyzer):
    name = "test.upper"
    targets = ("text/plain",)

    def run(self, artifact: Artifact, ctx: Context) -> None:
        ctx.text("upper", artifact.text.upper())


def test_max_depth_stops_the_chain(tmp_path: Path) -> None:
    config = Config(plugin_dirs=(), limits=ScanLimits(max_depth=1))
    result = _engine([ToText, Upper], config=config).analyze(_write_sample(tmp_path))
    assert result.plugin_runs == 1
    assert {artifact.depth for artifact in result.artifacts} == {0, 1}


class Slow(Analyzer):
    name = "test.slow"
    targets = ("application/octet-stream",)

    def run(self, artifact: Artifact, ctx: Context) -> None:
        time.sleep(0.5)
        ctx.text("late", "too late")


def test_plugin_timeout_is_recorded(tmp_path: Path) -> None:
    config = Config(plugin_dirs=(), limits=ScanLimits(timeout=0.05))
    started = time.monotonic()
    result = _engine([Slow], config=config).analyze(_write_sample(tmp_path))
    elapsed = time.monotonic() - started
    assert elapsed < 0.3
    assert result.plugin_runs == 1
    assert [skip.kind for skip in result.skips] == ["timeout"]
    assert "timeout" in result.skips[0].reason


class NeedsTool(Analyzer):
    name = "test.needs_tool"
    targets = ("application/octet-stream",)
    requires = ("nvox-tool-that-does-not-exist",)

    def run(self, artifact: Artifact, ctx: Context) -> None:
        raise AssertionError("must not run without its requirement")


def test_missing_requirement_skips_plugin(tmp_path: Path) -> None:
    result = _engine([NeedsTool]).analyze(_write_sample(tmp_path))
    assert result.plugin_runs == 0
    assert [skip.kind for skip in result.skips] == ["requirement"]
    assert "nvox-tool-that-does-not-exist" in result.skips[0].reason


class DisabledPlugin(Analyzer):
    name = "test.disabled"
    targets = ("application/octet-stream",)

    def run(self, artifact: Artifact, ctx: Context) -> None:
        raise AssertionError("disabled plugin must not run")


def test_disabled_plugin_is_skipped(tmp_path: Path) -> None:
    config = Config(plugin_dirs=(), disabled_plugins=("test.disabled",))
    result = _engine([DisabledPlugin], config=config).analyze(_write_sample(tmp_path))
    assert result.plugin_runs == 0
    assert [skip.kind for skip in result.skips] == ["disabled"]


class DisabledToolPlugin(Analyzer):
    name = "test.disabled_tool"
    targets = ("application/octet-stream",)
    requires = ("sh",)

    def run(self, artifact: Artifact, ctx: Context) -> None:
        raise AssertionError("tool is disabled")


def test_disabled_tool_is_skipped(tmp_path: Path) -> None:
    config = Config(plugin_dirs=(), disabled_tools=("sh",))
    result = _engine([DisabledToolPlugin], config=config).analyze(_write_sample(tmp_path))
    assert [skip.kind for skip in result.skips] == ["disabled"]
    assert "sh" in result.skips[0].reason


class Skipper(Analyzer):
    name = "test.skipper"
    targets = ("application/octet-stream",)

    def run(self, artifact: Artifact, ctx: Context) -> None:
        ctx.skip("nothing interesting")


def test_plugin_skip_reason(tmp_path: Path) -> None:
    result = _engine([Skipper]).analyze(_write_sample(tmp_path))
    assert [skip.kind for skip in result.skips] == ["plugin"]
    assert result.skips[0].reason == "nothing interesting"


class Broken(Analyzer):
    name = "test.broken"
    targets = ("application/octet-stream",)

    def run(self, artifact: Artifact, ctx: Context) -> None:
        raise RuntimeError("boom")


def test_plugin_errors_are_isolated(tmp_path: Path) -> None:
    result = _engine([Broken]).analyze(_write_sample(tmp_path))
    assert result.root is not None
    assert [skip.kind for skip in result.skips] == ["error"]
    assert "RuntimeError: boom" in result.skips[0].reason


def _write_isolated_plugin(tmp_path: Path, body: str) -> Path:
    directory = tmp_path / "plugins"
    directory.mkdir()
    (directory / "isolated_plugin.py").write_text(
        "from __future__ import annotations\n"
        "\n"
        "from nvox.api import Analyzer, Artifact, Context\n"
        "\n"
        "\n"
        "class Isolated(Analyzer):\n"
        '    name = "test.isolated"\n'
        '    targets = ("application/octet-stream",)\n'
        "    priority = 100\n"
        "\n"
        "    def run(self, artifact: Artifact, ctx: Context) -> None:\n"
        f"{body}",
        encoding="utf-8",
    )
    return directory


def _isolated_engine(plugin_dir: Path, *, timeout: float = 10.0) -> Engine:
    config = Config(
        plugin_dirs=(plugin_dir,),
        limits=ScanLimits(isolation="process", jobs=1, timeout=timeout),
    )
    registry = PluginRegistry().load_all(config).subset(["test.isolated"])
    return Engine(config, registry=registry)


def test_process_isolation_runs_discovered_plugin(tmp_path: Path) -> None:
    plugin_dir = _write_isolated_plugin(
        tmp_path,
        '        ctx.text("isolated", "from child")\n',
    )
    result = _isolated_engine(plugin_dir).analyze(_write_sample(tmp_path))

    assert result.skips == ()
    texts = result.by_kind(ArtifactKind.TEXT)
    assert [artifact.payload for artifact in texts] == [b"from child"]
    assert texts[0].producer == "test.isolated"


def test_process_isolation_kills_timed_out_plugin(tmp_path: Path) -> None:
    plugin_dir = _write_isolated_plugin(
        tmp_path,
        "        import time\n"
        "\n"
        "        time.sleep(30)\n"
        '        ctx.text("late", "too late")\n',
    )
    started = time.monotonic()
    result = _isolated_engine(plugin_dir, timeout=0.5).analyze(_write_sample(tmp_path))
    elapsed = time.monotonic() - started

    assert elapsed < 5
    assert [skip.kind for skip in result.skips] == ["timeout"]
    assert "0.5s" in result.skips[0].reason


def test_process_isolation_falls_back_for_manual_plugins(tmp_path: Path) -> None:
    config = Config(plugin_dirs=(), limits=ScanLimits(isolation="process", jobs=1))
    result = _engine([ToText], config=config).analyze(_write_sample(tmp_path))
    assert result.skips == ()
    assert any(artifact.name == "decoded" for artifact in result.artifacts)


class Optioned(Analyzer):
    name = "test.optioned"
    targets = ("application/octet-stream",)
    options: ClassVar[dict[str, str]] = {"value": "default"}

    def run(self, artifact: Artifact, ctx: Context) -> None:
        ctx.text("option", str(ctx.option("value")))


def test_plugin_options_merge_configuration(tmp_path: Path) -> None:
    config = Config(
        plugin_dirs=(),
        plugin_options={"test.optioned": {"value": "from-config"}},
    )
    result = _engine([Optioned], config=config).analyze(_write_sample(tmp_path))
    text = next(a for a in result.artifacts if a.name == "option")
    assert text.text == "from-config"


class Refuser(Analyzer):
    name = "test.refuser"
    targets = ("*/*",)

    def pre_ingest(self, path: Path) -> str | None:
        return "not allowed by policy"


def test_pre_ingest_hook_refuses_input(tmp_path: Path) -> None:
    result = _engine([Refuser]).analyze(_write_sample(tmp_path))
    assert result.evidence is None
    assert result.artifacts == ()
    assert [skip.kind for skip in result.skips] == ["hook"]
    assert result.skips[0].plugin == "test.refuser"


class Hooker(Analyzer):
    name = "test.hooker"
    targets = ("text/plain",)

    def on_artifact(self, artifact: Artifact, ctx: Context) -> None:
        if artifact.kind is ArtifactKind.BYTES:
            ctx.text("hook/out", "from-hook")


def test_on_artifact_hook_emits_artifacts(tmp_path: Path) -> None:
    result = _engine([Hooker]).analyze(_write_sample(tmp_path))
    assert result.plugin_runs == 0
    text = next(a for a in result.artifacts if a.name == "hook/out")
    assert text.producer == "test.hooker"
    assert text.depth == 1
    assert text.text == "from-hook"


class PostScan(Analyzer):
    name = "test.post_scan"
    targets = ("*/*",)
    observations: ClassVar[list[int]] = []

    def post_scan(self, result: ScanResult) -> None:
        type(self).observations.append(len(result.artifacts))


def test_post_scan_hook_runs(tmp_path: Path) -> None:
    PostScan.observations.clear()
    _engine([PostScan]).analyze(_write_sample(tmp_path))
    assert PostScan.observations == [1]


class BrokenHook(Analyzer):
    name = "test.broken_hook"
    targets = ("*/*",)

    def on_artifact(self, artifact: Artifact, ctx: Context) -> None:
        raise RuntimeError("hook exploded")


def test_hook_errors_are_isolated(tmp_path: Path) -> None:
    result = _engine([BrokenHook]).analyze(_write_sample(tmp_path))
    assert result.root is not None
    assert [skip.kind for skip in result.skips] == ["hook"]
    assert "hook exploded" in result.skips[0].reason


def test_store_persistence(tmp_path: Path) -> None:
    root = tmp_path / "out"
    store = ArtifactStore(root=root)
    _engine([ToText], store=store).analyze(_write_sample(tmp_path))
    artifact = store.all()[0]
    path = store.path_for(artifact.sha256)
    assert path is not None
    assert path.read_bytes() == artifact.payload
    assert (root / "index.jsonl").is_file()


def test_engine_reuse_keeps_results_isolated(tmp_path: Path) -> None:
    first_path = tmp_path / "first.bin"
    first_path.write_bytes(b"first ctf{one}")
    second_path = tmp_path / "second.bin"
    second_path.write_bytes(b"second ctf{two}")
    store = ArtifactStore()
    engine = _engine([ToText, Finder], store=store)

    first = engine.analyze(first_path)
    second = engine.analyze(second_path)

    assert first.root is not None and second.root is not None
    assert first.root.sha256 != second.root.sha256
    first_hashes = {artifact.sha256 for artifact in first.artifacts}
    second_hashes = {artifact.sha256 for artifact in second.artifacts}
    assert first_hashes.isdisjoint(second_hashes)
    assert len(second.artifacts) == 3
    assert len(store) == 6


def test_engine_reuse_reanalyses_with_shared_store(tmp_path: Path) -> None:
    path = _write_sample(tmp_path)
    store = ArtifactStore()
    engine = _engine([ToText], store=store)

    first = engine.analyze(path)
    second = engine.analyze(path)

    assert len(first.artifacts) == 2
    assert len(second.artifacts) == 2
    assert len(store) == 2


def test_events_are_emitted(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    registry = PluginRegistry()
    registry.register(ToText, origin="test")
    engine = Engine(
        Config(plugin_dirs=()),
        registry=registry,
        on_event=lambda name, payload: events.append((name, dict(payload))),
    )
    engine.analyze(_write_sample(tmp_path))
    names = [name for name, _ in events]
    assert names[0] == "scan_start"
    assert names[-1] == "scan_end"
    assert "artifact" in names
    assert "plugin_start" in names
    assert "plugin_end" in names


def test_builtin_strings_plugin_pipeline(tmp_path: Path) -> None:
    registry = PluginRegistry()
    registry.load_builtin()
    engine = Engine(Config(plugin_dirs=()), registry=registry)
    result = engine.analyze(_write_sample(tmp_path, b"\x00hello ctf{strings}\x00"))
    texts = result.by_kind(ArtifactKind.TEXT)
    assert texts
    assert any("ctf{strings}" in artifact.text for artifact in texts)
    assert any(artifact.producer == "generic.strings" for artifact in texts)
