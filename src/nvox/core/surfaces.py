"""Text surfaces shared by plugins and the flag engine.

A *surface* is a view of an artifact payload that can be searched without
changing the artifact: raw bytes, ASCII runs, or UTF-16 runs. Every extracted
run keeps its absolute byte offset so a finding can point back to the exact
place in the stored evidence.

The extractors are deliberately cheap and deterministic. They are not charset
detectors: ASCII and UTF-16 are the two encodings that matter for CTF flags and
cover metadata, strings dumps and bit-plane artifacts.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

_PRINTABLE = frozenset(range(0x20, 0x7F))
_TAB = 0x09


@dataclass(frozen=True)
class TextRun:
    """A contiguous printable run and its offset in the source payload."""

    offset: int
    text: str
    encoding: str


def ascii_runs(data: bytes, min_length: int = 4, *, include_tab: bool = True) -> Iterator[TextRun]:
    """Yield printable ASCII runs of at least ``min_length`` characters."""
    minimum = max(1, min_length)
    start: int | None = None
    for index, byte in enumerate(data):
        printable = byte in _PRINTABLE or (include_tab and byte == _TAB)
        if printable:
            if start is None:
                start = index
            continue
        if start is not None:
            if index - start >= minimum:
                yield TextRun(start, data[start:index].decode("ascii", errors="replace"), "ascii")
            start = None
    if start is not None and len(data) - start >= minimum:
        yield TextRun(start, data[start:].decode("ascii", errors="replace"), "ascii")


def utf16_runs(
    data: bytes,
    min_length: int = 4,
    *,
    byte_order: str = "little",
) -> Iterator[TextRun]:
    """Yield ASCII-subset UTF-16 runs.

    Both byte alignments are tried, because a UTF-16 payload may start at an
    odd offset inside a binary container. The offset always points at the first
    byte of the run in ``data``.
    """
    if byte_order not in {"little", "big"}:
        raise ValueError(f"unsupported byte order: {byte_order!r}")
    minimum = max(1, min_length)
    encoding = "utf-16-le" if byte_order == "little" else "utf-16-be"
    little = byte_order == "little"

    for alignment in (0, 1):
        index = alignment
        start: int | None = None
        chars: list[str] = []
        while index + 1 < len(data):
            first = data[index]
            second = data[index + 1]
            codepoint = (second << 8) | first if little else (first << 8) | second
            if codepoint in _PRINTABLE:
                if start is None:
                    start = index
                chars.append(chr(codepoint))
            else:
                if start is not None and len(chars) >= minimum:
                    yield TextRun(start, "".join(chars), encoding)
                start = None
                chars.clear()
            index += 2
        if start is not None and len(chars) >= minimum:
            yield TextRun(start, "".join(chars), encoding)


def text_runs(
    data: bytes,
    min_length: int = 4,
    *,
    utf16: bool = True,
    include_tab: bool = True,
) -> Iterator[TextRun]:
    """Yield every supported text surface in a stable order."""
    yield from ascii_runs(data, min_length, include_tab=include_tab)
    if utf16:
        yield from utf16_runs(data, min_length, byte_order="little")
        yield from utf16_runs(data, min_length, byte_order="big")


__all__ = ["TextRun", "ascii_runs", "text_runs", "utf16_runs"]
