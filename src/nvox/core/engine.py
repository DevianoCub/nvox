"""Scan orchestration: artifact graph, scheduling, limits and provenance."""

from __future__ import annotations

import json
import logging
import shutil
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from nvox.api import (
    Artifact,
    ArtifactKind,
    Context,
    Plugin,
    PluginSkip,
    PluginTimeout,
)
from nvox.core.config import Config
from nvox.core.evidence import Evidence, EvidenceError, EvidenceTooLarge
from nvox.core.findings import Finding, better_finding, build_chain, finding_rank
from nvox.core.flags import FlagScanner
from nvox.core.hooks import HookManager, HookOutcome
from nvox.core.registry import PluginRegistry
from nvox.core.store import ArtifactStore, ArtifactStoreError

SkipKind = Literal["hook", "input", "disabled", "requirement", "timeout", "error", "plugin"]
EventSink = Callable[[str, Mapping[str, Any]], None]

MAX_PLUGIN_RUNS = 10000
MAX_FINDINGS = 1000


@dataclass(frozen=True)
class Skip:
    """A plugin invocation that did not run or did not complete."""

    plugin: str
    artifact: str
    reason: str
    kind: SkipKind
    version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin": self.plugin,
            "version": self.version,
            "artifact": self.artifact,
            "reason": self.reason,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class ScanResult:
    """Outcome of one evidence scan."""

    evidence: Evidence | None
    artifacts: tuple[Artifact, ...]
    skips: tuple[Skip, ...]
    duration: float
    plugin_runs: int
    findings: tuple[Finding, ...] = ()

    @property
    def root(self) -> Artifact | None:
        """The depth-0 evidence artifact, if ingestion succeeded."""
        for artifact in self.artifacts:
            if artifact.depth == 0:
                return artifact
        return None

    def find(self, sha_prefix: str) -> Artifact | None:
        """Find an artifact by full hash or unique prefix."""
        for artifact in self.artifacts:
            if artifact.sha256.startswith(sha_prefix):
                return artifact
        return None

    def by_kind(self, kind: ArtifactKind) -> tuple[Artifact, ...]:
        return tuple(artifact for artifact in self.artifacts if artifact.kind is kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence": (
                None
                if self.evidence is None
                else {
                    "path": str(self.evidence.path),
                    "sha256": self.evidence.sha256,
                    "size": self.evidence.size,
                    "media_type": self.evidence.media_type,
                }
            ),
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "skips": [skip.to_dict() for skip in self.skips],
            "findings": [finding.to_dict() for finding in self.findings],
            "duration": self.duration,
            "plugin_runs": self.plugin_runs,
        }


