import json
from pathlib import Path

import pytest

from pawbot.agent.skills import LoRAPipeline, Skill, SkillExecutor, SkillLoader, SkillWriter


class MemoryStub:
    def __init__(self):
        self.saved = []

    def save(self, memory_type: str, payload: dict):
        self.saved.append((memory_type, payload))


class LoopStub:
    def __init__(self):
        self.calls = []

    def process(self, task: str, context: dict | None = None) -> str:
        self.calls.append((task, context or {}))
        return "done"


class TestSkillWriter:
    def test_create_skill_saves_to_disk(self, tmp_path: Path):
        writer = SkillWriter(skills_dir=tmp_path / "skills")
        skill = writer.create_skill(
            name="deploy-to-vps",
            description="Deploy app to VPS",
            steps=["Connect to host", "Pull latest code"],
            tools_used=["ssh", "git"],
            triggers=["deploy", "vps"],
        )
        path = tmp_path / "skills" / "deploy-to-vps" / "skill.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["name"] == skill.name

    def test_duplicate_name_raises(self, tmp_path: Path):
        writer = SkillWriter(skills_dir=tmp_path / "skills")
        writer.create_skill(
            name="deploy-to-vps",
            description="Deploy app to VPS",
            steps=["Connect"],
            tools_used=["ssh"],
        )
        with pytest.raises(ValueError, match="already exists"):
            writer.create_skill(
                name="deploy-to-vps",
                description="Duplicate",
                steps=["Step"],
                tools_used=[],
            )

    def test_invalid_name_format_raises(self, tmp_path: Path):
        writer = SkillWriter(skills_dir=tmp_path / "skills")
        with pytest.raises(ValueError, match="kebab-case"):
            writer.create_skill(
                name="DeployToVPS",
                description="Invalid name",
                steps=["Step"],
                tools_used=[],
            )

    def test_update_increments_version(self, tmp_path: Path):
        writer = SkillWriter(skills_dir=tmp_path / "skills")
        writer.create_skill(
            name="deploy-to-vps",
            description="Deploy app to VPS",
            steps=["Connect"],
            tools_used=["ssh"],
        )
        updated = writer.update_skill("deploy-to-vps", description="Updated")
        assert updated.version == "1.1"
        assert updated.description == "Updated"

    def test_delete_removes_directory(self, tmp_path: Path):
        writer = SkillWriter(skills_dir=tmp_path / "skills")
        writer.create_skill(
            name="deploy-to-vps",
            description="Deploy app to VPS",
            steps=["Connect"],
            tools_used=["ssh"],
        )
        assert writer.delete_skill("deploy-to-vps") is True
        assert not (tmp_path / "skills" / "deploy-to-vps").exists()

    def test_load_skill_from_disk(self, tmp_path: Path):
        writer = SkillWriter(skills_dir=tmp_path / "skills")
        writer.create_skill(
            name="deploy-to-vps",
            description="Deploy app to VPS",
            steps=["Connect"],
            tools_used=["ssh"],
            triggers=["deploy"],
        )
        loaded = writer.load_skill("deploy-to-vps")
        assert loaded.name == "deploy-to-vps"
        assert loaded.triggers == ["deploy"]

    def test_list_sorted_by_success_count(self, tmp_path: Path):
        writer = SkillWriter(skills_dir=tmp_path / "skills")
        writer.create_skill(
            name="low-skill",
            description="Low usage",
            steps=["Step"],
            tools_used=[],
        )
        writer.create_skill(
            name="high-skill",
            description="High usage",
            steps=["Step"],
            tools_used=[],
        )
        writer.update_skill("low-skill", success_count=1)
        writer.update_skill("high-skill", success_count=10)
        skills = writer.list_skills()
        assert [s.name for s in skills] == ["high-skill", "low-skill"]

    def test_find_relevant_matches_triggers(self, tmp_path: Path):
        writer = SkillWriter(skills_dir=tmp_path / "skills")
        writer.create_skill(
            name="deploy-to-vps",
            description="Deploy to server",
            steps=["Step"],
            tools_used=[],
            triggers=["deploy", "vps"],
        )
        relevant = writer.find_relevant("please deploy this to my vps")
        assert len(relevant) == 1
        assert relevant[0].name == "deploy-to-vps"

    def test_record_success_updates_stats(self, tmp_path: Path):
        memory = MemoryStub()
        writer = SkillWriter(skills_dir=tmp_path / "skills", memory_router=memory)
        writer.create_skill(
            name="deploy-to-vps",
            description="Deploy to server",
            steps=["Step"],
            tools_used=[],
        )
        writer.record_success("deploy-to-vps", tokens_used=120)
        skill = writer.load_skill("deploy-to-vps")
        assert skill.success_count == 1
        assert skill.avg_tokens == 120
        assert skill.last_used > 0
        assert memory.saved and memory.saved[0][0] == "procedure"


class TestSkill:
    def test_to_context_block_format(self):
        skill = Skill(
            name="deploy-to-vps",
            description="Deploy to VPS",
            triggers=["deploy"],
            system_prompt="",
            steps=["Open SSH session", "Run deploy script"],
            tools_used=["ssh"],
            parameters=[],
            examples=[],
        )
        block = skill.to_context_block()
        assert "## Skill: deploy-to-vps" in block
        assert "Tools: ssh" in block
        assert "1. Open SSH session" in block

    def test_skill_file_path_correct(self):
        skill = Skill(
            name="deploy-to-vps",
            description="Deploy to VPS",
            triggers=[],
            system_prompt="",
            steps=["Step"],
            tools_used=[],
            parameters=[],
            examples=[],
        )
        normalized = skill.skill_file.replace("\\", "/")
        assert normalized.endswith("pawbot/skills/deploy-to-vps/skill.json")


