"""Bounded archive inspection and recursive extraction."""

from __future__ import annotations

import bz2
import io
import lzma
import mimetypes
import tarfile
import zipfile
import zlib
from pathlib import PurePosixPath
from typing import Any, ClassVar

from nvox.api import (
    Artifact,
    ArtifactKind,
    Context,
    Extractor,
    PluginKind,
    infer_kind,
    make_artifact,
    register,
)
from nvox.core.magic import detect

_ARCHIVE_TARGETS = (
    "application/zip",
    "application/gzip",
    "application/x-gzip",
    "application/x-bzip2",
    "application/x-xz",
    "application/x-tar",
    "application/x-7z-compressed",
    "application/vnd.rar",
    "application/x-rar-compressed",
)
_UNSUPPORTED = {
    "application/x-7z-compressed",
    "application/vnd.rar",
    "application/x-rar-compressed",
}


def _safe_member(name: str) -> tuple[str, str | None]:
    cleaned = name.replace("\\", "/").lstrip("/")
    path = PurePosixPath(cleaned)
    if not cleaned or any(part in {"..", ""} for part in path.parts):
        return cleaned, "unsafe path"
    return cleaned, None


def _media_type_for(name: str, data: bytes) -> str:
    signature = detect(data)
    if signature is not None:
        return signature.media_type
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


class ArchiveError(ValueError):
    """Raised when an archive cannot be inspected."""


def _emit_entry(
    ctx: Context,
    *,
    name: str,
    data: bytes,
    params: dict[str, Any] | None = None,
) -> Artifact:
    media_type = _media_type_for(name, data)
    return ctx.adopt(
        make_artifact(
            kind=infer_kind(media_type),
            name=f"archive/{name}",
            payload=data,
            media_type=media_type,
            producer=ctx.plugin_name,
            params=params or {},
            depth=ctx.depth,
        )
    )


