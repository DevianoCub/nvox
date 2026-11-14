"""Tests for shared text surface extraction."""

from __future__ import annotations

from nvox.core.surfaces import ascii_runs, text_runs, utf16_runs


def test_ascii_runs_report_offsets() -> None:
    data = b"\x00\x01hello\x00world!!\x00"
    runs = list(ascii_runs(data, 4))
    assert [(run.offset, run.text, run.encoding) for run in runs] == [
        (2, "hello", "ascii"),
        (8, "world!!", "ascii"),
    ]


def test_ascii_runs_respect_min_length_and_tabs() -> None:
    assert list(ascii_runs(b"ab\x00bc", 3)) == []
    runs = list(ascii_runs(b"a\tb", 3))
    assert runs[0].text == "a\tb"


def test_utf16le_runs_report_byte_offsets() -> None:
    payload = b"\xff\xfe" + "ctf{utf16_ok}".encode("utf-16-le") + b"\x00"
    runs = list(utf16_runs(payload, 4, byte_order="little"))
    match = next(run for run in runs if "ctf{utf16_ok}" in run.text)
    assert match.encoding == "utf-16-le"
    assert payload[match.offset : match.offset + len(match.text) * 2].decode("utf-16-le") == (
        "ctf{utf16_ok}"
    )


def test_utf16be_runs_report_byte_offsets() -> None:
    payload = b"\x00\x00" + "htb{be_flag}".encode("utf-16-be") + b"\x00\x00"
    runs = list(utf16_runs(payload, 4, byte_order="big"))
    match = next(run for run in runs if "htb{be_flag}" in run.text)
    assert match.encoding == "utf-16-be"
    assert int(match.offset) == 2


def test_text_runs_cover_all_encodings() -> None:
    payload = (
        b"\x00plain_ascii\x00"
        + "pico{le_flag}".encode("utf-16-le")
        + b"\x00\x00"
        + "pico{be_flag}".encode("utf-16-be")
        + b"\x00\x00"
    )
    texts = [run.text for run in text_runs(payload, 4)]
    assert "plain_ascii" in texts
    assert "pico{le_flag}" in texts
    assert "pico{be_flag}" in texts
