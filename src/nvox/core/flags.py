"""Flag format compiler and surface scanner.

A format is a literal pattern with exactly one ``{}`` placeholder, for example
``ctf{}``. The placeholder matches a non-empty, single-line body. For anything
else, ``--flag`` supplies a raw regular expression.

The scanner searches raw bytes and text runs (ASCII, UTF-16LE/BE). It does not
decode: decoders are plugins that emit new artifacts, so every decoded layer
gets normal provenance and is scanned in turn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nvox.api import Artifact, ArtifactKind
from nvox.core.findings import Finding, better_finding
from nvox.core.surfaces import TextRun, text_runs


class FlagFormatError(ValueError):
    """Raised for an invalid flag format or regular expression."""


@dataclass(frozen=True)
class FlagSpec:
    """One compiled flag pattern (format or raw regular expression)."""

    label: str
    text_pattern: re.Pattern[str]
    byte_pattern: re.Pattern[bytes]
    min_length: int
    max_length: int
    is_regex: bool = False


def compile_format(
    fmt: str,
    *,
    ignore_case: bool = False,
    min_length: int = 4,
    max_length: int = 200,
) -> FlagSpec:
    """Compile ``prefix{}suffix`` into text and byte patterns."""
    if not fmt:
        raise FlagFormatError("flag format must not be empty")
    if fmt.count("{}") != 1:
        raise FlagFormatError(
            f"invalid flag format {fmt!r}: exactly one '{{}}' placeholder is required"
        )
    _validate_lengths(min_length, max_length)
    prefix, suffix = fmt.split("{}")
    if not prefix and not suffix:
        raise FlagFormatError("flag format must contain literal text outside '{}'")

    body = rf"[^\r\n]{{{min_length},{max_length}}}?"
    # The braces around ``{}`` are part of the flag syntax: ``ctf{}`` matches
    # ``ctf{...}``, not ``ctf...``.
    pattern = rf"{re.escape(prefix)}\{{(?P<body>{body})\}}{re.escape(suffix)}"
    flags = re.IGNORECASE if ignore_case else 0
    text_pattern = re.compile(pattern, flags)
    byte_pattern = _compile_bytes(text_pattern, fmt)
    return FlagSpec(
        label=fmt,
        text_pattern=text_pattern,
        byte_pattern=byte_pattern,
        min_length=min_length,
        max_length=max_length,
    )


def compile_regex(expression: str, *, ignore_case: bool = False) -> FlagSpec:
    """Compile a user-supplied flag regular expression."""
    if not expression:
        raise FlagFormatError("flag regular expression must not be empty")
    flags = re.IGNORECASE if ignore_case else 0
    try:
        text_pattern = re.compile(expression, flags)
    except re.error as exc:
        raise FlagFormatError(f"invalid flag regular expression {expression!r}: {exc}") from exc
    byte_pattern = _compile_bytes(text_pattern, expression)
    return FlagSpec(
        label=expression,
        text_pattern=text_pattern,
        byte_pattern=byte_pattern,
        min_length=1,
        max_length=0,
        is_regex=True,
    )


def build_flag_specs(
    formats: tuple[str, ...] | list[str] = (),
    patterns: tuple[str, ...] | list[str] = (),
    *,
    ignore_case: bool = False,
    min_length: int = 4,
    max_length: int = 200,
) -> tuple[FlagSpec, ...]:
    """Compile all requested formats and raw patterns."""
    specs = [
        compile_format(
            fmt,
            ignore_case=ignore_case,
            min_length=min_length,
            max_length=max_length,
        )
        for fmt in formats
    ]
    specs.extend(compile_regex(pattern, ignore_case=ignore_case) for pattern in patterns)
    if not specs:
        raise FlagFormatError("no flag formats or patterns were provided")
    return tuple(specs)


class FlagScanner:
    """Search one artifact's raw and text surfaces for flags."""

    def __init__(
        self,
        specs: tuple[FlagSpec, ...] | list[FlagSpec],
        *,
        context_chars: int = 40,
        utf16: bool = True,
        min_string_run: int = 4,
        max_findings: int = 500,
    ) -> None:
        if not specs:
            raise ValueError("FlagScanner requires at least one FlagSpec")
        self.specs = tuple(specs)
        self.context_chars = max(0, context_chars)
        self.utf16 = utf16
        self.min_string_run = max(1, min_string_run)
        self.max_findings = max(1, max_findings)

    def scan(self, artifact: Artifact) -> tuple[Finding, ...]:
        """Return deduplicated findings for one artifact."""
        payload = artifact.payload
        best: dict[tuple[str, int], Finding] = {}
        raw_surface, raw_confidence = _base_surface(artifact)

        for spec in self.specs:
            for byte_match in spec.byte_pattern.finditer(payload):
                value = _match_value(byte_match, binary=True)
                if not self._valid(spec, value):
                    continue
                finding = Finding(
                    value=value,
                    format=spec.label,
                    surface=raw_surface,
                    offset=byte_match.start(),
                    context=self._byte_context(payload, byte_match.start(), byte_match.end()),
                    artifact=artifact.sha256,
                    confidence=raw_confidence,
                )
                _keep(best, finding)
                if len(best) >= self.max_findings:
                    return _sorted(best)

        for run in text_runs(payload, self.min_string_run, utf16=self.utf16):
            surface, confidence = _text_surface(artifact, raw_surface, raw_confidence)
            for spec in self.specs:
                for text_match in spec.text_pattern.finditer(run.text):
                    value = _match_value(text_match, binary=False)
                    if not self._valid(spec, value):
                        continue
                    offset = _run_offset(run, text_match.start())
                    finding = Finding(
                        value=value,
                        format=spec.label,
                        surface=surface,
                        offset=offset,
                        context=self._text_context(run.text, text_match.start(), text_match.end()),
                        artifact=artifact.sha256,
                        confidence=confidence,
                    )
                    _keep(best, finding)
                    if len(best) >= self.max_findings:
                        return _sorted(best)
        return _sorted(best)

    def _valid(self, spec: FlagSpec, value: str) -> bool:
        if not value:
            return False
        if spec.is_regex:
            return True
        return spec.min_length <= len(value) <= spec.max_length

    def _byte_context(self, payload: bytes, start: int, end: int) -> str:
        left = max(0, start - self.context_chars)
        right = min(len(payload), end + self.context_chars)
        return _clean_context(payload[left:right].decode("utf-8", errors="replace"))

    def _text_context(self, text: str, start: int, end: int) -> str:
        left = max(0, start - self.context_chars)
        right = min(len(text), end + self.context_chars)
        return _clean_context(text[left:right])