@register
class ArchiveExtractor(Extractor):
    """Extract ZIP/TAR and single-stream compression archives without writing to disk."""

    name: ClassVar[str] = "generic.archive"
    version: ClassVar[str] = "0.1.0"
    kind: ClassVar[PluginKind] = PluginKind.EXTRACTOR
    targets: ClassVar[tuple[str, ...]] = _ARCHIVE_TARGETS
    priority: ClassVar[int] = 80
    description: ClassVar[str] = "Recursively extract ZIP/TAR/gzip/bzip2/xz archives with limits."
    options: ClassVar[dict[str, object]] = {
        "max_entries": 256,
        "max_file_bytes": 32 * 1024 * 1024,
        "max_total_bytes": 96 * 1024 * 1024,
        "max_ratio": 1000,
    }

    def accepts(self, artifact: Artifact) -> bool:
        if artifact.kind is ArtifactKind.ARCHIVE:
            return super().accepts(artifact)
        return False

    def run(self, artifact: Artifact, ctx: Context) -> None:
        if artifact.media_type.split(";", 1)[0].strip().lower() in _UNSUPPORTED:
            ctx.skip(
                "archive: 7z/RAR extraction needs an external tool; "
                "the bridge or `nvox extract` can handle it"
            )
        max_entries = max(1, int(ctx.option("max_entries", 256)))
        max_file = min(
            max(1, int(ctx.option("max_file_bytes", 32 * 1024 * 1024))),
            ctx.max_artifact_bytes,
        )
        max_total = max(1, int(ctx.option("max_total_bytes", 96 * 1024 * 1024)))
        max_ratio = max(1, int(ctx.option("max_ratio", 1000)))
        entries: list[dict[str, Any]] = []
        total = 0
        media_type = artifact.media_type.split(";", 1)[0].strip().lower()
        try:
            if media_type == "application/zip":
                self._extract_zip(
                    artifact.payload,
                    ctx,
                    max_entries=max_entries,
                    max_file=max_file,
                    max_total=max_total,
                    max_ratio=max_ratio,
                    entries=entries,
                )
            elif media_type == "application/x-tar":
                self._extract_tar(
                    artifact.payload,
                    ctx,
                    max_entries=max_entries,
                    max_file=max_file,
                    max_total=max_total,
                    entries=entries,
                )
            else:
                self._extract_stream(
                    artifact.payload,
                    media_type,
                    ctx,
                    max_file=max_file,
                    entries=entries,
                )
            total = sum(int(entry.get("size", 0)) for entry in entries if entry.get("extracted"))
        except (ArchiveError, OSError, EOFError, zipfile.BadZipFile, tarfile.TarError) as exc:
            ctx.skip(f"archive: {exc}")
        extracted = sum(1 for entry in entries if entry.get("extracted"))
        if not extracted:
            ctx.skip("archive: no extractable regular files")
        ctx.meta(
            "archive/summary",
            {
                "format": media_type,
                "entries": entries,
                "extracted": extracted,
                "total_extracted_bytes": total,
            },
            source="archive",
        )

    def _extract_zip(
        self,
        payload: bytes,
        ctx: Context,
        *,
        max_entries: int,
        max_file: int,
        max_total: int,
        max_ratio: int,
        entries: list[dict[str, Any]],
    ) -> None:
        total = 0
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos = archive.infolist()[:max_entries]
            for info in infos:
                if info.is_dir():
                    continue
                name, unsafe = _safe_member(info.filename)
                entry: dict[str, Any] = {
                    "name": name,
                    "size": info.file_size,
                    "compressed_size": info.compress_size,
                }
                if unsafe:
                    entry.update(status="skipped", reason=unsafe)
                    entries.append(entry)
                    continue
                if info.flag_bits & 0x1:
                    entry.update(status="skipped", reason="encrypted")
                    entries.append(entry)
                    continue
                if info.file_size > max_file:
                    entry.update(status="skipped", reason="file exceeds max_file_bytes")
                    entries.append(entry)
                    continue
                if (
                    info.file_size > 1024 * 1024
                    and info.compress_size > 0
                    and info.file_size / info.compress_size > max_ratio
                ):
                    entry.update(status="skipped", reason="compression ratio exceeds max_ratio")
                    entries.append(entry)
                    continue
                if total + info.file_size > max_total:
                    entry.update(status="skipped", reason="archive total budget exhausted")
                    entries.append(entry)
                    continue
                with archive.open(info) as handle:
                    data = handle.read(max_file + 1)
                if len(data) > max_file:
                    entry.update(status="skipped", reason="file exceeds max_file_bytes")
                    entries.append(entry)
                    continue
                params = {
                    "archive_member": name,
                    "compressed_size": info.compress_size,
                    "crc32": f"{info.CRC:08x}",
                }
                _emit_entry(ctx, name=name, data=data, params=params)
                total += len(data)
                entry.update(status="extracted", extracted=True, size=len(data))
                entries.append(entry)

    def _extract_tar(
        self,
        payload: bytes,
        ctx: Context,
        *,
        max_entries: int,
        max_file: int,
        max_total: int,
        entries: list[dict[str, Any]],
    ) -> None:
        total = 0
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
            members = archive.getmembers()[:max_entries]
            for member in members:
                if not member.isfile():
                    continue
                name, unsafe = _safe_member(member.name)
                entry: dict[str, Any] = {"name": name, "size": member.size}
                if unsafe:
                    entry.update(status="skipped", reason=unsafe)
                    entries.append(entry)
                    continue
                if member.size > max_file or total + member.size > max_total:
                    entry.update(status="skipped", reason="file exceeds extraction budget")
                    entries.append(entry)
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    entry.update(status="skipped", reason="unreadable member")
                    entries.append(entry)
                    continue
                data = handle.read(max_file + 1)
                if len(data) > max_file:
                    entry.update(status="skipped", reason="file exceeds max_file_bytes")
                    entries.append(entry)
                    continue
                _emit_entry(
                    ctx,
                    name=name,
                    data=data,
                    params={"archive_member": name},
                )
                total += len(data)
                entry.update(status="extracted", extracted=True, size=len(data))
                entries.append(entry)

    def _extract_stream(
        self,
        payload: bytes,
        media_type: str,
        ctx: Context,
        *,
        max_file: int,
        entries: list[dict[str, Any]],
    ) -> None:
        data = _decompress_single(payload, media_type, max_file)
        if tarfile.is_tarfile(io.BytesIO(data)):
            _emit_entry(
                ctx,
                name="archive.tar",
                data=data,
                params={"decompressed_from": media_type},
            )
            entries.append(
                {
                    "name": "archive.tar",
                    "size": len(data),
                    "status": "extracted",
                    "extracted": True,
                    "container": "tar",
                }
            )
            return
        signature = detect(data)
        extension = signature.extension if signature else "bin"
        _emit_entry(
            ctx,
            name=f"decompressed.{extension}",
            data=data,
            params={"decompressed_from": media_type},
        )
        entries.append(
            {
                "name": f"decompressed.{extension}",
                "size": len(data),
                "status": "extracted",
                "extracted": True,
            }
        )


def _decompress_single(payload: bytes, media_type: str, max_size: int) -> bytes:
    try:
        if media_type in {"application/gzip", "application/x-gzip"}:
            decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
            data = decompressor.decompress(payload, max_size + 1)
        elif media_type == "application/x-bzip2":
            data = bz2.BZ2Decompressor().decompress(payload, max_length=max_size + 1)
        elif media_type == "application/x-xz":
            data = lzma.LZMADecompressor().decompress(payload, max_length=max_size + 1)
        else:
            raise ArchiveError(f"unsupported archive type: {media_type}")
    except (OSError, EOFError, lzma.LZMAError, ValueError) as exc:
        raise ArchiveError(f"cannot decompress {media_type}: {exc}") from exc
    if len(data) > max_size:
        raise ArchiveError(f"decompressed data exceeds max_file_bytes ({max_size})")
    return data


__all__ = ["ArchiveError", "ArchiveExtractor"]
