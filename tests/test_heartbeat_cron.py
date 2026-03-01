import threading
import time
from pathlib import Path
from typing import Any

from pawbot.agent.memory import MemoryDecayEngine
from pawbot.cron.scheduler import CronScheduler
from pawbot.heartbeat.engine import HeartbeatEngine, TaskWatcher


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class StubCron:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def register(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs


class DummyLoop:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.event = threading.Event()

    def process(self, message: str, context: dict[str, Any] | None = None) -> str:
        self.calls.append((message, context))
        self.event.set()
        return "ok"


class DummyChannel:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []
        self.event = threading.Event()

    def send(self, contact_id: str, message: str) -> None:
        self.sent.append((contact_id, message))
        self.event.set()


class DummyMemory:
    def __init__(self):
        self.saved: list[tuple[str, dict[str, Any]]] = []

    def save(self, type_: str, content: dict[str, Any]) -> None:
        self.saved.append((type_, content))


class TestCronJob:
    def test_next_run_calculated_on_register(self, tmp_path: Path):
        scheduler = CronScheduler(registry_path=tmp_path / "crons.json")
        job = scheduler.register("job1", "* * * * *", lambda: None)
        assert job.next_run > int(time.time())

    def test_run_count_increments(self, tmp_path: Path):
        scheduler = CronScheduler(registry_path=tmp_path / "crons.json")
        done = threading.Event()

        def _fn():
            done.set()

        job = scheduler.register("job1", "* * * * *", _fn)
        job.next_run = int(time.time()) - 1
        scheduler._check_due_jobs()

        assert done.wait(1.0)
        assert _wait_for(lambda: job.run_count == 1)

    def test_last_error_captured_on_failure(self, tmp_path: Path):
        scheduler = CronScheduler(registry_path=tmp_path / "crons.json")

        def _fail():
            raise RuntimeError("boom")

        job = scheduler.register("job1", "* * * * *", _fail)
        job.next_run = int(time.time()) - 1
        scheduler._check_due_jobs()

        assert _wait_for(lambda: job.last_error == "boom")


class TestCronScheduler:
    def test_register_and_list_jobs(self, tmp_path: Path):
        scheduler = CronScheduler(registry_path=tmp_path / "crons.json")
        scheduler.register("job1", "* * * * *", lambda: None, description="test")

        jobs = scheduler.list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["name"] == "job1"
        assert jobs[0]["description"] == "test"

    def test_unregister_removes_job(self, tmp_path: Path):
        scheduler = CronScheduler(registry_path=tmp_path / "crons.json")
        scheduler.register("job1", "* * * * *", lambda: None)
        assert scheduler.unregister("job1") is True
        assert scheduler.list_jobs() == []

    def test_job_fires_when_due(self, tmp_path: Path):
        scheduler = CronScheduler(registry_path=tmp_path / "crons.json")
        fired = threading.Event()

        def _fn():
            fired.set()

        job = scheduler.register("job1", "* * * * *", _fn)
        job.next_run = int(time.time()) - 1
        scheduler._check_due_jobs()

        assert fired.wait(1.0)

    def test_failed_job_reschedules(self, tmp_path: Path):
        scheduler = CronScheduler(registry_path=tmp_path / "crons.json")

        def _fail():
            raise RuntimeError("fail once")

        job = scheduler.register("job1", "* * * * *", _fail)
        now = int(time.time())
        job.next_run = now - 1
        scheduler._check_due_jobs()

        assert _wait_for(lambda: job.last_error == "fail once")
        assert job.next_run > now

    def test_registry_persists_to_json(self, tmp_path: Path):
        path = tmp_path / "crons.json"
        scheduler = CronScheduler(registry_path=path)
        scheduler.register("job1", "* * * * *", lambda: None, description="persisted")

        assert path.exists()
        scheduler2 = CronScheduler(registry_path=path)
        jobs = scheduler2.list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["name"] == "job1"
        assert jobs[0]["description"] == "persisted"

    def test_disabled_job_not_fired(self, tmp_path: Path):
        scheduler = CronScheduler(registry_path=tmp_path / "crons.json")
        fired = threading.Event()

        def _fn():
            fired.set()

        job = scheduler.register("job1", "* * * * *", _fn)
        scheduler.enable("job1", enabled=False)
        job.next_run = int(time.time()) - 1
        scheduler._check_due_jobs()

        assert fired.wait(0.2) is False


class TestHeartbeatTrigger:
    def test_once_trigger_fires_at_timestamp(self, tmp_path: Path):
        loop = DummyLoop()
        engine = HeartbeatEngine(loop, StubCron(), triggers_path=tmp_path / "triggers.json")
        trigger_id = engine.add_trigger(
            trigger_type="custom",
            message="hello",
            schedule=f"once:{int(time.time()) - 1}",
        )

        engine._check_all_triggers()
        assert loop.event.wait(1.0)
        assert _wait_for(lambda: engine._triggers[trigger_id].run_count == 1)

    def test_cron_trigger_fires_on_schedule(self, tmp_path: Path):
        loop = DummyLoop()
        engine = HeartbeatEngine(loop, StubCron(), triggers_path=tmp_path / "triggers.json")
        trigger_id = engine.add_trigger(
            trigger_type="custom",
            message="hello",
            schedule="* * * * *",
        )

        engine._check_all_triggers()
        assert loop.event.wait(1.0)
        assert _wait_for(lambda: engine._triggers[trigger_id].run_count == 1)

    def test_max_runs_deactivates_trigger(self, tmp_path: Path):
        loop = DummyLoop()
        engine = HeartbeatEngine(loop, StubCron(), triggers_path=tmp_path / "triggers.json")
        trigger_id = engine.add_trigger(
            trigger_type="custom",
            message="hello",
            schedule="* * * * *",
            max_runs=1,
        )

        engine._check_all_triggers()
        assert _wait_for(lambda: engine._triggers[trigger_id].run_count == 1)
        assert engine._triggers[trigger_id].active is False


class TestHeartbeatEngine:
    def test_add_and_remove_trigger(self, tmp_path: Path):
        engine = HeartbeatEngine(DummyLoop(), StubCron(), triggers_path=tmp_path / "triggers.json")
        trigger_id = engine.add_trigger("custom", "msg", "* * * * *")
        assert trigger_id in engine._triggers
        assert engine.remove_trigger(trigger_id) is True
        assert trigger_id not in engine._triggers

    def test_fire_calls_agent_loop(self, tmp_path: Path):
        loop = DummyLoop()
        engine = HeartbeatEngine(loop, StubCron(), triggers_path=tmp_path / "triggers.json")
        trigger_id = engine.add_trigger("custom", "ping", "* * * * *")
        trigger = engine._triggers[trigger_id]

        engine._fire(trigger, int(time.time()))
        assert loop.event.wait(1.0)

    def test_fire_sends_to_channel_if_configured(self, tmp_path: Path):
        loop = DummyLoop()
        channel = DummyChannel()
        channels = type("Channels", (), {"channels": {"telegram": channel}})()
        engine = HeartbeatEngine(
            loop,
            StubCron(),
            channel_router=channels,
            triggers_path=tmp_path / "triggers.json",
        )
        trigger_id = engine.add_trigger(
            "custom",
            "ping",
            "* * * * *",
            contact_id="user123",
            channel="telegram",
        )
        trigger = engine._triggers[trigger_id]

        engine._fire(trigger, int(time.time()))
        assert channel.event.wait(1.0)
        assert channel.sent[0] == ("user123", "ok")

    def test_fire_saves_to_memory(self, tmp_path: Path):
        loop = DummyLoop()
        memory = DummyMemory()
        engine = HeartbeatEngine(
            loop,
            StubCron(),
            memory_router=memory,
            triggers_path=tmp_path / "triggers.json",
        )
        trigger_id = engine.add_trigger("custom", "ping", "* * * * *")
        trigger = engine._triggers[trigger_id]

        engine._fire(trigger, int(time.time()))
        assert _wait_for(lambda: len(memory.saved) == 1)
        assert memory.saved[0][0] == "episode"
        assert memory.saved[0][1]["trigger_id"] == trigger_id

    def test_triggers_persist_across_restart(self, tmp_path: Path):
        path = tmp_path / "triggers.json"
        engine1 = HeartbeatEngine(DummyLoop(), StubCron(), triggers_path=path)
        trigger_id = engine1.add_trigger("followup", "msg", "* * * * *")

        engine2 = HeartbeatEngine(DummyLoop(), StubCron(), triggers_path=path)
        assert trigger_id in engine2._triggers
        assert engine2._triggers[trigger_id].trigger_type == "followup"


class TestTaskWatcher:
    def test_watch_registers_heartbeat_trigger(self, tmp_path: Path):
        engine = HeartbeatEngine(DummyLoop(), StubCron(), triggers_path=tmp_path / "triggers.json")
        watcher = TaskWatcher(engine)
        watcher.watch("t1", "build project", contact_id="u1", channel="telegram", timeout_minutes=1)

        triggers = [t for t in engine._triggers.values() if t.trigger_type == "task_check"]
        assert len(triggers) == 1
        assert triggers[0].context["watched_task_id"] == "t1"

    def test_complete_fires_immediate_trigger(self, tmp_path: Path):
        engine = HeartbeatEngine(DummyLoop(), StubCron(), triggers_path=tmp_path / "triggers.json")
        watcher = TaskWatcher(engine)
        watcher.watch("t1", "build project", contact_id="u1", channel="telegram", timeout_minutes=1)
        watcher.complete("t1", result="done")

        triggers = [t for t in engine._triggers.values() if t.trigger_type == "task_complete"]
        assert len(triggers) == 1
        assert triggers[0].schedule.startswith("once:")

    def test_timeout_triggers_check(self, tmp_path: Path):
        engine = HeartbeatEngine(DummyLoop(), StubCron(), triggers_path=tmp_path / "triggers.json")
        watcher = TaskWatcher(engine)
        watcher.watch("t-timeout", "long task", timeout_minutes=1)

        triggers = [t for t in engine._triggers.values() if t.trigger_type == "task_check"]
        assert len(triggers) == 1
        assert triggers[0].schedule.startswith("once:")


class TestPhase1Integration:
    def test_memory_decay_registered_as_cron_job(self, tmp_path: Path):
        scheduler = CronScheduler(registry_path=tmp_path / "crons.json")
        scheduler.register(
            name=MemoryDecayEngine.JOB_NAME,
            schedule=MemoryDecayEngine.CRON_SCHEDULE,
            fn=lambda: None,
            description="Nightly memory salience decay and archival",
        )
        jobs = scheduler.list_jobs()
        assert any(
            j["name"] == MemoryDecayEngine.JOB_NAME
            and j["schedule"] == MemoryDecayEngine.CRON_SCHEDULE
            for j in jobs
        )

    def test_decay_fires_on_schedule(self, tmp_path: Path):
        scheduler = CronScheduler(registry_path=tmp_path / "crons.json")
        fired = threading.Event()

        scheduler.register(
            name=MemoryDecayEngine.JOB_NAME,
            schedule=MemoryDecayEngine.CRON_SCHEDULE,
            fn=lambda: fired.set(),
            description="Nightly memory salience decay and archival",
        )
        scheduler._jobs[MemoryDecayEngine.JOB_NAME].next_run = int(time.time()) - 1
        scheduler._check_due_jobs()

        assert fired.wait(1.0)
