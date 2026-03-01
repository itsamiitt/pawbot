# PHASE 11 — HEARTBEAT & CRON SCHEDULER
> **Cross-reference:** [MASTER_REFERENCE.md](./MASTER_REFERENCE.md)
> **Implementation Day:** Day 20 (11.1 Smart Heartbeat), Weeks 5–8 (11.2 Advanced Scheduling)
> **Primary Files:** `~/nanobot/heartbeat/engine.py` (NEW), `~/nanobot/cron/scheduler.py` (NEW/enhance)
> **Test File:** `~/nanobot/tests/test_heartbeat_cron.py`
> **Depends on:** Phase 1 (MemoryRouter — read user context), Phase 2 (AgentLoop.process()), Phase 10 (ChannelRouter — proactive messages)

---

## BEFORE YOU START — READ THESE FILES

```bash
cat ~/nanobot/heartbeat/          # existing heartbeat code, if any
cat ~/nanobot/cron/               # existing cron code, if any
cat ~/nanobot/agent/loop.py       # AgentLoop.process() signature
cat ~/.nanobot/config.json        # current config structure
```

**Existing interfaces:** If `heartbeat/` or `cron/` have existing public methods, preserve them.

---

## WHAT YOU ARE BUILDING

Two tightly related systems:

1. **HeartbeatEngine** — wakes the agent proactively on schedule to check on long-running tasks, follow up on open questions, and send reminders to users. The agent is not purely reactive — it can self-trigger.

2. **CronScheduler** — runs registered jobs on cron-expression schedules. Used by Phase 1 (memory decay at 3am), Phase 11 itself (heartbeat check), and any user-defined scheduled tasks.

---

## CANONICAL NAMES — ALL NEW CLASSES IN THIS PHASE

| Class Name | File | Purpose |
|---|---|---|
| `CronScheduler` | `cron/scheduler.py` | Registers and runs scheduled jobs |
| `CronJob` | `cron/scheduler.py` | Single registered job dataclass |
| `HeartbeatEngine` | `heartbeat/engine.py` | Proactive agent wake-up logic |
| `HeartbeatTrigger` | `heartbeat/engine.py` | Dataclass describing a wake-up condition |
| `TaskWatcher` | `heartbeat/engine.py` | Monitors long-running background tasks |

---

## FEATURE 11.1 — CRON SCHEDULER

### `CronJob` dataclass

```python
from dataclasses import dataclass, field
from typing import Callable, Optional
import time

@dataclass
class CronJob:
    name: str               # unique identifier e.g. "memory_decay"
    schedule: str           # cron expression: "0 3 * * *"
    fn: Callable            # function to call (must be callable with no args)
    description: str = ""
    last_run: int = 0       # unix timestamp
    next_run: int = 0       # unix timestamp, calculated on register
    run_count: int = 0
    last_error: str = ""
    enabled: bool = True
```

### `CronScheduler` class

**File:** `~/nanobot/cron/scheduler.py`

