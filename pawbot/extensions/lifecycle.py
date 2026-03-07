"""Lifecycle hook system for extensions (Phase E3).

Defines and dispatches lifecycle hooks:
  - on_load       — after an extension is loaded
  - on_agent_start — before the agent starts processing a message
  - on_tool_call   — before a tool call is executed
  - on_tool_result — after a tool call returns
  - on_agent_end   — after the agent finishes processing
  - on_unload      — before an extension is unloaded

Hooks are called from the correct points in ``agent/loop.py``.
Handlers are registered per-extension and dispatched in priority order.
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Any, Callable

from loguru import logger


class HookName(str, Enum):
    """Lifecycle hook events."""

    ON_LOAD = "on_load"
    ON_AGENT_START = "on_agent_start"
    ON_TOOL_CALL = "on_tool_call"
    ON_TOOL_RESULT = "on_tool_result"
    ON_AGENT_END = "on_agent_end"
    ON_UNLOAD = "on_unload"


# Type alias for hook handlers
HookHandler = Callable[..., Any]


class HookRegistration:
    """A single hook handler registration."""

    __slots__ = ("extension_id", "hook_name", "handler", "priority")

    def __init__(
        self,
        extension_id: str,
        hook_name: HookName,
        handler: HookHandler,
        priority: int = 100,
    ):
        self.extension_id = extension_id
        self.hook_name = hook_name
        self.handler = handler
        self.priority = priority

    def __repr__(self) -> str:
        return (
            f"HookRegistration({self.extension_id!r}, "
            f"{self.hook_name.value!r}, priority={self.priority})"
        )


class LifecycleDispatcher:
    """Dispatch lifecycle hooks to registered handlers.

    Handlers are called in priority order (lower = earlier).
    Errors in handlers are caught and logged, never crash the caller.
    """

    def __init__(self) -> None:
        self._hooks: dict[HookName, list[HookRegistration]] = {
            hook: [] for hook in HookName
        }
        self._sorted: dict[HookName, bool] = {hook: True for hook in HookName}

    # ── Registration ─────────────────────────────────────────────────────

    def register(
        self,
        hook_name: HookName,
        extension_id: str,
        handler: HookHandler,
        priority: int = 100,
    ) -> None:
        """Register a handler for a lifecycle hook.

        Args:
            hook_name: Which lifecycle event to listen for.
            extension_id: The extension registering this handler.
            handler: Callable to invoke.  Can be sync or async.
            priority: Lower values run first.  Default 100.
        """
        reg = HookRegistration(
            extension_id=extension_id,
            hook_name=hook_name,
            handler=handler,
            priority=priority,
        )
        self._hooks[hook_name].append(reg)
        self._sorted[hook_name] = False
        logger.debug(
            "Hook registered: {} → {} (priority {})",
            hook_name.value,
            extension_id,
            priority,
        )

    def unregister(self, extension_id: str) -> int:
        """Remove all hooks for an extension.  Returns count removed."""
        count = 0
        for hook_name in HookName:
            before = len(self._hooks[hook_name])
            self._hooks[hook_name] = [
                r for r in self._hooks[hook_name] if r.extension_id != extension_id
            ]
            count += before - len(self._hooks[hook_name])
        return count

    # ── Dispatch ─────────────────────────────────────────────────────────

    def dispatch(
        self,
        hook_name: HookName,
        *,
        extension_id: str = "",
        **kwargs: Any,
    ) -> list[Any]:
        """Dispatch a lifecycle hook synchronously.

        All registered handlers for the hook are called in priority order.
        If a handler is async, it's skipped with a warning (use
        ``dispatch_async`` instead).

        Args:
            hook_name: The hook to dispatch.
            extension_id: Optional filter — only call handlers from this ext.
            **kwargs: Passed to each handler.

        Returns:
            List of return values from handlers (None values filtered out).
        """
        self._ensure_sorted(hook_name)
        results: list[Any] = []

        for reg in self._hooks[hook_name]:
            if extension_id and reg.extension_id != extension_id:
                continue
            try:
                result = reg.handler(**kwargs)
                if asyncio.iscoroutine(result):
                    logger.warning(
                        "Async hook handler for {}:{} called synchronously — skipping",
                        hook_name.value,
                        reg.extension_id,
                    )
                    continue
                if result is not None:
                    results.append(result)
            except Exception as e:
                logger.warning(
                    "Hook {}:{} raised {}: {}",
                    hook_name.value,
                    reg.extension_id,
                    type(e).__name__,
                    e,
                )

        return results

    async def dispatch_async(
        self,
        hook_name: HookName,
        *,
        extension_id: str = "",
        **kwargs: Any,
    ) -> list[Any]:
        """Dispatch a lifecycle hook asynchronously.

        Handles both sync and async handlers.

        Args:
            hook_name: The hook to dispatch.
            extension_id: Optional filter — only call handlers from this ext.
            **kwargs: Passed to each handler.

        Returns:
            List of return values from handlers (None values filtered out).
        """
        self._ensure_sorted(hook_name)
        results: list[Any] = []

        for reg in self._hooks[hook_name]:
            if extension_id and reg.extension_id != extension_id:
                continue
            try:
                result = reg.handler(**kwargs)
                if asyncio.iscoroutine(result):
                    result = await result
                if result is not None:
                    results.append(result)
            except Exception as e:
                logger.warning(
                    "Hook {}:{} raised {}: {}",
                    hook_name.value,
                    reg.extension_id,
                    type(e).__name__,
                    e,
                )

        return results

    # ── Mutating Hooks ───────────────────────────────────────────────────

    async def dispatch_before_tool_call(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> tuple[dict[str, Any], bool, str]:
        """Dispatch on_tool_call hooks that can modify params or block.

        Returns:
            (modified_params, blocked, block_reason)
        """
        self._ensure_sorted(HookName.ON_TOOL_CALL)
        current_params = dict(params)

        for reg in self._hooks[HookName.ON_TOOL_CALL]:
            try:
                result = reg.handler(
                    tool_name=tool_name, params=current_params
                )
                if asyncio.iscoroutine(result):
                    result = await result
                if isinstance(result, dict):
                    if result.get("block"):
                        return (
                            current_params,
                            True,
                            result.get("block_reason", "Blocked by extension"),
                        )
                    if "params" in result:
                        current_params = result["params"]
            except Exception as e:
                logger.warning(
                    "Hook on_tool_call:{} raised {}: {}",
                    reg.extension_id,
                    type(e).__name__,
                    e,
                )

        return current_params, False, ""

    # ── Query ────────────────────────────────────────────────────────────

    def has_hooks(self, hook_name: HookName) -> bool:
        """Check if any handlers are registered for a hook."""
        return bool(self._hooks.get(hook_name))

    def hook_count(self, hook_name: HookName) -> int:
        """Count handlers for a hook."""
        return len(self._hooks.get(hook_name, []))

    @property
    def total_hooks(self) -> int:
        """Total count of all registered hook handlers."""
        return sum(len(h) for h in self._hooks.values())

    # ── Internal ─────────────────────────────────────────────────────────

    def _ensure_sorted(self, hook_name: HookName) -> None:
        """Sort handlers by priority if needed."""
        if not self._sorted.get(hook_name, True):
            self._hooks[hook_name].sort(key=lambda r: r.priority)
            self._sorted[hook_name] = True
