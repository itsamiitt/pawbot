"""Skill loading, writing, execution, and LoRA pipeline utilities."""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

# Default built-in skills directory (relative to this file)
BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def _to_config_dict(config: Any | None) -> dict[str, Any]:
    if config is None:
        return {}
    if isinstance(config, dict):
        return config
    if hasattr(config, "model_dump"):
        try:
            data = config.model_dump(by_alias=False)
            if isinstance(data, dict):
                return data
        except Exception as e:  # noqa: F841
            pass
    if hasattr(config, "dict"):
        try:
            data = config.dict()
            if isinstance(data, dict):
                return data
        except Exception as e:  # noqa: F841
            pass
    return {}


@dataclass
class Skill:
    """
    A reusable capability bundle.

    By default, a skill is stored at:
    ~/pawbot/skills/{name}/skill.json
    """

    name: str
    description: str
    triggers: list[str]
    system_prompt: str
    steps: list[str]
    tools_used: list[str]
    parameters: list[dict[str, Any]]
    examples: list[dict[str, Any]]
    success_count: int = 0
    avg_tokens: int = 0
    created_at: int = field(default_factory=lambda: int(time.time()))
    last_used: int = 0
    author: str = "agent"
    version: str = "1.0"

    @property
    def skill_dir(self) -> str:
        return os.path.expanduser(f"~/pawbot/skills/{self.name}")

    @property
    def skill_file(self) -> str:
        return os.path.join(self.skill_dir, "skill.json")

    def to_context_block(self) -> str:
        steps_text = "\n".join(f"  {idx + 1}. {step}" for idx, step in enumerate(self.steps))
        tools_text = ", ".join(self.tools_used) if self.tools_used else "none"
        return (
            f"## Skill: {self.name}\n"
            f"{self.description}\n"
            f"Tools: {tools_text}\n"
            f"Steps:\n{steps_text}"
        )


