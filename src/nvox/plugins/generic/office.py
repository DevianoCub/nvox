"""Basic OOXML (DOCX/XLSX/PPTX) package inspection."""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from typing import Any, ClassVar

from nvox.api import Analyzer, Artifact, Context, PluginKind, register

_CONTENT_TYPES = "[Content_Types].xml"
_CORE = "docProps/core.xml"
_APP = "docProps/app.xml"
_MACROS = "word/vbaProject.bin"
_OFFICE_KINDS = (
    (("word/",), "docx"),
    (("xl/",), "xlsx"),
    (("ppt/",), "pptx"),
)


class OfficeError(ValueError):
    """Raised when an OOXML package is malformed."""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_values(data: bytes, *, max_bytes: int = 4 * 1024 * 1024) -> dict[str, str]:
    if len(data) > max_bytes:
        raise OfficeError("XML part exceeds max_bytes")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise OfficeError(f"invalid XML: {exc}") from exc
    values: dict[str, str] = {}
    for element in root.iter():
        text = (element.text or "").strip()
        if text:
            values[_local_name(element.tag)] = text
    return values


def _external_link_targets(
    archive: zipfile.ZipFile,
    names: list[str],
    *,
    max_part_bytes: int,
) -> list[str]:
    targets: list[str] = []
    for name in names:
        if not name.endswith(".rels"):
            continue
        info = archive.getinfo(name)
        if info.file_size > max_part_bytes:
            continue
        try:
            root = ET.fromstring(archive.read(name))
        except ET.ParseError:
            continue
        for element in root.iter():
            if (
                _local_name(element.tag) == "Relationship"
                and element.attrib.get("TargetMode") == "External"
            ):
                target = element.attrib.get("Target")
                if target:
                    targets.append(target)
    return targets


def parse_office(payload: bytes, *, max_part_bytes: int = 4 * 1024 * 1024) -> dict[str, Any] | None:
    """Inspect an OOXML ZIP package and return its metadata summary."""
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = archive.namelist()
            if _CONTENT_TYPES not in names:
                return None
            kind = None
            for prefixes, candidate in _OFFICE_KINDS:
                if any(any(name.startswith(prefix) for prefix in prefixes) for name in names):
                    kind = candidate
                    break
            if kind is None:
                return None
            metadata: dict[str, str] = {}
            for part, label in ((_CORE, "core"), (_APP, "app")):
                if part not in names:
                    continue
                info = archive.getinfo(part)
                if info.file_size > max_part_bytes:
                    metadata[f"{label}_error"] = "part exceeds max_part_bytes"
                    continue
                metadata.update(_xml_values(archive.read(part), max_bytes=max_part_bytes))
            external_links = _external_link_targets(
                archive,
                names,
                max_part_bytes=max_part_bytes,
            )
            media = [
                name
                for name in names
                if name.startswith("word/media/") or name.startswith("ppt/media/")
            ]
            embedded = [
                name for name in names if "embeddings/" in name or name.startswith("customXml/")
            ]
            return {
                "kind": kind,
                "parts": len(names),
                "metadata": metadata,
                "has_macros": (
                    _MACROS in names or any(name.endswith("vbaProject.bin") for name in names)
                ),
                "media": media[:50],
                "embedded": embedded[:50],
                "external_links": external_links,
                "hidden_parts": [name for name in names if "hidden" in name.lower()][:50],
            }
    except zipfile.BadZipFile as exc:
        raise OfficeError(f"not a valid ZIP/OOXML package: {exc}") from exc


@register
class OfficePlugin(Analyzer):
    """Report DOCX/XLSX/PPTX metadata, macros and embedded parts."""

    name: ClassVar[str] = "generic.office"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.ANALYZER
    targets: ClassVar[tuple[str, ...]] = ("application/zip",)
    priority: ClassVar[int] = 82
    description: ClassVar[str] = "Inspect DOCX/XLSX/PPTX package metadata, macros and links."
    options: ClassVar[dict[str, object]] = {"max_part_bytes": 4 * 1024 * 1024}

    def run(self, artifact: Artifact, ctx: Context) -> None:
        try:
            parsed = parse_office(
                artifact.payload,
                max_part_bytes=max(1, int(ctx.option("max_part_bytes", 4 * 1024 * 1024))),
            )
        except OfficeError as exc:
            ctx.skip(f"office: {exc}")
        if parsed is None:
            ctx.skip("office: not an OOXML document")
        ctx.meta(f"office/{parsed['kind']}", parsed, source="ooxml")
        metadata = parsed.get("metadata", {})
        if isinstance(metadata, dict) and metadata:
            ctx.text(
                f"office/{parsed['kind']}/metadata",
                "\n".join(f"{key}: {value}" for key, value in metadata.items()),
            )


__all__ = ["OfficeError", "OfficePlugin", "parse_office"]