```python
import threading, time, json, os, logging
from croniter import croniter   # pip install croniter

logger = logging.getLogger("nanobot")

class CronScheduler:
    """
    Runs registered jobs on cron-expression schedules.
    Runs in a background daemon thread.
    Persists job registry to ~/.nanobot/crons.json.
    """

    REGISTRY_PATH = os.path.expanduser("~/.nanobot/crons.json")

    def __init__(self):
        self._jobs: dict[str, CronJob] = {}
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._load_registry()

    def register(self, name: str, schedule: str, fn: Callable,
                 description: str = "") -> CronJob:
        """
        Register a job. Overwrites if name already exists.
        schedule: standard cron expression (5-part: min hr dom mon dow)
        """
        now = time.time()
        cron = croniter(schedule, now)
        next_run = int(cron.get_next(float))

        job = CronJob(
            name=name,
            schedule=schedule,
            fn=fn,
            description=description,
            next_run=next_run,
        )
        self._jobs[name] = job
        self._save_registry()
        logger.info(f"Cron registered: {name} @ {schedule}, next: {next_run}")
        return job

    def unregister(self, name: str) -> bool:
        """Remove a job. Returns True if removed."""
        if name in self._jobs:
            del self._jobs[name]
            self._save_registry()
            return True
        return False

    def start(self):
        """Start the scheduler in a background daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("CronScheduler started")

    def stop(self):
        self._running = False

    def _loop(self):
        """Main scheduler loop. Checks every 30 seconds."""
        while self._running:
            now = int(time.time())
            for name, job in list(self._jobs.items()):
                if not job.enabled:
                    continue
                if now >= job.next_run:
                    self._run_job(job)
            time.sleep(30)

    def _run_job(self, job: CronJob):
        """Execute a job in a daemon thread. Update next_run after execution."""
        def _execute():
            try:
                logger.info(f"Cron job starting: {job.name}")
                job.fn()
                job.run_count += 1
                job.last_run = int(time.time())
                job.last_error = ""
                logger.info(f"Cron job completed: {job.name} (run #{job.run_count})")
            except Exception as e:
                job.last_error = str(e)
                logger.warning(f"Cron job failed: {job.name} — {e}")
            finally:
                # Schedule next run
                cron = croniter(job.schedule, time.time())
                job.next_run = int(cron.get_next(float))
                self._save_registry()

        thread = threading.Thread(target=_execute, daemon=True)
        thread.start()

    def list_jobs(self) -> list[dict]:
        """Return all jobs with status info."""
        return [
            {
                "name": j.name,
                "schedule": j.schedule,
                "description": j.description,
                "last_run": j.last_run,
                "next_run": j.next_run,
                "run_count": j.run_count,
                "last_error": j.last_error,
                "enabled": j.enabled,
            }
            for j in self._jobs.values()
        ]

    def _save_registry(self):
        """Persist job metadata (not functions) to crons.json."""
        data = {
            name: {
                "schedule": job.schedule,
                "description": job.description,
                "last_run": job.last_run,
                "next_run": job.next_run,
                "run_count": job.run_count,
                "last_error": job.last_error,
                "enabled": job.enabled,
            }
            for name, job in self._jobs.items()
        }
        with open(self.REGISTRY_PATH, "w") as f:
            json.dump(data, f, indent=2)

    def _load_registry(self):
        """Load persisted metadata on startup."""
        if os.path.exists(self.REGISTRY_PATH):
            try:
                with open(self.REGISTRY_PATH) as f:
                    data = json.load(f)
                # Restore metadata only — fn is re-registered on startup
                for name, meta in data.items():
                    if name not in self._jobs:
                        # Create placeholder (fn will be set when register() is called)
                        self._jobs[name] = CronJob(
                            name=name,
                            schedule=meta.get("schedule", "0 * * * *"),
                            fn=lambda: None,
                            last_run=meta.get("last_run", 0),
                            next_run=meta.get("next_run", 0),
                            run_count=meta.get("run_count", 0),
                            last_error=meta.get("last_error", ""),
                            enabled=meta.get("enabled", True),
                        )
            except Exception as e:
                logger.warning(f"Failed to load crons.json: {e}")
```

Add `croniter>=2.0.0` to `pyproject.toml`.

### Phase 1 Integration

Wire `MemoryDecayEngine` into the scheduler at nanobot startup:

```python
# In nanobot startup code (agent initialization):
from nanobot.cron.scheduler import CronScheduler
from nanobot.agent.memory import MemoryDecayEngine, SQLiteFactStore

sqlite_store = SQLiteFactStore(config)
decay_engine = MemoryDecayEngine(sqlite_store)
cron = CronScheduler()
cron.register(
    name=MemoryDecayEngine.JOB_NAME,       # "memory_decay"
    schedule=MemoryDecayEngine.CRON_SCHEDULE,  # "0 3 * * *"
    fn=decay_engine.decay_pass,
    description="Nightly memory salience decay and archival"
)
cron.start()
```

---

## FEATURE 11.2 — SMART HEARTBEAT ENGINE

**File:** `~/nanobot/heartbeat/engine.py`

The HeartbeatEngine wakes the agent on a schedule to:
- Check the status of any monitored background tasks
- Send follow-up messages to users if awaited responses haven't arrived
- Run user-configured proactive reminders

### `HeartbeatTrigger` dataclass

```python
@dataclass
class HeartbeatTrigger:
    """Defines when and why the heartbeat should wake the agent."""
    id: str
    trigger_type: str   # "task_check" | "followup" | "reminder" | "custom"
    schedule: str       # cron expression OR "once:{unix_timestamp}"
    context: dict       # passed to AgentLoop.process() as context
    message: str        # prompt sent to agent on wake
    contact_id: str = ""   # if set, agent response is sent to this contact
    channel: str = ""      # "whatsapp" | "telegram" | "email" | "" (no send)
    max_runs: int = 0      # 0 = infinite
    run_count: int = 0
    active: bool = True
```

### `HeartbeatEngine` class