def _compile_bytes(pattern: re.Pattern[str], label: str) -> re.Pattern[bytes]:
    # ``re.UNICODE`` is implicit for str patterns and illegal for bytes.
    flags = pattern.flags & ~re.UNICODE
    try:
        return re.compile(pattern.pattern.encode("utf-8"), flags)
    except (UnicodeEncodeError, re.error) as exc:
        raise FlagFormatError(f"flag pattern {label!r} is not byte-searchable: {exc}") from exc


def _validate_lengths(min_length: int, max_length: int) -> None:
    if min_length < 1:
        raise FlagFormatError("min_length must be at least 1")
    if max_length < min_length:
        raise FlagFormatError("max_length must be greater than or equal to min_length")


def _match_value(match: re.Match[str] | re.Match[bytes], *, binary: bool) -> str:
    group: str | bytes
    try:
        group = match.group("body")
    except IndexError:
        group = match.group(0)
    if isinstance(group, bytes):
        return group.decode("utf-8", errors="replace") if binary else group.decode("utf-8")
    return group


def _base_surface(artifact: Artifact) -> tuple[str, str]:
    if artifact.producer == "decoder.xor":
        return "xor", "medium"
    if artifact.kind is ArtifactKind.META:
        return "metadata", "high"
    if artifact.kind is ArtifactKind.TEXT:
        return "text", "high"
    if artifact.depth > 0:
        return "bitstream", "medium"
    return "raw", "low"


def _text_surface(artifact: Artifact, base: str, confidence: str) -> tuple[str, str]:
    if base in {"metadata", "text", "xor", "bitstream"}:
        return base, confidence
    return "strings", "medium"


def _run_offset(run: TextRun, character_index: int) -> int:
    step = 1 if run.encoding == "ascii" else 2
    return run.offset + character_index * step


def _keep(best: dict[tuple[str, int], Finding], candidate: Finding) -> None:
    key = (candidate.value, candidate.offset)
    best[key] = better_finding(best.get(key), candidate)


def _sorted(best: dict[tuple[str, int], Finding]) -> tuple[Finding, ...]:
    return tuple(sorted(best.values(), key=lambda item: (item.offset, item.value, item.surface)))


def _clean_context(value: str) -> str:
    return " ".join(value.replace("\r", " ").replace("\n", " ").replace("\t", " ").split())


__all__ = [
    "FlagFormatError",
    "FlagScanner",
    "FlagSpec",
    "build_flag_specs",
    "compile_format",
    "compile_regex",
]
