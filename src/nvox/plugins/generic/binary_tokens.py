"""Extract individual encoded tokens from binary payloads.

``generic.strings`` emits one text artifact containing every printable run. The
existing decoders expect the whole artifact to be a single token, so a base64
or hex value surrounded by unrelated binary strings is usually lost. This
plugin emits each plausible token as a separate text artifact, letting the
normal decoder chain decode it with provenance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from nvox.api import Analyzer, Artifact, ArtifactKind, Context, PluginKind, register
from nvox.core.surfaces import TextRun, ascii_runs, utf16_runs

_BASE64 = re.compile(r"[A-Za-z0-9+/]+={0,2}")
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+={0,2}")
_BASE85 = re.compile(r"[0-9A-Za-z!#$%&()*+;<=>?@^_`{|}~-]+")
_HEX = re.compile(r"[0-9A-Fa-f]+")
_URL_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")
_TOKEN = re.compile(r"\S+")


@dataclass(frozen=True)
class _Candidate:
    """One token worth handing to the decoder chain."""

    offset: int
    value: str
    encoding: str
    score: int


def _eligible(value: str, min_length: int) -> tuple[bool, int]:
    """Return whether ``value`` is a useful token and its rough priority.

    Plain words are deliberately not emitted. They are already covered by the
    raw flag scanner and would otherwise fill the candidate budget with text
    that no decoder can use.
    """
    if len(value) < min_length:
        return False, 0
    score = 0
    braced = "{" in value and "}" in value
    if braced:
        score += 100
    if _URL_ESCAPE.search(value):
        score += 80
    if _BASE64.fullmatch(value):
        score += 50
        if any(char in value for char in "+/="):
            score += 10
        if any(char.isdigit() for char in value):
            score += 5
    elif _BASE64URL.fullmatch(value):
        score += 45
    if _HEX.fullmatch(value) and len(value) % 2 == 0:
        score += 40
    if _BASE85.fullmatch(value) and any(char in value for char in "!#$%&()*+;<=>?@^_`{|}~"):
        score += 35
    return score > 0, score


def _runs(artifact: Artifact, min_length: int, *, utf16: bool) -> tuple[TextRun, ...]:
    """Return printable runs in the order the token extractor should inspect."""
    runs = list(ascii_runs(artifact.payload, min_length))
    if utf16:
        for byte_order in ("little", "big"):
            runs.extend(utf16_runs(artifact.payload, min_length, byte_order=byte_order))
    return tuple(runs)


def _token_step(run: TextRun) -> int:
    """Return the byte stride for a character offset inside ``run``."""
    return 2 if run.encoding.startswith("utf-16") else 1


def _collect(
    artifact: Artifact,
    *,
    min_length: int,
    max_token_bytes: int,
    utf16: bool,
) -> list[_Candidate]:
    seen: set[str] = set()
    candidates: list[_Candidate] = []
    for run in _runs(artifact, min_length, utf16=utf16):
        step = _token_step(run)
        for match in _TOKEN.finditer(run.text):
            value = match.group(0)
            if value in seen or len(value.encode("utf-8")) > max_token_bytes:
                continue
            eligible, score = _eligible(value, min_length)
            if not eligible:
                continue
            seen.add(value)
            candidates.append(
                _Candidate(
                    offset=run.offset + match.start() * step,
                    value=value,
                    encoding=run.encoding,
                    score=score,
                )
            )
    return candidates


@register
class BinaryTokenPlugin(Analyzer):
    """Split binary strings into encoded tokens for the decoder chain."""

    name: ClassVar[str] = "generic.binary_tokens"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("*/*",)
    priority: ClassVar[int] = 87
    description: ClassVar[str] = (
        "Extract individual base, hex and braced tokens from binary payloads."
    )
    options: ClassVar[dict[str, object]] = {
        "max_input_bytes": 8_000_000,
        "min_token_length": 8,
        "max_token_bytes": 4096,
        "max_tokens": 64,
        "utf16": True,
    }

    def accepts(self, artifact: Artifact) -> bool:
        return artifact.kind is ArtifactKind.BYTES and super().accepts(artifact)

    def run(self, artifact: Artifact, ctx: Context) -> None:
        payload = artifact.payload
        if not payload:
            ctx.skip("binary_tokens: empty payload")
        max_input = max(1, int(ctx.option("max_input_bytes", 8_000_000)))
        if len(payload) > max_input:
            ctx.skip(f"binary_tokens: input exceeds max_input_bytes ({max_input})")
        min_length = max(1, int(ctx.option("min_token_length", 8)))
        max_token_bytes = max(1, int(ctx.option("max_token_bytes", 4096)))
        max_tokens = max(1, int(ctx.option("max_tokens", 64)))
        candidates = _collect(
            artifact,
            min_length=min_length,
            max_token_bytes=max_token_bytes,
            utf16=bool(ctx.option("utf16", True)),
        )
        if not candidates:
            ctx.skip("binary_tokens: no encoded tokens found")
        candidates.sort(key=lambda candidate: (-candidate.score, candidate.offset))
        for index, candidate in enumerate(candidates[:max_tokens]):
            ctx.text(
                f"binary/token/{index:03d}",
                candidate.value,
                offset=candidate.offset,
                encoding=candidate.encoding,
                score=candidate.score,
                source="binary-token",
            )


__all__ = ["BinaryTokenPlugin"]