```python
class HeartbeatEngine:
    """
    Wakes the agent proactively based on registered triggers.
    Works alongside CronScheduler — each trigger becomes a cron job.
    """

    HEARTBEAT_CHECK_SCHEDULE = "*/5 * * * *"  # every 5 minutes
    TRIGGERS_PATH = os.path.expanduser("~/.nanobot/heartbeat_triggers.json")

    def __init__(self, agent_loop, cron_scheduler: CronScheduler,
                 channel_router=None, memory_router=None):
        self.loop = agent_loop
        self.cron = cron_scheduler
        self.channels = channel_router
        self.memory = memory_router
        self._triggers: dict[str, HeartbeatTrigger] = {}
        self._load_triggers()

        # Register the heartbeat checker itself as a cron job
        self.cron.register(
            name="heartbeat_check",
            schedule=self.HEARTBEAT_CHECK_SCHEDULE,
            fn=self._check_all_triggers,
            description="Proactive heartbeat: check triggers and wake agent"
        )

    def add_trigger(
        self,
        trigger_type: str,
        message: str,
        schedule: str,
        context: dict = {},
        contact_id: str = "",
        channel: str = "",
        max_runs: int = 0,
    ) -> str:
        """Add a new heartbeat trigger. Returns trigger_id."""
        import uuid
        trigger_id = str(uuid.uuid4())[:8]
        trigger = HeartbeatTrigger(
            id=trigger_id,
            trigger_type=trigger_type,
            schedule=schedule,
            context=context,
            message=message,
            contact_id=contact_id,
            channel=channel,
            max_runs=max_runs,
        )
        self._triggers[trigger_id] = trigger
        self._save_triggers()
        logger.info(f"Heartbeat trigger added: {trigger_id} ({trigger_type})")
        return trigger_id

    def remove_trigger(self, trigger_id: str) -> bool:
        if trigger_id in self._triggers:
            del self._triggers[trigger_id]
            self._save_triggers()
            return True
        return False

    def _check_all_triggers(self):
        """
        Called by CronScheduler every 5 minutes.
        Evaluates each trigger and fires those that are due.
        """
        now = int(time.time())
        for tid, trigger in list(self._triggers.items()):
            if not trigger.active:
                continue
            if self._is_due(trigger, now):
                self._fire(trigger)

    def _is_due(self, trigger: HeartbeatTrigger, now: int) -> bool:
        """Check if trigger's schedule is currently due."""
        if trigger.schedule.startswith("once:"):
            fire_at = int(trigger.schedule.split(":")[1])
            return now >= fire_at
        try:
            cron = croniter(trigger.schedule, now - 60)
            next_time = cron.get_next(float)
            return next_time <= now
        except Exception:
            return False

    def _fire(self, trigger: HeartbeatTrigger):
        """Wake the agent with this trigger's message and context."""
        def _execute():
            try:
                logger.info(f"Heartbeat firing: {trigger.id} ({trigger.trigger_type})")
                context = {**trigger.context, "heartbeat_trigger": trigger.id,
                           "trigger_type": trigger.trigger_type}
                response = self.loop.process(trigger.message, context=context)

                # Send response to contact if configured
                if response and trigger.contact_id and trigger.channel and self.channels:
                    channel_adapter = self.channels._channels.get(trigger.channel)
                    if channel_adapter:
                        channel_adapter.send(trigger.contact_id, response)

                # Save to memory
                if self.memory:
                    self.memory.save("episode", {
                        "text": f"[Heartbeat {trigger.trigger_type}] {trigger.message}",
                        "response": response,
                        "trigger_id": trigger.id,
                    })

                trigger.run_count += 1

                # Deactivate if max_runs reached
                if trigger.max_runs > 0 and trigger.run_count >= trigger.max_runs:
                    trigger.active = False
                    logger.info(f"Heartbeat trigger deactivated (max_runs): {trigger.id}")

                self._save_triggers()

            except Exception as e:
                logger.warning(f"Heartbeat fire failed for {trigger.id}: {e}")

        thread = threading.Thread(target=_execute, daemon=True)
        thread.start()

    def _save_triggers(self):
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
        with open(self.TRIGGERS_PATH, "w") as f:
            json.dump(data, f, indent=2)

    def _load_triggers(self):
        if os.path.exists(self.TRIGGERS_PATH):
            try:
                with open(self.TRIGGERS_PATH) as f:
                    data = json.load(f)
                for tid, meta in data.items():
                    self._triggers[tid] = HeartbeatTrigger(
                        id=tid, **meta
                    )
            except Exception as e:
                logger.warning(f"Failed to load heartbeat triggers: {e}")
```