class TestSkillLoader:
    def test_load_returns_relevant_skills(self, tmp_path: Path):
        skills_dir = tmp_path / "runtime-skills"
        writer = SkillWriter(skills_dir=skills_dir)
        writer.create_skill(
            name="deploy-to-vps",
            description="Deploy to server",
            steps=["Step"],
            tools_used=[],
            triggers=["deploy", "vps"],
        )
        loader = SkillLoader(
            workspace=tmp_path,
            config={"skills": {"skills_dir": str(skills_dir)}},
        )
        loaded = loader.load("deploy this to vps")
        assert len(loaded) == 1
        assert loaded[0].name == "deploy-to-vps"

    def test_get_returns_none_if_not_found(self, tmp_path: Path):
        loader = SkillLoader(
            workspace=tmp_path,
            config={"skills": {"skills_dir": str(tmp_path / "runtime-skills")}},
        )
        assert loader.get("does-not-exist") is None

    def test_to_context_string_formats_correctly(self, tmp_path: Path):
        skills_dir = tmp_path / "runtime-skills"
        writer = SkillWriter(skills_dir=skills_dir)
        skill = writer.create_skill(
            name="deploy-to-vps",
            description="Deploy to server",
            steps=["Step"],
            tools_used=["ssh"],
            triggers=["deploy"],
        )
        loader = SkillLoader(
            workspace=tmp_path,
            config={"skills": {"skills_dir": str(skills_dir)}},
        )
        text = loader.to_context_string([skill])
        assert text.startswith("# Relevant Skills")
        assert "## Skill: deploy-to-vps" in text


class TestSkillExecutor:
    def test_execute_builds_parameterised_task(self, tmp_path: Path):
        writer = SkillWriter(skills_dir=tmp_path / "skills", memory_router=MemoryStub())
        writer.create_skill(
            name="deploy-to-vps",
            description="Deploy to server",
            steps=["SSH in", "Run deploy"],
            tools_used=["ssh"],
            parameters=[{"name": "host", "type": "str", "required": True}],
        )
        loop = LoopStub()
        executor = SkillExecutor(loop, writer)
        result = executor.execute("deploy-to-vps", {"host": "prod.example.com"})
        assert result == "done"
        assert "Execute skill 'deploy-to-vps'" in loop.calls[0][0]
        assert "- host: prod.example.com" in loop.calls[0][0]

    def test_execute_injects_system_prompt(self, tmp_path: Path):
        writer = SkillWriter(skills_dir=tmp_path / "skills", memory_router=MemoryStub())
        writer.create_skill(
            name="deploy-to-vps",
            description="Deploy to server",
            steps=["Step"],
            tools_used=[],
            system_prompt="Always verify host",
        )
        loop = LoopStub()
        executor = SkillExecutor(loop, writer)
        executor.execute("deploy-to-vps")
        assert loop.calls[0][1]["skill_system_prompt"] == "Always verify host"
        assert loop.calls[0][1]["active_skill"] == "deploy-to-vps"


class TestLoRAPipeline:
    def test_collect_appends_to_jsonl(self, tmp_path: Path):
        dataset_path = tmp_path / "training" / "dataset.jsonl"
        pipeline = LoRAPipeline({"lora": {"dataset_path": str(dataset_path)}})
        pipeline.collect("Fix bug", "Patched bug", "coding_task", 100)
        lines = dataset_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["instruction"] == "Fix bug"
        assert payload["output"] == "Patched bug"

    def test_dataset_size_counts_lines(self, tmp_path: Path):
        dataset_path = tmp_path / "training" / "dataset.jsonl"
        pipeline = LoRAPipeline({"lora": {"dataset_path": str(dataset_path)}})
        pipeline.collect("a", "b", "coding_task", 10)
        pipeline.collect("c", "d", "coding_task", 12)
        assert pipeline.dataset_size() == 2

    def test_export_creates_copy(self, tmp_path: Path):
        dataset_path = tmp_path / "training" / "dataset.jsonl"
        export_path = tmp_path / "export.jsonl"
        pipeline = LoRAPipeline({"lora": {"dataset_path": str(dataset_path)}})
        pipeline.collect("a", "b", "coding_task", 10)
        out = pipeline.export_dataset(str(export_path))
        assert Path(out).exists()
        assert Path(out).read_text(encoding="utf-8") == dataset_path.read_text(encoding="utf-8")

    def test_training_stats_correct(self, tmp_path: Path):
        dataset_path = tmp_path / "training" / "dataset.jsonl"
        out_dir = tmp_path / "models" / "pawbot-lora"
        pipeline = LoRAPipeline(
            {"lora": {"dataset_path": str(dataset_path), "output_dir": str(out_dir), "min_examples": 2}}
        )
        pipeline.collect("a", "b", "coding_task", 10)
        stats = pipeline.training_stats()
        assert stats["dataset_size"] == 1
        assert stats["min_to_train"] == 2
        assert stats["ready_to_train"] is False
        assert stats["auto_train"] is False
        assert stats["model_exists"] is False

    def test_auto_train_disabled_by_default(self):
        pipeline = LoRAPipeline({})
        assert pipeline.auto_train is False

    def test_axolotl_config_written_correctly(self, tmp_path: Path):
        dataset_path = tmp_path / "training" / "dataset.jsonl"
        out_dir = tmp_path / "models" / "pawbot-lora"
        pipeline = LoRAPipeline(
            {"lora": {"dataset_path": str(dataset_path), "output_dir": str(out_dir)}}
        )
        config_path = Path(pipeline._write_axolotl_config())
        assert config_path.exists()
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["datasets"][0]["path"] == str(dataset_path)
        assert data["output_dir"] == str(out_dir)
        assert data["adapter"] == "lora"
