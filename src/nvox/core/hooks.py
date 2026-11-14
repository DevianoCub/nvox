"""Hook dispatch over loaded plugins.

Hooks let plugins extend core behaviour without patching it. Dispatch is
isolated: an exception in one hook is recorded and never aborts the scan.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nvox.api import Artifact, Context, Plugin


@dataclass(frozen=True)
class HookOutcome:
    """Result of invoking one hook implementation."""

    plugin: str
    artifacts: tuple[Artifact, ...] = ()
    error: str | None = None


def _overrides(plugin: Plugin, method: str) -> bool:
    implementation = getattr(type(plugin), method, None)
    return implementation is not None and implementation is not getattr(Plugin, method)


class HookManager:
    """Dispatches hook calls to plugins that override them."""

    def __init__(self, plugins: Iterable[Plugin]) -> None:
        self._plugins = tuple(plugins)

    def implementors(self, method: str) -> tuple[Plugin, ...]:
        return tuple(plugin for plugin in self._plugins if _overrides(plugin, method))

    def pre_ingest(self, path: Path) -> tuple[str, str] | None:
        """Return ``(plugin, reason)`` when a hook refuses the input."""
        for plugin in self.implementors("pre_ingest"):
            try:
                reason = plugin.pre_ingest(path)
            except Exception as exc:
                reason = f"hook error: {type(exc).__name__}: {exc}"
            if reason:
                return plugin.name, str(reason)
        return None

    def on_artifact(
        self,
        artifact: Artifact,
        ctx_factory: Callable[[str], Context],
    ) -> tuple[HookOutcome, ...]:
        """Invoke ``on_artifact`` for every implementor."""
        outcomes: list[HookOutcome] = []
        for plugin in self.implementors("on_artifact"):
            ctx = ctx_factory(plugin.name)
            error: str | None = None
            try:
                plugin.on_artifact(artifact, ctx)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            finally:
                ctx.close()
            outcomes.append(HookOutcome(plugin=plugin.name, artifacts=ctx.artifacts, error=error))
        return tuple(outcomes)

    def post_scan(self, result: Any) -> tuple[HookOutcome, ...]:
        """Invoke ``post_scan`` for every implementor."""
        outcomes: list[HookOutcome] = []
        for plugin in self.implementors("post_scan"):
            error: str | None = None
            try:
                plugin.post_scan(result)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            outcomes.append(HookOutcome(plugin=plugin.name, error=error))
        return tuple(outcomes)

    def on_finding(self, finding: Any) -> Any:
        """Let hooks inspect or drop a finding."""
        current = finding
        for plugin in self.implementors("on_finding"):
            try:
                current = plugin.on_finding(current)
            except Exception:
                continue
            if current is None:
                return None
        return current


__all__ = ["HookManager", "HookOutcome"]
