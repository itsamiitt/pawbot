"""Phase 11 heartbeat engine and task watcher."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from pawbot.cron.scheduler import CronScheduler

try:
    from croniter import croniter
except Exception:  # pragma: no cover - exercised by graceful fallback paths
    croniter = None

try:
    from pawbot.bus.events import OutboundMessage
except Exception:  # pragma: no cover - optional for isolated tests
    OutboundMessage = None


@dataclass
class HeartbeatTrigger:
    """Defines when and why the heartbeat should wake the agent."""

    id: str
    trigger_type: str
    schedule: str
    context: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    contact_id: str = ""
    channel: str = ""
    max_runs: int = 0
    run_count: int = 0
    active: bool = True


class HeartbeatEngine:
    """
    Wakes the agent proactively based on registered triggers.

    Trigger persistence is stored in ~/.pawbot/heartbeat_triggers.json by default.
    """

    HEARTBEAT_CHECK_SCHEDULE = "*/5 * * * *"
    TRIGGERS_PATH = os.path.expanduser("~/.pawbot/heartbeat_triggers.json")

    def __init__(
        self,
        agent_loop: Any,
        cron_scheduler: CronScheduler,
        channel_router: Any = None,
        memory_router: Any = None,
        triggers_path: str | os.PathLike[str] | None = None,
        check_schedule: str | None = None,
    ):
        self.loop = agent_loop
        self.cron = cron_scheduler
        self.channels = channel_router
        self.memory = memory_router
        self.triggers_path = os.path.expanduser(str(triggers_path or self.TRIGGERS_PATH))
        self.check_schedule = check_schedule or self.HEARTBEAT_CHECK_SCHEDULE
        self._triggers: dict[str, HeartbeatTrigger] = {}
        self._last_slot_fired: dict[str, int] = {}
        self._lock = threading.RLock()
        self._load_triggers()

        try:
            self.cron.register(
                name="heartbeat_check",
                schedule=self.check_schedule,
                fn=self._check_all_triggers,
                description="Proactive heartbeat: check triggers and wake agent",
            )
        except Exception as exc:
            logger.warning("HeartbeatEngine failed to register heartbeat_check: {}", exc)

    def add_trigger(
        self,
        trigger_type: str,
        message: str,
        schedule: str,
        context: dict[str, Any] | None = None,
        contact_id: str = "",
        channel: str = "",
        max_runs: int = 0,
    ) -> str:
        """Add a trigger and return trigger ID."""
        trigger_id = str(uuid.uuid4())[:8]
        trigger = HeartbeatTrigger(
            id=trigger_id,
            trigger_type=trigger_type,
            schedule=schedule,
            context=dict(context or {}),
            message=message,
            contact_id=contact_id,
            channel=channel,
            max_runs=max(0, int(max_runs)),
        )
        with self._lock:
            self._triggers[trigger_id] = trigger
            self._save_triggers_locked()
        logger.info("Heartbeat trigger added: {} ({})", trigger_id, trigger_type)
        return trigger_id

    def remove_trigger(self, trigger_id: str) -> bool:
        """Remove a trigger by ID."""
        with self._lock:
            if trigger_id not in self._triggers:
                return False
            del self._triggers[trigger_id]
            self._last_slot_fired.pop(trigger_id, None)
            self._save_triggers_locked()
        logger.info("Heartbeat trigger removed: {}", trigger_id)
        return True

    def list_triggers(self) -> list[dict[str, Any]]:
        """List all triggers with current runtime state."""
        with self._lock:
            triggers = list(self._triggers.values())
        return [
            {
                "id": t.id,
                "trigger_type": t.trigger_type,
                "schedule": t.schedule,
                "context": dict(t.context),
                "message": t.message,
                "contact_id": t.contact_id,
                "channel": t.channel,
                "max_runs": t.max_runs,
                "run_count": t.run_count,
                "active": t.active,
            }
            for t in triggers
        ]

    def _check_all_triggers(self) -> None:
        """Evaluate all triggers and fire those that are due."""
        now = int(time.time())
        due: list[tuple[HeartbeatTrigger, int]] = []

        with self._lock:
            for trigger in self._triggers.values():
                if not trigger.active:
                    continue
                slot = self._due_slot(trigger, now)
                if slot is None:
                    continue
                if slot <= self._last_slot_fired.get(trigger.id, 0):
                    continue
                self._last_slot_fired[trigger.id] = slot
                due.append((trigger, slot))

        for trigger, slot in due:
            self._fire(trigger, slot)

    def _is_due(self, trigger: HeartbeatTrigger, now: int) -> bool:
        """Check whether a trigger is due at a given unix timestamp."""
        return self._due_slot(trigger, now) is not None

    def _due_slot(self, trigger: HeartbeatTrigger, now: int) -> int | None:
        if trigger.schedule.startswith("once:"):
            try:
                fire_at = int(trigger.schedule.split(":", 1)[1])
            except (ValueError, IndexError):
                return None
            if trigger.run_count > 0:
                return None
            return fire_at if now >= fire_at else None

        if croniter is None:
            return None
        try:
            # Last scheduled slot at or before now.
            slot = int(croniter(trigger.schedule, now).get_prev(float))
            return slot if slot <= now else None
        except Exception as e:  # noqa: F841
            return None

    def _fire(self, trigger: HeartbeatTrigger, _slot: int) -> None:
        """Wake the agent with this trigger in a daemon thread."""

        def _execute() -> None:
            try:
                logger.info("Heartbeat firing: {} ({})", trigger.id, trigger.trigger_type)
                context = {
                    **trigger.context,
                    "heartbeat_trigger": trigger.id,
                    "trigger_type": trigger.trigger_type,
                }
                response = self._invoke_agent(trigger, context)

                if response and trigger.contact_id and trigger.channel:
                    self._send_to_channel(trigger, str(response))

                if self.memory is not None:
                    self._save_to_memory(trigger, response)

                with self._lock:
                    trigger.run_count += 1
                    if trigger.schedule.startswith("once:"):
                        trigger.active = False
                    if trigger.max_runs > 0 and trigger.run_count >= trigger.max_runs:
                        trigger.active = False
                        logger.info("Heartbeat trigger deactivated (max_runs): {}", trigger.id)
                    self._save_triggers_locked()
            except Exception as exc:
                logger.warning("Heartbeat fire failed for {}: {}", trigger.id, exc)

        thread = threading.Thread(target=_execute, daemon=True, name=f"heartbeat-{trigger.id}")
        thread.start()

    def _invoke_agent(self, trigger: HeartbeatTrigger, context: dict[str, Any]) -> Any:
        """
        Invoke the agent loop.

        Supports either:
        - loop.process(message, context=...)
        - loop.process_direct(message, session_key=..., channel=..., chat_id=...)
        """
        if self.loop is None:
            return None

        process = getattr(self.loop, "process", None)
        if callable(process):
            kwargs: dict[str, Any] = {}
            try:
                sig = inspect.signature(process)
                if "context" in sig.parameters:
                    kwargs["context"] = context
            except (TypeError, ValueError):
                pass
            return self._resolve_result(process(trigger.message, **kwargs))

        process_direct = getattr(self.loop, "process_direct", None)
        if callable(process_direct):
            kwargs = {}
            try:
                sig = inspect.signature(process_direct)
                if "session_key" in sig.parameters:
                    kwargs["session_key"] = f"heartbeat:{trigger.id}"
                if "channel" in sig.parameters:
                    kwargs["channel"] = trigger.channel or "cli"
                if "chat_id" in sig.parameters:
                    kwargs["chat_id"] = trigger.contact_id or "direct"
                if "context" in sig.parameters:
                    kwargs["context"] = context
            except (TypeError, ValueError):
                pass
            return self._resolve_result(process_direct(trigger.message, **kwargs))

        logger.warning("HeartbeatEngine: loop has no process/process_direct method")
        return None

    @staticmethod
    def _resolve_result(result: Any) -> Any:
        if inspect.isawaitable(result):
            return asyncio.run(result)
        return result

    def _send_to_channel(self, trigger: HeartbeatTrigger, response: str) -> None:
        adapter = self._get_channel_adapter(trigger.channel)
        if adapter is None:
            return

        send = getattr(adapter, "send", None)
        if not callable(send):
            return

        try:
            result = send(trigger.contact_id, response)
        except TypeError:
            if OutboundMessage is None:
                payload = {
                    "channel": trigger.channel,
                    "chat_id": trigger.contact_id,
                    "content": response,
                }
                result = send(payload)
            else:
                result = send(OutboundMessage(
                    channel=trigger.channel,
                    chat_id=trigger.contact_id,
                    content=response,
                ))

        if inspect.isawaitable(result):
            asyncio.run(result)

    def _get_channel_adapter(self, name: str) -> Any | None:
        if not name or self.channels is None:
            return None
        if isinstance(self.channels, dict):
            return self.channels.get(name)
        if hasattr(self.channels, "_channels"):
            channels = getattr(self.channels, "_channels", {})
            if isinstance(channels, dict):
                return channels.get(name)
        if hasattr(self.channels, "channels"):
            channels = getattr(self.channels, "channels", {})
            if isinstance(channels, dict):
                return channels.get(name)
        if hasattr(self.channels, "get_channel") and callable(self.channels.get_channel):
            return self.channels.get_channel(name)
        return None

    def _save_to_memory(self, trigger: HeartbeatTrigger, response: Any) -> None:
        save = getattr(self.memory, "save", None)
        if not callable(save):
            return

        payload = {
            "text": f"[Heartbeat {trigger.trigger_type}] {trigger.message}",
            "response": response,
            "trigger_id": trigger.id,
            "timestamp": int(time.time()),
        }
        result = save("episode", payload)
        if inspect.isawaitable(result):
            asyncio.run(result)

    def _save_triggers(self) -> None:
        with self._lock:
            self._save_triggers_locked()

    def _save_triggers_locked(self) -> None:
        path = Path(self.triggers_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            tid: {
                "trigger_type": t.trigger_type,
                "schedule": t.schedule,
                "context": t.context,
                "message": t.message,
                "contact_id": t.contact_id,
                "channel": t.channel,
                "max_runs": t.max_runs,
                "run_count": t.run_count,
                "active": t.active,
            }
            for tid, t in self._triggers.items()
        }
        from pawbot.utils.fs import write_json_with_backup
        write_json_with_backup(path, data)

    def _load_triggers(self) -> None:
        from pawbot.utils.fs import safe_read_json
        path = Path(self.triggers_path)
        data = safe_read_json(path, default={})
        if not isinstance(data, dict):
            return
        for tid, meta in data.items():
            if not isinstance(meta, dict):
                continue
            try:
                self._triggers[str(tid)] = HeartbeatTrigger(
                    id=str(tid),
                    trigger_type=str(meta.get("trigger_type", "custom")),
                    schedule=str(meta.get("schedule", "")),
                    context=dict(meta.get("context") or {}),
                    message=str(meta.get("message", "")),
                    contact_id=str(meta.get("contact_id", "")),
                    channel=str(meta.get("channel", "")),
                    max_runs=int(meta.get("max_runs", 0) or 0),
                    run_count=int(meta.get("run_count", 0) or 0),
                    active=bool(meta.get("active", True)),
                )
            except Exception as exc:
                logger.warning("Failed to parse heartbeat trigger '{}': {}", tid, exc)


class TaskWatcher:
    """
    Monitors long-running tasks and notifies via heartbeat triggers.
    """

    def __init__(self, heartbeat: HeartbeatEngine):
        self.heartbeat = heartbeat
        self._watched: dict[str, dict[str, Any]] = {}

    def watch(
        self,
        task_id: str,
        description: str,
        contact_id: str = "",
        channel: str = "",
        timeout_minutes: int = 60,
    ) -> None:
        """Register a task watch with timeout follow-up."""
        now = int(time.time())
        fire_at = now + max(1, int(timeout_minutes)) * 60
        self._watched[task_id] = {
            "description": description,
            "contact_id": contact_id,
            "channel": channel,
            "started_at": now,
            "timeout_at": fire_at,
        }
        self.heartbeat.add_trigger(
            trigger_type="task_check",
            message=f"Check status of task: {description} (task_id: {task_id})",
            schedule=f"once:{fire_at}",
            context={"watched_task_id": task_id},
            contact_id=contact_id,
            channel=channel,
            max_runs=1,
        )

    def complete(self, task_id: str, result: str = "") -> None:
        """Mark a watched task complete and notify quickly."""
        meta = self._watched.pop(task_id, None)
        if not meta:
            return
        if not meta.get("contact_id"):
            return
        self.heartbeat.add_trigger(
            trigger_type="task_complete",
            message=f"Task completed: {meta['description']}. Result: {result}",
            schedule=f"once:{int(time.time()) + 5}",
            contact_id=str(meta["contact_id"]),
            channel=str(meta.get("channel", "")),
            max_runs=1,
        )