class SkillWriter:
    """
    Creates and persists runtime skills.

    Skills are created after successful novel work and reused later.
    """

    SKILLS_DIR = os.path.expanduser("~/pawbot/skills")

    def __init__(
        self,
        config: Any | None = None,
        memory_router: Any | None = None,
        skills_dir: str | os.PathLike[str] | None = None,
    ):
        self.config = _to_config_dict(config)
        cfg_dir = self.config.get("skills", {}).get("skills_dir", self.SKILLS_DIR)
        self.skills_dir = Path(os.path.expanduser(str(skills_dir or cfg_dir)))
        self.memory = memory_router

    def create_skill(
        self,
        name: str,
        description: str,
        steps: list[str],
        tools_used: list[str],
        triggers: list[str] | None = None,
        parameters: list[dict[str, Any]] | None = None,
        examples: list[dict[str, Any]] | None = None,
        system_prompt: str = "",
    ) -> Skill:
        """Create a new skill and persist it to disk."""
        if not re.match(r"^[a-z0-9-]+$", name):
            raise ValueError(f"Skill name must be kebab-case: '{name}'")
        if not description.strip():
            raise ValueError("Skill description is required")
        if not steps:
            raise ValueError("Skill must contain at least one step")
        if self._skill_path(name).exists():
            raise ValueError(f"Skill '{name}' already exists. Use update_skill().")

        skill = Skill(
            name=name,
            description=description.strip(),
            triggers=list(triggers or [name]),
            system_prompt=system_prompt,
            steps=list(steps),
            tools_used=list(tools_used),
            parameters=list(parameters or []),
            examples=list(examples or []),
        )
        self._save(skill)
        logger.info("Skill created: {}", name)
        return skill

    def update_skill(self, name: str, **updates: Any) -> Skill:
        """Update fields on an existing skill."""
        skill = self.load_skill(name)
        for key, value in updates.items():
            if hasattr(skill, key):
                setattr(skill, key, value)

        try:
            version = float(skill.version)
        except Exception as e:  # noqa: F841
            version = 1.0
        skill.version = f"{version + 0.1:.1f}"

        self._save(skill)
        logger.info("Skill updated: {} -> v{}", name, skill.version)
        return skill

    def delete_skill(self, name: str) -> bool:
        """Delete a skill directory."""
        skill_dir = self.skills_dir / name
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
            logger.info("Skill deleted: {}", name)
            return True
        return False

    def load_skill(self, name: str) -> Skill:
        """Load one skill from disk."""
        path = self._skill_path(name)
        if not path.exists():
            raise FileNotFoundError(f"Skill not found: {name}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return Skill(**data)

    def list_skills(self) -> list[Skill]:
        """List all persisted skills sorted by success_count descending."""
        if not self.skills_dir.exists():
            return []

        skills: list[Skill] = []
        for entry in self.skills_dir.iterdir():
            if not entry.is_dir():
                continue
            path = entry / "skill.json"
            if not path.exists():
                continue
            try:
                skills.append(self.load_skill(entry.name))
            except Exception as exc:
                logger.warning("Failed to load skill {}: {}", entry.name, exc)

        return sorted(skills, key=lambda s: s.success_count, reverse=True)

    def find_relevant(self, task: str, limit: int = 3) -> list[Skill]:
        """Find skills relevant to a task."""
        task_lower = task.lower()
        candidates: list[tuple[int, Skill]] = []

        for skill in self.list_skills():
            score = 0
            if skill.name.lower() in task_lower:
                score += 3
            if skill.description and skill.description.lower() in task_lower:
                score += 1
            for trigger in skill.triggers:
                if trigger.lower() in task_lower:
                    score += 2
            if score > 0:
                candidates.append((score, skill))

        candidates.sort(key=lambda item: (item[0], item[1].success_count), reverse=True)
        return [skill for _, skill in candidates[:limit]]

    def record_success(self, name: str, tokens_used: int) -> None:
        """Record a successful skill execution."""
        try:
            skill = self.load_skill(name)
        except FileNotFoundError:
            return
        except Exception as exc:
            logger.warning("Failed loading skill for success update ({}): {}", name, exc)
            return

        skill.success_count += 1
        skill.last_used = int(time.time())
        tokens_used = max(0, int(tokens_used))

        if skill.avg_tokens == 0:
            skill.avg_tokens = tokens_used
        else:
            skill.avg_tokens = int(0.8 * skill.avg_tokens + 0.2 * tokens_used)

        try:
            self._save(skill)
            self._save_skill_memory(skill)
        except Exception as exc:
            logger.warning("Failed to record skill success for {}: {}", name, exc)

    def _skill_path(self, name: str) -> Path:
        return self.skills_dir / name / "skill.json"

    def _save(self, skill: Skill) -> None:
        path = self._skill_path(skill.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        from pawbot.utils.fs import atomic_write_json
        atomic_write_json(path, dataclasses.asdict(skill))

    def _save_skill_memory(self, skill: Skill) -> None:
        """Persist skill stats to memory as a procedure (best effort)."""
        payload = {
            "name": skill.name,
            "description": skill.description,
            "triggers": skill.triggers,
            "steps": skill.steps,
            "tools_used": skill.tools_used,
            "success_count": skill.success_count,
            "avg_tokens": skill.avg_tokens,
            "last_used": skill.last_used,
        }

        try:
            if self.memory is not None and hasattr(self.memory, "save"):
                self.memory.save("procedure", payload)
                return

            from pawbot.agent import memory as memory_module

            save_fn = getattr(memory_module, "save", None)
            if callable(save_fn):
                save_fn("procedure", payload)
        except Exception as exc:
            logger.warning("Failed to persist skill '{}' to memory: {}", skill.name, exc)


class SkillLoader:
    """
    Loads and formats skills.

    Supports:
    - Runtime JSON skills (SkillWriter)
    - Existing markdown skills under workspace/built-in directories
    """

    def __init__(
        self,
        workspace: Path | Any | None = None,
        builtin_skills_dir: Path | None = None,
        config: Any | None = None,
        memory_router: Any | None = None,
    ):
        cfg = _to_config_dict(config)
        # Compatibility: allow SkillLoader(config_dict) usage.
        if workspace is None:
            workspace = Path.cwd()
        if not isinstance(workspace, Path) and isinstance(workspace, dict) and not cfg:
            cfg = dict(workspace)
            workspace = Path.cwd()

        self.config = cfg
        self.workspace = Path(workspace)
        self.workspace_skills = self.workspace / "skills"
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR
        self.writer = SkillWriter(config=self.config, memory_router=memory_router)
        self._cache: dict[str, Skill] = {}

    # Phase 13 API
    def load(self, task: str) -> list[Skill]:
        """Load skills relevant to a task."""
        relevant = self.writer.find_relevant(task, limit=3)
        for skill in relevant:
            self._cache[skill.name] = skill
            logger.info("Skill loaded for task: {}", skill.name)
        return relevant

    def get(self, name: str) -> Skill | None:
        """Get a skill by name."""
        if name in self._cache:
            return self._cache[name]
        try:
            skill = self.writer.load_skill(name)
            self._cache[name] = skill
            return skill
        except FileNotFoundError:
            return None
        except Exception as exc:
            logger.warning("Failed to load skill '{}': {}", name, exc)
            return None

    def to_context_string(self, skills: list[Skill]) -> str:
        """Format skill blocks for context injection."""
        if not skills:
            return ""
        blocks = [skill.to_context_block() for skill in skills]
        return "# Relevant Skills\n" + "\n\n".join(blocks)

    # Legacy interface preserved for existing ContextBuilder usage.
    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """List markdown and runtime skills."""
        skills: list[dict[str, str]] = []
        seen: set[str] = set()

        # Runtime JSON skills.
        for skill in self.writer.list_skills():
            path = self.writer.skills_dir / skill.name / "skill.json"
            skills.append({"name": skill.name, "path": str(path), "source": "runtime"})
            seen.add(skill.name)

        # Workspace markdown skills.
        if self.workspace_skills.exists():
            for skill_dir in self.workspace_skills.iterdir():
                if not skill_dir.is_dir() or skill_dir.name in seen:
                    continue
                skill_file = skill_dir / "SKILL.md"
                if skill_file.exists():
                    skills.append(
                        {"name": skill_dir.name, "path": str(skill_file), "source": "workspace"}
                    )
                    seen.add(skill_dir.name)

        # Built-in markdown skills.
        if self.builtin_skills and self.builtin_skills.exists():
            for skill_dir in self.builtin_skills.iterdir():
                if not skill_dir.is_dir() or skill_dir.name in seen:
                    continue
                skill_file = skill_dir / "SKILL.md"
                if skill_file.exists():
                    skills.append(
                        {"name": skill_dir.name, "path": str(skill_file), "source": "builtin"}
                    )
                    seen.add(skill_dir.name)

        if not filter_unavailable:
            return skills

        filtered: list[dict[str, str]] = []
        for skill in skills:
            if skill["source"] == "runtime":
                filtered.append(skill)
                continue
            if self._check_requirements(self._get_skill_meta(skill["name"])):
                filtered.append(skill)
        return filtered

    def load_skill(self, name: str) -> str | None:
        """Load markdown skill content by name."""
        workspace_skill = self.workspace_skills / name / "SKILL.md"
        if workspace_skill.exists():
            return workspace_skill.read_text(encoding="utf-8")

        if self.builtin_skills:
            builtin_skill = self.builtin_skills / name / "SKILL.md"
            if builtin_skill.exists():
                return builtin_skill.read_text(encoding="utf-8")

        return None

    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """Load named skills for context."""
        parts: list[str] = []
        for name in skill_names:
            dynamic = self.get(name)
            if dynamic is not None:
                parts.append(dynamic.to_context_block())
                continue

            content = self.load_skill(name)
            if content:
                content = self._strip_frontmatter(content)
                parts.append(f"### Skill: {name}\n\n{content}")
        return "\n\n---\n\n".join(parts) if parts else ""

    def build_skills_summary(self) -> str:
        """Build XML summary of skills for progressive loading."""
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""

        def escape_xml(raw: str) -> str:
            return raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        lines = ["<skills>"]
        for skill in all_skills:
            name = skill["name"]
            source = skill.get("source", "")
            path = skill.get("path", "")
            desc = self._get_skill_description(name)
            available = True if source == "runtime" else self._check_requirements(self._get_skill_meta(name))

            lines.append(f"  <skill available=\"{str(available).lower()}\">")
            lines.append(f"    <name>{escape_xml(name)}</name>")
            lines.append(f"    <description>{escape_xml(desc)}</description>")
            lines.append(f"    <location>{escape_xml(path)}</location>")
            if not available and source != "runtime":
                missing = self._get_missing_requirements(self._get_skill_meta(name))
                if missing:
                    lines.append(f"    <requires>{escape_xml(missing)}</requires>")
            lines.append("  </skill>")
        lines.append("</skills>")
        return "\n".join(lines)

    def get_always_skills(self) -> list[str]:
        """Get markdown skills marked always=true with requirements met."""
        result: list[str] = []
        for skill in self.list_skills(filter_unavailable=True):
            if skill.get("source") == "runtime":
                continue
            meta = self.get_skill_metadata(skill["name"]) or {}
            skill_meta = self._parse_pawbot_metadata(meta.get("metadata", ""))
            if skill_meta.get("always") or meta.get("always"):
                result.append(skill["name"])
        return result

    def get_skill_metadata(self, name: str) -> dict[str, Any] | None:
        """Get parsed frontmatter metadata from markdown skills."""
        content = self.load_skill(name)
        if not content or not content.startswith("---"):
            return None

        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return None

        metadata: dict[str, Any] = {}
        for line in match.group(1).split("\n"):
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip().strip("\"'")
        return metadata

    def _strip_frontmatter(self, content: str) -> str:
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end():].strip()
        return content

    def _parse_pawbot_metadata(self, raw: str) -> dict[str, Any]:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data.get("pawbot", data.get("openclaw", {}))
        except (json.JSONDecodeError, TypeError):
            pass
        return {}

    def _get_skill_meta(self, name: str) -> dict[str, Any]:
        meta = self.get_skill_metadata(name) or {}
        return self._parse_pawbot_metadata(meta.get("metadata", ""))

    def _check_requirements(self, skill_meta: dict[str, Any]) -> bool:
        requires = skill_meta.get("requires", {})
        for binary in requires.get("bins", []):
            if not shutil.which(binary):
                return False
        for env_name in requires.get("env", []):
            if not os.environ.get(env_name):
                return False
        return True

    def _get_missing_requirements(self, skill_meta: dict[str, Any]) -> str:
        missing: list[str] = []
        requires = skill_meta.get("requires", {})
        for binary in requires.get("bins", []):
            if not shutil.which(binary):
                missing.append(f"CLI: {binary}")
        for env_name in requires.get("env", []):
            if not os.environ.get(env_name):
                missing.append(f"ENV: {env_name}")
        return ", ".join(missing)

    def _get_skill_description(self, name: str) -> str:
        dynamic = self.get(name)
        if dynamic is not None and dynamic.description:
            return dynamic.description

        meta = self.get_skill_metadata(name)
        if meta and meta.get("description"):
            return str(meta["description"])
        return name


class SkillsLoader(SkillLoader):
    """Backward-compatible alias for existing imports."""


class SkillExecutor:
    """Runs a skill with parameters through the agent loop."""

    def __init__(self, agent_loop: Any, skill_writer: SkillWriter):
        self.loop = agent_loop
        self.writer = skill_writer

    def execute(self, skill_name: str, params: dict[str, Any] | None = None) -> str:
        skill = self.writer.load_skill(skill_name)
        params = params or {}
        steps_text = "\n".join(f"{idx + 1}. {step}" for idx, step in enumerate(skill.steps))
        params_text = "\n".join(f"- {k}: {v}" for k, v in params.items()) if params else "none"
        task = (
            f"Execute skill '{skill_name}': {skill.description}\n"
            f"Parameters:\n{params_text}\n"
            f"Follow these steps:\n{steps_text}"
        )
        context = {
            "active_skill": skill_name,
            "skill_system_prompt": skill.system_prompt,
        }

        result = self._call_loop(task, context)
        self.writer.record_success(skill_name, tokens_used=0)
        return result

    def _call_loop(self, task: str, context: dict[str, Any]) -> str:
        process = getattr(self.loop, "process", None)
        if callable(process):
            outcome = process(task, context=context)
            resolved = self._resolve(outcome)
            return self._to_text(resolved)

        process_direct = getattr(self.loop, "process_direct", None)
        if callable(process_direct):
            outcome = process_direct(
                task,
                session_key=f"skill:{int(time.time())}",
                channel="cli",
                chat_id="direct",
            )
            resolved = self._resolve(outcome)
            return self._to_text(resolved)

        raise RuntimeError("Agent loop does not provide process/process_direct")

    @staticmethod
    def _resolve(value: Any) -> Any:
        if inspect.isawaitable(value):
            return asyncio.run(value)
        return value

    @staticmethod
    def _to_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        content = getattr(value, "content", None)
        if isinstance(content, str):
            return content
        return str(value)


@dataclass
class TrainingExample:
    """A single LoRA training example."""

    instruction: str
    response: str
    task_type: str
    tokens: int
    quality_score: float
    created_at: int = field(default_factory=lambda: int(time.time()))
    skill_name: str = ""


class LoRAPipeline:
    """
    Collects successful completions into an Alpaca-style JSONL dataset
    and can trigger background fine-tuning via Axolotl.
    """

    DATASET_PATH = os.path.expanduser("~/.pawbot/training/dataset.jsonl")
    MODEL_OUTPUT_PATH = os.path.expanduser("~/.pawbot/models/pawbot-lora")
    MIN_EXAMPLES_TO_TRAIN = 100

    def __init__(self, config: Any | None):
        cfg = _to_config_dict(config)
        lora_cfg = cfg.get("lora", {})

        self.enabled = bool(lora_cfg.get("enabled", False))
        self.auto_train = bool(lora_cfg.get("auto_train", False))
        self.min_examples = int(lora_cfg.get("min_examples", self.MIN_EXAMPLES_TO_TRAIN))
        self.base_model = str(lora_cfg.get("base_model", "meta-llama/Meta-Llama-3.1-8B"))
        self.dataset_path = Path(
            os.path.expanduser(str(lora_cfg.get("dataset_path", self.DATASET_PATH)))
        )
        self.model_output_path = Path(
            os.path.expanduser(str(lora_cfg.get("output_dir", self.MODEL_OUTPUT_PATH)))
        )
        self._lock = threading.Lock()
        self.dataset_path.parent.mkdir(parents=True, exist_ok=True)

    def collect(
        self,
        instruction: str,
        response: str,
        task_type: str,
        tokens: int,
        quality_score: float = 0.8,
        skill_name: str = "",
    ) -> None:
        """Append one training example to dataset.jsonl."""
        example = TrainingExample(
            instruction=instruction,
            response=response,
            task_type=task_type,
            tokens=int(tokens),
            quality_score=float(quality_score),
            skill_name=skill_name,
        )
        alpaca = {
            "instruction": example.instruction,
            "input": "",
            "output": example.response,
        }
        with self._lock:
            with self.dataset_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(alpaca, ensure_ascii=False) + "\n")

        count = self.dataset_size()
        logger.info("Training example collected ({} total)", count)
        if self.auto_train and count >= self.min_examples and count % 100 == 0:
            self._trigger_training()

    def dataset_size(self) -> int:
        """Return number of JSONL lines in dataset."""
        if not self.dataset_path.exists():
            return 0
        with self.dataset_path.open("r", encoding="utf-8") as file:
            return sum(1 for line in file if line.strip())

    def export_dataset(self, output_path: str | None = None) -> str:
        """Copy dataset.jsonl to a target location."""
        timestamp = int(time.time())
        destination = Path(
            os.path.expanduser(output_path or f"~/.pawbot/training/dataset_{timestamp}.jsonl")
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.dataset_path.exists():
            shutil.copy(self.dataset_path, destination)
        else:
            destination.write_text("", encoding="utf-8")
        return str(destination)

    def _trigger_training(self) -> None:
        """Start Axolotl training in a daemon thread."""

        def _train() -> None:
            logger.info("LoRA training starting with {} examples", self.dataset_size())
            config_path = self._write_axolotl_config()
            try:
                result = subprocess.run(
                    ["python", "-m", "axolotl.cli.train", config_path],
                    capture_output=True,
                    text=True,
                    timeout=3600,
                )
            except FileNotFoundError as exc:
                logger.warning("LoRA training skipped (axolotl not installed): {}", exc)
                return
            except Exception as exc:
                logger.warning("LoRA training failed to start: {}", exc)
                return

            if result.returncode == 0:
                logger.info("LoRA training completed successfully")
            else:
                logger.warning("LoRA training failed: {}", result.stderr[:500])

        thread = threading.Thread(target=_train, daemon=True, name="lora-training")
        thread.start()

    def _write_axolotl_config(self) -> str:
        """Write Axolotl config as JSON (valid YAML 1.2)."""
        config = {
            "base_model": self.base_model,
            "model_type": "LlamaForCausalLM",
            "tokenizer_type": "LlamaTokenizer",
            "datasets": [{"path": str(self.dataset_path), "type": "alpaca"}],
            "output_dir": str(self.model_output_path),
            "adapter": "lora",
            "lora_r": 16,
            "lora_alpha": 32,
            "lora_dropout": 0.05,
            "lora_target_modules": ["q_proj", "v_proj"],
            "sequence_len": 2048,
            "micro_batch_size": 2,
            "gradient_accumulation_steps": 4,
            "num_epochs": 3,
            "learning_rate": 0.0002,
            "train_on_inputs": False,
            "fp16": True,
        }
        config_path = self.dataset_path.parent / "axolotl_config.yaml"
        from pawbot.utils.fs import atomic_write_json
        atomic_write_json(config_path, config)
        return str(config_path)

    def training_stats(self) -> dict[str, Any]:
        """Return LoRA dataset/training status."""
        size = self.dataset_size()
        return {
            "dataset_size": size,
            "min_to_train": self.min_examples,
            "ready_to_train": size >= self.min_examples,
            "auto_train": self.auto_train,
            "model_exists": self.model_output_path.exists(),
            "dataset_path": str(self.dataset_path),
        }


__all__ = [
    "Skill",
    "SkillWriter",
    "SkillLoader",
    "SkillsLoader",
    "SkillExecutor",
    "TrainingExample",
    "LoRAPipeline",
]
