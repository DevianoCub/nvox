"""Tests for M6 generic file analysis: entropy, archives, PDF and Office."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fixture_builders import (
    make_docx,
    make_gzip,
    make_pdf,
    make_tar,
    make_zip_entries,
)
from nvox.api import (
    ArtifactKind,
    Context,
    PluginSkip,
    infer_kind,
    make_artifact,
)
from nvox.core.config import Config, ScanLimits
from nvox.core.engine import Engine
from nvox.core.flags import FlagScanner, build_flag_specs
from nvox.core.registry import PluginRegistry
from nvox.core.store import ArtifactStore
from nvox.plugins.generic.archive import ArchiveExtractor
from nvox.plugins.generic.entropy import EntropyPlugin, entropy, entropy_map
from nvox.plugins.generic.office import OfficeError, OfficePlugin, parse_office
from nvox.plugins.generic.pdf import PdfError, PdfPlugin, parse_pdf
from nvox.plugins.generic.strings import StringsPlugin


def _run(
    plugin: object,
    payload: bytes,
    media_type: str,
    *,
    options: dict[str, object] | None = None,
    depth: int = 1,
) -> tuple[object, ...]:
    artifact = make_artifact(
        kind=infer_kind(media_type),
        name="fixture",
        payload=payload,
        media_type=media_type,
    )
    ctx = Context(
        artifact,
        plugin.name,  # type: ignore[attr-defined]
        max_artifact_bytes=64 * 1024 * 1024,
        depth=depth,
        options=options,
    )
    plugin.run(artifact, ctx)  # type: ignore[attr-defined]
    return ctx.artifacts


def _meta(artifacts: tuple[object, ...], name: str) -> dict[str, object]:
    artifact = next(item for item in artifacts if item.name == name)  # type: ignore[attr-defined]
    return json.loads(artifact.text)  # type: ignore[attr-defined]


def test_entropy_helpers_and_plugin_map() -> None:
    assert entropy(b"") == 0.0
    assert entropy(b"\x00" * 128) == 0.0
    assert entropy(bytes(range(256))) == pytest.approx(8.0)

    mapping = entropy_map(bytes(range(256)) * 4, window=256, step=256)
    assert mapping["overall_entropy"] == pytest.approx(8.0)
    assert mapping["high_entropy_windows"] > 0
    assert all(window["entropy"] <= 8.0 for window in mapping["windows"])

    payload = bytes(range(256)) * 8
    artifacts = _run(EntropyPlugin(), payload, "application/octet-stream")
    metadata = _meta(artifacts, "entropy/map")
    assert metadata["size"] == len(payload)
    assert metadata["overall_entropy"] == pytest.approx(8.0)
    image = next(item for item in artifacts if item.name == "entropy/heatmap.png")
    assert image.payload.startswith(b"\x89PNG\r\n\x1a\n")  # type: ignore[attr-defined]


def test_zip_entries_are_extracted_with_provenance() -> None:
    payload = make_zip_entries(
        [
            ("ctf.txt", b"ctf{zipped}"),
            ("nested/inner.bin", b"\x00\x01\x02"),
        ]
    )
    artifacts = _run(ArchiveExtractor(), payload, "application/zip")
    names = {item.name for item in artifacts}
    assert "archive/ctf.txt" in names
    assert "archive/nested/inner.bin" in names
    text = next(item for item in artifacts if item.name == "archive/ctf.txt")
    assert text.payload == b"ctf{zipped}"  # type: ignore[attr-defined]
    assert text.params["archive_member"] == "ctf.txt"  # type: ignore[attr-defined]
    summary = _meta(artifacts, "archive/summary")
    assert summary["format"] == "application/zip"
    assert summary["extracted"] == 2


def test_zip_guards_reject_unsafe_paths_and_oversized_files() -> None:
    payload = make_zip_entries(
        [
            ("../escape.txt", b"ctf{escape}"),
            ("big.bin", b"x" * 128),
            ("safe.txt", b"ctf{safe}"),
        ]
    )
    artifacts = _run(
        ArchiveExtractor(),
        payload,
        "application/zip",
        options={"max_file_bytes": 32, "max_entries": 8},
    )
    names = {item.name for item in artifacts}
    assert "archive/safe.txt" in names
    assert "archive/../escape.txt" not in names
    assert "archive/big.bin" not in names
    summary = _meta(artifacts, "archive/summary")
    entries = {entry["name"]: entry for entry in summary["entries"]}  # type: ignore[index]
    assert entries["../escape.txt"]["status"] == "skipped"
    assert entries["../escape.txt"]["reason"] == "unsafe path"
    assert entries["big.bin"]["reason"] == "file exceeds max_file_bytes"


def test_tar_and_compressed_streams_are_extracted() -> None:
    tar_payload = make_tar(
        [
            ("flag.txt", b"ctf{tar}"),
            ("folder/data.bin", b"\x00\x01"),
        ]
    )
    artifacts = _run(ArchiveExtractor(), tar_payload, "application/x-tar")
    names = {item.name for item in artifacts}
    assert "archive/flag.txt" in names
    assert "archive/folder/data.bin" in names

    gzip_payload = make_gzip(b"ctf{gzip_stream}")
    artifacts = _run(ArchiveExtractor(), gzip_payload, "application/gzip")
    child = next(item for item in artifacts if item.name.startswith("archive/decompressed."))
    assert child.payload == b"ctf{gzip_stream}"  # type: ignore[attr-defined]
    assert child.params["decompressed_from"] == "application/gzip"  # type: ignore[attr-defined]

    artifacts = _run(ArchiveExtractor(), make_gzip(tar_payload), "application/gzip")
    child = next(item for item in artifacts if item.name == "archive/archive.tar")
    assert child.media_type == "application/x-tar"  # type: ignore[attr-defined]


def test_archive_errors_become_skips() -> None:
    plugin = ArchiveExtractor()
    artifact = make_artifact(
        kind=ArtifactKind.ARCHIVE,
        name="broken.zip",
        payload=b"PK\x03\x04not really a zip",
        media_type="application/zip",
    )
    ctx = Context(artifact, plugin.name, max_artifact_bytes=1024, depth=1)
    with pytest.raises(PluginSkip, match="archive:"):
        plugin.run(artifact, ctx)

    unsupported = make_artifact(
        kind=ArtifactKind.ARCHIVE,
        name="archive.7z",
        payload=b"7z\xbc\xaf\x27\x1c",
        media_type="application/x-7z-compressed",
    )
    ctx = Context(unsupported, plugin.name, max_artifact_bytes=1024, depth=1)
    with pytest.raises(PluginSkip, match="external tool"):
        plugin.run(unsupported, ctx)


def test_recursive_archive_chain_finds_flag(tmp_path: Path) -> None:
    inner = make_zip_entries([("flag.txt", b"ctf{deep_archive}")])
    outer = make_zip_entries([("inner.zip", inner)])
    path = tmp_path / "outer.zip"
    path.write_bytes(outer)

    config = Config(
        plugin_dirs=(),
        limits=ScanLimits(max_depth=4, jobs=1, timeout=30.0),
    )
    registry = PluginRegistry()
    registry.register(ArchiveExtractor, origin="test")
    engine = Engine(
        config,
        registry=registry,
        store=ArtifactStore(),
        jobs=1,
        flag_scanner=FlagScanner(build_flag_specs(["ctf{}"])),
    )
    result = engine.analyze(path)
    assert [finding.value for finding in result.findings] == ["deep_archive"]
    assert [step.plugin for step in result.findings[0].chain] == [
        "generic.archive",
        "generic.archive",
    ]


def test_pdf_metadata_text_actions_and_trailing() -> None:
    for compress in (False, True):
        payload = make_pdf(
            text="ctf{pdf_text}",
            title="ctf{pdf_title}",
            author="ctf{pdf_author}",
            producer="Nvox",
            compress=compress,
            javascript=True,
            embedded=True,
            trailing=b"ctf{pdf_trailing}",
        )
        parsed = parse_pdf(payload)
        assert parsed["version"] == "1.4"
        assert parsed["pages"] == 1
        assert parsed["metadata"]["Title"] == "ctf{pdf_title}"
        assert parsed["text"] == "ctf{pdf_text}"
        assert parsed["javascript"]
        assert parsed["open_action"]
        assert parsed["embedded_files"] == 1
        assert parsed["trailing_bytes"] == len(b"\nctf{pdf_trailing}")

        artifacts = _run(PdfPlugin(), payload, "application/pdf")
        text = next(item for item in artifacts if item.name == "pdf/text")
        assert text.text == "ctf{pdf_text}"  # type: ignore[attr-defined]
        metadata = _meta(artifacts, "pdf/metadata")
        assert metadata["metadata"]["Author"] == "ctf{pdf_author}"  # type: ignore[index]
        trailing = next(item for item in artifacts if item.name == "pdf/trailing")
        assert trailing.payload == b"\nctf{pdf_trailing}"  # type: ignore[attr-defined]


def test_pdf_rejects_non_pdf_and_skips_empty_documents() -> None:
    with pytest.raises(PdfError):
        parse_pdf(b"not a pdf")
    plugin = PdfPlugin()
    artifact = make_artifact(
        kind=ArtifactKind.BYTES,
        name="empty.pdf",
        payload=b"%PDF-1.4\n%%EOF\n",
        media_type="application/pdf",
    )
    ctx = Context(artifact, plugin.name, max_artifact_bytes=1024, depth=1)
    with pytest.raises(PluginSkip):
        plugin.run(artifact, ctx)


def test_docx_metadata_macros_and_external_links() -> None:
    payload = make_docx(
        title="ctf{docx_title}",
        creator="ctf{docx_author}",
        company="ACME",
        macros=True,
        external_link="https://example.invalid/ctf",
    )
    parsed = parse_office(payload)
    assert parsed is not None
    assert parsed["kind"] == "docx"
    assert parsed["metadata"]["title"] == "ctf{docx_title}"
    assert parsed["metadata"]["creator"] == "ctf{docx_author}"
    assert parsed["metadata"]["Company"] == "ACME"
    assert parsed["has_macros"]
    assert parsed["external_links"] == ["https://example.invalid/ctf"]

    artifacts = _run(OfficePlugin(), payload, "application/zip")
    metadata = _meta(artifacts, "office/docx")
    assert metadata["has_macros"]
    text = next(item for item in artifacts if item.name == "office/docx/metadata")
    assert "ctf{docx_title}" in text.text  # type: ignore[attr-defined]


def test_office_ignores_plain_zip_and_rejects_bad_packages() -> None:
    assert parse_office(make_zip_entries([("readme.txt", b"not office")])) is None

    plugin = OfficePlugin()
    artifact = make_artifact(
        kind=ArtifactKind.ARCHIVE,
        name="broken.docx",
        payload=b"PK\x03\x04broken",
        media_type="application/zip",
    )
    ctx = Context(artifact, plugin.name, max_artifact_bytes=1024, depth=1)
    with pytest.raises(PluginSkip, match="office:"):
        plugin.run(artifact, ctx)

    with pytest.raises(OfficeError):
        parse_office(b"PK\x03\x04broken")


def test_strings_plugin_extracts_utf32_variants() -> None:
    payload = (
        b"\x00\x00"
        + "ctf{utf32_le}".encode("utf-32-le")
        + b"\x00\x00\x00\x00"
        + "htb{utf32_be}".encode("utf-32-be")
        + b"\x00\x00\x00\x00"
    )
    artifacts = _run(StringsPlugin(), payload, "application/octet-stream")
    text = next(item for item in artifacts if item.name == "strings")
    assert "ctf{utf32_le}" in text.text  # type: ignore[attr-defined]
    assert "htb{utf32_be}" in text.text  # type: ignore[attr-defined]