class Engine:
    """Runs the plugin graph over a single evidence file."""

    def __init__(
        self,
        config: Config,
        *,
        registry: PluginRegistry | None = None,
        store: ArtifactStore | None = None,
        hooks: HookManager | None = None,
        jobs: int | None = None,
        logger: logging.Logger | None = None,
        on_event: EventSink | None = None,
        flag_scanner: FlagScanner | None = None,
    ) -> None:
        self.config = config
        self.registry = registry if registry is not None else PluginRegistry().load_all(config)
        self.store = (
            store
            if store is not None
            else ArtifactStore(max_bytes=config.limits.max_artifact_bytes)
        )
        self.hooks = hooks if hooks is not None else HookManager(self.registry.plugins)
        self.jobs = jobs if jobs is not None else config.limits.effective_jobs()
        self.log = logger if logger is not None else logging.getLogger("nvox.engine")
        self._events = on_event
        self.flag_scanner = flag_scanner
        self._visited: set[tuple[str, str, str]] = set()
        self._blockers: dict[str, Skip] = {}
        self._hooks_done: set[str] = set()
        self._scan_artifacts: dict[str, Artifact] = {}
        self._enqueued: set[str] = set()
        self._scanned: set[tuple[str, str, tuple[str, ...], int]] = set()
        self._findings: dict[str, Finding] = {}
        self._evidence_sha256 = ""
        self._evidence_path = ""

    def analyze(self, path: Path) -> ScanResult:
        """Ingest ``path`` and run the plugin graph over it."""
        started = time.monotonic()
        skips: list[Skip] = []
        runs = 0
        self._visited.clear()
        self._blockers.clear()
        self._hooks_done.clear()
        self._scan_artifacts.clear()
        self._enqueued.clear()
        self._scanned.clear()
        self._findings.clear()
        self._evidence_sha256 = ""
        self._evidence_path = ""

        self._event("scan_start", {"path": str(path)})
        refused = self.hooks.pre_ingest(Path(path))
        if refused is not None:
            hook_name, reason = refused
            skips.append(Skip(plugin=hook_name, artifact="", reason=reason, kind="hook"))
            return self._finish(None, skips, 0, started)

        try:
            evidence = Evidence.from_path(path, max_bytes=self.config.limits.max_artifact_bytes)
        except EvidenceTooLarge as exc:
            skips.append(Skip(plugin="", artifact="", reason=str(exc), kind="input"))
            return self._finish(None, skips, 0, started)
        except EvidenceError as exc:
            skips.append(Skip(plugin="", artifact="", reason=str(exc), kind="input"))
            return self._finish(None, skips, 0, started)

        root = evidence.to_artifact()
        stored_root, _ = self.store.add(root)
        self._evidence_sha256 = evidence.sha256
        self._evidence_path = str(evidence.path)
        self._scan_artifacts[stored_root.sha256] = stored_root
        self._enqueued.add(stored_root.sha256)
        self._event("artifact", stored_root.to_dict())
        self._scan_findings(stored_root, skips)
        queue: deque[Artifact] = deque([stored_root])

        executor = ThreadPoolExecutor(max_workers=max(1, self.jobs))
        budget_exhausted = False
        try:
            while queue and not budget_exhausted:
                artifact = queue.popleft()
                self._run_artifact_hooks(artifact, queue, skips)
                if artifact.depth >= self.config.limits.max_depth:
                    continue
                for plugin in self.registry.matching(artifact):
                    if not self._implements_run(plugin):
                        continue
                    blocker = self._blocker(plugin)
                    if blocker is not None:
                        self._remember_blocker(plugin.name, blocker, artifact, skips)
                        continue
                    options = self._options(plugin)
                    key = (artifact.sha256, plugin.name, self._options_key(options))
                    if key in self._visited:
                        continue
                    self._visited.add(key)
                    if runs >= MAX_PLUGIN_RUNS:
                        skips.append(
                            Skip(
                                plugin=plugin.name,
                                version=plugin.version,
                                artifact=artifact.sha256,
                                reason=f"scan exceeded {MAX_PLUGIN_RUNS} plugin runs",
                                kind="error",
                            )
                        )
                        budget_exhausted = True
                        break
                    successes = self._invoke_plugin(executor, plugin, artifact, options, skips)
                    runs += 1
                    for child in successes:
                        self._store_child(child, queue, skips)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        return self._finish(evidence, skips, runs, started)

    def _invoke_plugin(
        self,
        executor: ThreadPoolExecutor,
        plugin: Plugin,
        artifact: Artifact,
        options: Mapping[str, Any],
        skips: list[Skip],
    ) -> tuple[Artifact, ...]:
        timeout = self._timeout(plugin)
        ctx = Context(
            artifact,
            plugin.name,
            max_artifact_bytes=self.config.limits.max_artifact_bytes,
            depth=artifact.depth + 1,
            options=options,
            deadline=time.monotonic() + timeout,
        )
        started = time.monotonic()
        self._event(
            "plugin_start",
            {"plugin": plugin.name, "artifact": artifact.sha256, "depth": artifact.depth + 1},
        )
        future: Future[None] = executor.submit(self._run_plugin, plugin, artifact, ctx)
        try:
            future.result(timeout=timeout)
        except FutureTimeout:
            ctx.close()
            skips.append(
                Skip(
                    plugin=plugin.name,
                    version=plugin.version,
                    artifact=artifact.sha256,
                    reason=f"exceeded timeout of {timeout:g}s",
                    kind="timeout",
                )
            )
            self._event("plugin_skip", {"plugin": plugin.name, "reason": "timeout"})
            return ()
        except PluginSkip as exc:
            ctx.close()
            skips.append(
                Skip(
                    plugin=plugin.name,
                    version=plugin.version,
                    artifact=artifact.sha256,
                    reason=exc.reason,
                    kind="plugin",
                )
            )
            self._event(
                "plugin_skip",
                {"plugin": plugin.name, "reason": exc.reason, "kind": "plugin"},
            )
            return ()
        except PluginTimeout as exc:
            ctx.close()
            skips.append(
                Skip(
                    plugin=plugin.name,
                    version=plugin.version,
                    artifact=artifact.sha256,
                    reason=str(exc),
                    kind="timeout",
                )
            )
            self._event("plugin_skip", {"plugin": plugin.name, "reason": "timeout"})
            return ()
        except Exception as exc:
            ctx.close()
            self.log.debug(
                "plugin %s failed on artifact %s",
                plugin.name,
                artifact.sha256,
                exc_info=exc,
            )
            skips.append(
                Skip(
                    plugin=plugin.name,
                    version=plugin.version,
                    artifact=artifact.sha256,
                    reason=f"{type(exc).__name__}: {exc}",
                    kind="error",
                )
            )
            self._event(
                "plugin_skip",
                {"plugin": plugin.name, "reason": "error", "error": str(exc)},
            )
            return ()
        finally:
            ctx.close()
        self._event(
            "plugin_end",
            {
                "plugin": plugin.name,
                "artifact": artifact.sha256,
                "artifacts": len(ctx.artifacts),
                "duration": round(time.monotonic() - started, 6),
            },
        )
        return ctx.artifacts

    def _run_plugin(self, plugin: Plugin, artifact: Artifact, ctx: Context) -> None:
        result = plugin.run(artifact, ctx)
        if result is None:
            return
        if isinstance(result, Iterable) and not isinstance(result, (str, bytes, bytearray)):
            for item in result:
                if isinstance(item, Artifact):
                    ctx.adopt(item)

    def _run_artifact_hooks(
        self, artifact: Artifact, queue: deque[Artifact], skips: list[Skip]
    ) -> None:
        if artifact.sha256 in self._hooks_done:
            return
        self._hooks_done.add(artifact.sha256)

        def factory(plugin_name: str) -> Context:
            plugin = self.registry.get(plugin_name)
            options = self._options(plugin) if plugin is not None else {}
            return Context(
                artifact,
                plugin_name,
                max_artifact_bytes=self.config.limits.max_artifact_bytes,
                depth=artifact.depth + 1,
                options=options,
            )

        outcomes: tuple[HookOutcome, ...] = self.hooks.on_artifact(artifact, factory)
        for outcome in outcomes:
            if outcome.error is not None:
                skips.append(
                    Skip(
                        plugin=outcome.plugin,
                        artifact=artifact.sha256,
                        reason=outcome.error,
                        kind="hook",
                    )
                )
            for child in outcome.artifacts:
                self._store_child(child, queue, skips)

    def _store_child(
        self, child: Artifact, queue: deque[Artifact], skips: list[Skip]
    ) -> Artifact | None:
        try:
            stored, _ = self.store.add(child)
        except ArtifactStoreError as exc:
            skips.append(
                Skip(
                    plugin=child.producer,
                    artifact=child.sha256,
                    reason=str(exc),
                    kind="error",
                )
            )
            return None
        self._scan_artifacts.setdefault(stored.sha256, stored)
        self._scan_findings(child, skips)
        if stored.sha256 not in self._enqueued:
            self._enqueued.add(stored.sha256)
            queue.append(stored)
            self._event("artifact", stored.to_dict())
        return stored

    def _scan_findings(self, artifact: Artifact, skips: list[Skip]) -> None:
        scanner = self.flag_scanner
        if scanner is None:
            return
        key = (artifact.sha256, artifact.producer, artifact.parents, artifact.depth)
        if key in self._scanned:
            return
        self._scanned.add(key)
        try:
            raw_findings = scanner.scan(artifact)
        except Exception as exc:
            self.log.debug("flag scanner failed on %s", artifact.sha256, exc_info=exc)
            skips.append(
                Skip(
                    plugin="flag.scanner",
                    artifact=artifact.sha256,
                    reason=f"{type(exc).__name__}: {exc}",
                    kind="error",
                )
            )
            return
        if not raw_findings:
            return
        chain = build_chain(artifact, self.store)
        for raw in raw_findings:
            if len(self._findings) >= MAX_FINDINGS and raw.value not in self._findings:
                break
            finding = replace(
                raw,
                chain=chain,
                evidence_sha256=self._evidence_sha256,
                evidence_path=self._evidence_path,
            )
            hooked = self.hooks.on_finding(finding)
            if hooked is None or not isinstance(hooked, Finding):
                continue
            if not hooked.chain:
                hooked = replace(hooked, chain=chain)
            if not hooked.evidence_sha256:
                hooked = replace(
                    hooked,
                    evidence_sha256=self._evidence_sha256,
                    evidence_path=self._evidence_path,
                )
            self._findings[hooked.value] = better_finding(self._findings.get(hooked.value), hooked)
            self._event("finding", hooked.to_dict())

    def _blocker(self, plugin: Plugin) -> Skip | None:
        if plugin.name in self.config.disabled_plugins:
            return Skip(
                plugin=plugin.name,
                version=plugin.version,
                artifact="",
                reason="disabled by configuration",
                kind="disabled",
            )
        disabled = [tool for tool in plugin.requires if tool in self.config.disabled_tools]
        if disabled:
            return Skip(
                plugin=plugin.name,
                version=plugin.version,
                artifact="",
                reason=f"external tool disabled by configuration: {', '.join(disabled)}",
                kind="disabled",
            )
        missing = [tool for tool in plugin.requires if shutil.which(tool) is None]
        if missing:
            return Skip(
                plugin=plugin.name,
                version=plugin.version,
                artifact="",
                reason=f"missing external tool: {', '.join(missing)}",
                kind="requirement",
            )
        return None

    def _remember_blocker(
        self, plugin_name: str, blocker: Skip, artifact: Artifact, skips: list[Skip]
    ) -> None:
        if plugin_name in self._blockers:
            return
        self._blockers[plugin_name] = replace(blocker, artifact=artifact.sha256)
        skips.append(self._blockers[plugin_name])
        self._event(
            "plugin_skip",
            {
                "plugin": plugin_name,
                "reason": blocker.reason,
                "kind": blocker.kind,
            },
        )

    def _options(self, plugin: Plugin) -> dict[str, Any]:
        merged = dict(plugin.options or {})
        merged.update(self.config.plugin_options.get(plugin.name, {}))
        return merged

    @staticmethod
    def _options_key(options: Mapping[str, Any]) -> str:
        return json.dumps(options, sort_keys=True, default=str)

    def _timeout(self, plugin: Plugin) -> float:
        return float(plugin.timeout or self.config.limits.timeout)

    @staticmethod
    def _implements_run(plugin: Plugin) -> bool:
        return getattr(type(plugin), "run", None) is not Plugin.run

    def _finish(
        self,
        evidence: Evidence | None,
        skips: list[Skip],
        runs: int,
        started: float,
    ) -> ScanResult:
        result = ScanResult(
            evidence=evidence,
            artifacts=tuple(
                sorted(
                    self._scan_artifacts.values(),
                    key=lambda item: (item.depth, item.name, item.sha256),
                )
            ),
            skips=tuple(sorted(skips, key=lambda skip: (skip.kind, skip.plugin, skip.artifact))),
            duration=time.monotonic() - started,
            plugin_runs=runs,
            findings=tuple(sorted(self._findings.values(), key=finding_rank)),
        )
        post_errors = self.hooks.post_scan(result)
        if post_errors:
            extra = tuple(
                Skip(
                    plugin=outcome.plugin,
                    artifact="",
                    reason=outcome.error or "hook error",
                    kind="hook",
                )
                for outcome in post_errors
                if outcome.error is not None
            )
            result = replace(result, skips=result.skips + extra)
        self._event(
            "scan_end",
            {
                "artifacts": len(result.artifacts),
                "skips": len(result.skips),
                "findings": len(result.findings),
                "duration": round(result.duration, 6),
            },
        )
        return result

    def _event(self, name: str, payload: Mapping[str, Any]) -> None:
        self.log.debug(
            "nvox event: %s",
            name,
            extra={"nvox_event": {"name": name, "data": dict(payload)}},
        )
        if self._events is None:
            return
        try:
            self._events(name, payload)
        except Exception:
            self.log.debug("event sink failed for %s", name, exc_info=True)


__all__ = ["Engine", "ScanResult", "Skip", "SkipKind"]
