"""Tests for flag format compilation, scanning and findings."""

from __future__ import annotations

import json

import pytest

from nvox.api import ArtifactKind, make_artifact
from nvox.core.findings import (
    ChainStep,
    Finding,
    better_finding,
    build_chain,
)
from nvox.core.flags import (
    FlagFormatError,
    FlagScanner,
    FlagSpec,
    build_flag_specs,
    compile_format,
    compile_regex,
)
from nvox.core.store import ArtifactStore


def _scan(
    payload: bytes,
    *specs: FlagSpec,
    kind: ArtifactKind = ArtifactKind.BYTES,
) -> tuple[Finding, ...]:
    artifact = make_artifact(kind=kind, name="sample", payload=payload)
    return FlagScanner(tuple(specs)).scan(artifact)


def test_format_matches_braced_body() -> None:
    spec = compile_format("ctf{}")
    findings = _scan(b"prefix ctf{abcd} suffix", spec)
    assert [finding.value for finding in findings] == ["abcd"]
    assert findings[0].offset == 7
    assert findings[0].surface == "strings"
    assert "ctf{abcd}" in findings[0].context


def test_format_is_case_sensitive_by_default() -> None:
    spec = compile_format("ctf{}")
    assert _scan(b"CTF{abcd}", spec) == ()
    insensitive = compile_format("ctf{}", ignore_case=True)
    assert [finding.value for finding in _scan(b"CTF{abcd}", insensitive)] == ["abcd"]


def test_format_min_and_max_body_length() -> None:
    spec = compile_format("ctf{}", min_length=4, max_length=8)
    assert _scan(b"ctf{abc}", spec) == ()
    assert [finding.value for finding in _scan(b"ctf{abcd}", spec)] == ["abcd"]
    assert _scan(b"ctf{abcdefghi}", spec) == ()


def test_format_does_not_cross_newline() -> None:
    spec = compile_format("ctf{}")
    assert _scan(b"ctf{abc\ndefg}", spec) == ()


def test_multiple_formats_and_raw_regex() -> None:
    specs = build_flag_specs(
        ["ctf{}", "htb{}"],
        [r"FLAG\{[A-Za-z0-9_]{3,}\}"],
    )
    payload = b"ctf{one_flag} htb{two_flag} FLAG{three}"
    values = [finding.value for finding in _scan(payload, *specs)]
    assert values == ["one_flag", "two_flag", "FLAG{three}"]


def test_raw_regex_case_insensitive() -> None:
    spec = compile_regex(r"FLAG\{[a-z]+\}", ignore_case=True)
    assert [finding.value for finding in _scan(b"flag{abc}", spec)] == ["flag{abc}"]


def test_invalid_formats_raise() -> None:
    with pytest.raises(FlagFormatError):
        compile_format("")
    with pytest.raises(FlagFormatError):
        compile_format("ctf")
    with pytest.raises(FlagFormatError):
        compile_format("ctf{}{}")
    with pytest.raises(FlagFormatError):
        compile_format("{}")
    with pytest.raises(FlagFormatError):
        compile_format("ctf{}", min_length=0)
    with pytest.raises(FlagFormatError):
        compile_regex("(")
    with pytest.raises(FlagFormatError):
        build_flag_specs([], [])


def test_utf16le_and_utf16be_surfaces() -> None:
    spec = compile_format("ctf{}")
    payload = (
        b"\x00"
        + "ctf{little_endian}".encode("utf-16-le")
        + b"\x00\x00"
        + "ctf{big_endian}".encode("utf-16-be")
        + b"\x00\x00"
    )
    findings = _scan(payload, spec)
    by_value = {finding.value: finding for finding in findings}
    assert set(by_value) == {"little_endian", "big_endian"}
    assert by_value["little_endian"].offset == 1
    assert by_value["big_endian"].offset > by_value["little_endian"].offset


def test_text_metadata_and_xor_surfaces() -> None:
    spec = compile_format("ctf{}")
    text_artifact = make_artifact(
        kind=ArtifactKind.TEXT,
        name="text",
        payload=b"ctf{text_surface}",
    )
    assert FlagScanner([spec]).scan(text_artifact)[0].surface == "text"

    meta_artifact = make_artifact(
        kind=ArtifactKind.META,
        name="meta",
        payload=json.dumps({"comment": "ctf{meta_surface}"}).encode(),
    )
    assert FlagScanner([spec]).scan(meta_artifact)[0].surface == "metadata"
    assert FlagScanner([spec]).scan(meta_artifact)[0].confidence == "high"

    xor_artifact = make_artifact(
        kind=ArtifactKind.TEXT,
        name="xor",
        payload=b"ctf{xor_surface}",
        producer="decoder.xor",
        depth=1,
    )
    finding = FlagScanner([spec]).scan(xor_artifact)[0]
    assert finding.surface == "xor"
    assert finding.confidence == "medium"


def test_scanner_deduplicates_raw_and_string_surfaces() -> None:
    spec = compile_format("ctf{}")
    findings = _scan(b"xx ctf{once_only} yy", spec)
    assert len(findings) == 1
    assert findings[0].surface == "strings"


def test_chain_walks_parents_and_excludes_root() -> None:
    store = ArtifactStore()
    root = store.put(make_artifact(kind=ArtifactKind.BYTES, name="root", payload=b"root"))
    child = store.put(
        make_artifact(
            kind=ArtifactKind.BYTES,
            name="child",
            payload=b"child",
            parents=(root.sha256,),
            producer="decoder.base64",
            params={"encoding": "base64"},
            depth=1,
        )
    )
    leaf = store.put(
        make_artifact(
            kind=ArtifactKind.TEXT,
            name="leaf",
            payload=b"ctf{leaf}",
            parents=(child.sha256,),
            producer="decoder.zlib",
            depth=2,
        )
    )
    chain = build_chain(leaf, store)
    assert [step.plugin for step in chain] == ["decoder.base64", "decoder.zlib"]
    assert chain[0].params == {"encoding": "base64"}
    assert build_chain(root, store) == ()


def test_better_finding_prefers_confidence_then_shorter_chain() -> None:
    low = Finding(
        value="ctf{x}",
        format="ctf{}",
        surface="raw",
        offset=0,
        context="",
        artifact="a",
        confidence="low",
    )
    high = Finding(
        value="ctf{x}",
        format="ctf{}",
        surface="text",
        offset=10,
        context="",
        artifact="b",
        confidence="high",
    )
    assert better_finding(low, high) is high
    chained = Finding(
        value="ctf{x}",
        format="ctf{}",
        surface="text",
        offset=0,
        context="",
        artifact="c",
        confidence="high",
        chain=(ChainStep("p", "n", "s"),),
    )
    assert better_finding(high, chained) is high
