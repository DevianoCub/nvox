"""Findings, confidence and provenance chains.

A finding is deliberately small: the flag value, where it was found, and the
artifact/plugin chain that produced the surface. Payloads stay in the artifact
store; reports refer to them by SHA-256.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nvox.api import Artifact

if TYPE_CHECKING:
    from nvox.core.store import ArtifactStore

CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True)
class ChainStep:
    """One transformation between evidence and the artifact that was matched."""

    plugin: str
    name: str
    sha256: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin": self.plugin,
            "name": self.name,
            "sha256": self.sha256,
            "params": dict(self.params),
        }


@dataclass(frozen=True)
class Finding:
    """A flag match with provenance and a human-readable context window."""

    value: str
    format: str
    surface: str
    offset: int
    context: str
    artifact: str
    confidence: str = "low"
    chain: tuple[ChainStep, ...] = ()
    evidence_sha256: str = ""
    evidence_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "format": self.format,
            "surface": self.surface,
            "offset": self.offset,
            "context": self.context,
            "artifact": self.artifact,
            "confidence": self.confidence,
            "chain": [step.to_dict() for step in self.chain],
            "evidence": {
                "sha256": self.evidence_sha256,
                "path": self.evidence_path,
            },
        }


def finding_rank(finding: Finding) -> tuple[int, int, int, str]:
    """Sort key: shortest provenance chain, then higher confidence and offset.

    The shortest chain matters for duplicate payloads: ``rot13(rot13(text))``
    is the same artifact as ``text``, so the direct match should win instead of
    a longer cycle that happens to end at the same bytes.
    """
    return (
        len(finding.chain),
        CONFIDENCE_RANK.get(finding.confidence, 3),
        finding.offset,
        finding.artifact,
    )


def better_finding(current: Finding | None, candidate: Finding) -> Finding:
    """Return the more useful of two findings for the same flag value."""
    if current is None:
        return candidate
    return candidate if finding_rank(candidate) < finding_rank(current) else current


def build_chain(artifact: Artifact, store: ArtifactStore) -> tuple[ChainStep, ...]:
    """Walk first-parent provenance from ``artifact`` back to the evidence root.

    The root artifact (depth 0) is evidence and is not a transformation step, so
    a finding directly on evidence has an empty chain.
    """
    steps: list[ChainStep] = []
    current = artifact
    seen: set[str] = set()
    while current.parents:
        if current.sha256 in seen:
            break
        seen.add(current.sha256)
        steps.append(
            ChainStep(
                plugin=current.producer,
                name=current.name,
                sha256=current.sha256,
                params=dict(current.params),
            )
        )
        parent = store.get(current.parents[0])
        if parent is None:
            break
        current = parent
    steps.reverse()
    return tuple(steps)


__all__ = [
    "CONFIDENCE_RANK",
    "ChainStep",
    "Finding",
    "better_finding",
    "build_chain",
    "finding_rank",
]