### `TaskWatcher` class

Monitors long-running background tasks and fires a heartbeat when they complete or fail:

```python
class TaskWatcher:
    """
    Registers tasks that should be watched.
    When a task completes or times out, fires a heartbeat trigger
    to inform the agent so it can report to the user.
    """

    def __init__(self, heartbeat: HeartbeatEngine):
        self.heartbeat = heartbeat
        self._watched: dict[str, dict] = {}

    def watch(
        self,
        task_id: str,
        description: str,
        contact_id: str = "",
        channel: str = "",
        timeout_minutes: int = 60,
    ):
        """Register a task to watch."""
        fire_at = int(time.time()) + timeout_minutes * 60
        self._watched[task_id] = {
            "description": description,
            "contact_id": contact_id,
            "channel": channel,
            "started_at": int(time.time()),
            "timeout_at": fire_at,
        }
        # Set a one-shot heartbeat to check on timeout
        self.heartbeat.add_trigger(
            trigger_type="task_check",
            message=f"Check status of task: {description} (task_id: {task_id})",
            schedule=f"once:{fire_at}",
            context={"watched_task_id": task_id},
            contact_id=contact_id,
            channel=channel,
            max_runs=1,
        )

    def complete(self, task_id: str, result: str = ""):
        """Mark a task as complete. Fires immediate heartbeat."""
        if task_id in self._watched:
            meta = self._watched.pop(task_id)
            if meta.get("contact_id"):
                self.heartbeat.add_trigger(
                    trigger_type="task_complete",
                    message=f"Task completed: {meta['description']}. Result: {result}",
                    schedule=f"once:{int(time.time()) + 5}",
                    contact_id=meta["contact_id"],
                    channel=meta.get("channel", ""),
                    max_runs=1,
                )
```

---

## CONFIG KEYS TO ADD

```json
{
  "heartbeat": {
    "enabled": true,
    "check_interval_minutes": 5,
    "triggers_path": "~/.nanobot/heartbeat_triggers.json"
  },
  "cron": {
    "enabled": true,
    "registry_path": "~/.nanobot/crons.json",
    "check_interval_seconds": 30
  }
}
```

---

## TEST REQUIREMENTS

**File:** `~/nanobot/tests/test_heartbeat_cron.py`

```python
class TestCronJob:
    def test_next_run_calculated_on_register()
    def test_run_count_increments()
    def test_last_error_captured_on_failure()

class TestCronScheduler:
    def test_register_and_list_jobs()
    def test_unregister_removes_job()
    def test_job_fires_when_due()
    def test_failed_job_reschedules()
    def test_registry_persists_to_json()
    def test_disabled_job_not_fired()

class TestHeartbeatTrigger:
    def test_once_trigger_fires_at_timestamp()
    def test_cron_trigger_fires_on_schedule()
    def test_max_runs_deactivates_trigger()

class TestHeartbeatEngine:
    def test_add_and_remove_trigger()
    def test_fire_calls_agent_loop()
    def test_fire_sends_to_channel_if_configured()
    def test_fire_saves_to_memory()
    def test_triggers_persist_across_restart()

class TestTaskWatcher:
    def test_watch_registers_heartbeat_trigger()
    def test_complete_fires_immediate_trigger()
    def test_timeout_triggers_check()

class TestPhase1Integration:
    def test_memory_decay_registered_as_cron_job()
    def test_decay_fires_on_schedule()
```

---

## CROSS-REFERENCES

- **Phase 1** (MemoryDecayEngine): `cron.register(name="memory_decay", schedule="0 3 * * *", fn=decay_engine.decay_pass)` — this is where Phase 1's decay engine gets wired in
- **Phase 2** (AgentLoop): HeartbeatEngine calls `loop.process(message, context=...)` — AgentLoop must handle `context["heartbeat_trigger"]` and `context["trigger_type"]`
- **Phase 10** (ChannelRouter): `HeartbeatEngine._fire()` calls `channel_router._channels[channel].send(contact_id, response)` for proactive messages
- **Phase 12** (SubagentRunner): `TaskWatcher.watch()` is used by SubagentRunner to fire a heartbeat when background tasks complete
- **Phase 15** (Observability): Wrap `_fire()` and `_run_job()` with trace spans

All canonical names are in [MASTER_REFERENCE.md](./MASTER_REFERENCE.md).
