# PHASE 13 — SKILL SYSTEM & LoRA FINE-TUNING PIPELINE
> **Cross-reference:** [MASTER_REFERENCE.md](./MASTER_REFERENCE.md)
> **Implementation Days:** Weeks 5–8 (13.1 SkillWriter), Weeks 5–8 (13.2 LoRA pipeline)
> **Primary Files:** `~/nanobot/agent/skills.py` (enhance), `~/nanobot/skills/` (bundled skills)
> **Test File:** `~/nanobot/tests/test_skills.py`
> **Depends on:** Phase 1 (MemoryRouter — save skill execution episodes), Phase 2 (AgentLoop — skill loading at task start), Phase 4 (ModelRouter — LoRA fine-tuned model routing)

---

## BEFORE YOU START — READ THESE FILES

```bash
cat ~/nanobot/agent/skills.py       # existing SkillLoader — preserve all public methods
cat ~/nanobot/skills/               # existing bundled skills (github, weather, tmux)
cat ~/nanobot/agent/loop.py         # how AgentLoop currently loads and calls skills
cat ~/.nanobot/config.json          # current skill configuration
```

**Existing interfaces to preserve:** `SkillLoader.load()`, `SkillLoader.get(name)`, and however AgentLoop calls skills today. Keep those signatures identical.

---

## WHAT YOU ARE BUILDING

Two major features:

1. **SkillWriter** — The agent can author new reusable skills at runtime, save them to disk, and load them in future sessions. Skills are structured prompt+tool bundles that capture how to perform a recurring task.

2. **LoRA Fine-Tuning Pipeline** — Collects the agent's successful task completions into a training dataset and fine-tunes a local Ollama model on them, making the agent progressively better at tasks it has done before.

---

## CANONICAL NAMES — ALL NEW CLASSES IN THIS PHASE

| Class Name | File | Purpose |
|---|---|---|
| `SkillLoader` | `agent/skills.py` | Enhanced skill loading and execution (existing, extend) |
| `SkillWriter` | `agent/skills.py` | Creates and saves new skills at runtime |
| `Skill` | `agent/skills.py` | Dataclass representing one skill |
| `SkillExecutor` | `agent/skills.py` | Runs a skill with given parameters |
| `LoRAPipeline` | `agent/skills.py` | Fine-tuning dataset collector and trainer |
| `TrainingExample` | `agent/skills.py` | Single training example dataclass |

---

## FEATURE 13.1 — SKILL WRITER

### `Skill` dataclass

```python
from dataclasses import dataclass, field
import time

@dataclass
class Skill:
    """
    A reusable capability bundle.
    Stored as a .json file in ~/nanobot/skills/{name}/skill.json
    """
    name: str                         # unique kebab-case identifier: "deploy-to-vps"
    description: str                  # one-line summary shown to the agent
    triggers: list[str]               # phrases that suggest this skill ("deploy", "vps")
    system_prompt: str                # role prompt injected when skill is active
    steps: list[str]                  # ordered natural-language steps
    tools_used: list[str]             # MCP tools this skill typically calls
    parameters: list[dict]            # [{"name": "host", "type": "str", "required": True}]
    examples: list[dict]              # [{"input": "...", "output": "..."}]
    success_count: int = 0
    avg_tokens: int = 0
    created_at: int = field(default_factory=lambda: int(time.time()))
    last_used: int = 0
    author: str = "agent"             # "agent" | "user"
    version: str = "1.0"

    @property
    def skill_dir(self) -> str:
        return os.path.expanduser(f"~/nanobot/skills/{self.name}")

    @property
    def skill_file(self) -> str:
        return os.path.join(self.skill_dir, "skill.json")

    def to_context_block(self) -> str:
        """
        Format this skill as a context injection for AgentLoop.
        Returns a concise string describing how to use this skill.
        """
        steps_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(self.steps))
        tools_text = ", ".join(self.tools_used) if self.tools_used else "none"
        return (
            f"## Skill: {self.name}\n"
            f"{self.description}\n"
            f"Tools: {tools_text}\n"
            f"Steps:\n{steps_text}"
        )
```

### `SkillWriter` class

```python
import json, os, logging
logger = logging.getLogger("nanobot")

class SkillWriter:
    """
    Allows the agent to create new skills and save them to disk.
    Skills are created after a successful novel System 2 task completion.
    """

    SKILLS_DIR = os.path.expanduser("~/nanobot/skills")

    def create_skill(
        self,
        name: str,
        description: str,
        steps: list[str],
        tools_used: list[str],
        triggers: list[str] = [],
        parameters: list[dict] = [],
        examples: list[dict] = [],
        system_prompt: str = "",
    ) -> Skill:
        """
        Create a new skill and save it to disk.
        Returns the created Skill object.
        Raises ValueError if name already exists (use update_skill to modify).
        """
        # Validate name format
        import re
        if not re.match(r'^[a-z0-9-]+$', name):
            raise ValueError(f"Skill name must be kebab-case: '{name}'")

        skill = Skill(
            name=name,
            description=description,
            triggers=triggers or [name],
            system_prompt=system_prompt,
            steps=steps,
            tools_used=tools_used,
            parameters=parameters,
            examples=examples,
        )

        if os.path.exists(skill.skill_file):
            raise ValueError(f"Skill '{name}' already exists. Use update_skill().")

        self._save(skill)
        logger.info(f"Skill created: {name}")
        return skill

    def update_skill(self, name: str, **updates) -> Skill:
        """Update fields on an existing skill."""
        skill = self.load_skill(name)
        for key, value in updates.items():
            if hasattr(skill, key):
                setattr(skill, key, value)
        skill.version = f"{float(skill.version) + 0.1:.1f}"
        self._save(skill)
        logger.info(f"Skill updated: {name} -> v{skill.version}")
        return skill

    def delete_skill(self, name: str) -> bool:
        """Delete a skill. Returns True if deleted."""
        import shutil
        skill_dir = os.path.expanduser(f"~/nanobot/skills/{name}")
        if os.path.exists(skill_dir):
            shutil.rmtree(skill_dir)
            logger.info(f"Skill deleted: {name}")
            return True
        return False

    def load_skill(self, name: str) -> Skill:
        """Load a skill from disk by name."""
        skill_file = os.path.expanduser(f"~/nanobot/skills/{name}/skill.json")
        if not os.path.exists(skill_file):
            raise FileNotFoundError(f"Skill not found: {name}")
        with open(skill_file) as f:
            data = json.load(f)
        return Skill(**data)

    def list_skills(self) -> list[Skill]:
        """Return all skills sorted by success_count descending."""
        skills = []
        if not os.path.exists(self.SKILLS_DIR):
            return skills
        for entry in os.scandir(self.SKILLS_DIR):
            if entry.is_dir():
                skill_file = os.path.join(entry.path, "skill.json")
                if os.path.exists(skill_file):
                    try:
                        skills.append(self.load_skill(entry.name))
                    except Exception as e:
                        logger.warning(f"Failed to load skill {entry.name}: {e}")
        return sorted(skills, key=lambda s: s.success_count, reverse=True)

    def find_relevant(self, task: str, limit: int = 3) -> list[Skill]:
        """
        Return skills most relevant to the given task.
        Matching: trigger phrase in task, or skill name in task.
        """
        task_lower = task.lower()
        candidates = []
        for skill in self.list_skills():
            score = 0
            if skill.name in task_lower:
                score += 3
            for trigger in skill.triggers:
                if trigger.lower() in task_lower:
                    score += 2
            if score > 0:
                candidates.append((score, skill))
        candidates.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in candidates[:limit]]

    def record_success(self, name: str, tokens_used: int):
        """Called after a skill completes successfully. Updates stats."""
        try:
            skill = self.load_skill(name)
            skill.success_count += 1
            skill.last_used = int(time.time())
            # Rolling average for avg_tokens
            if skill.avg_tokens == 0:
                skill.avg_tokens = tokens_used
            else:
                skill.avg_tokens = int(0.8 * skill.avg_tokens + 0.2 * tokens_used)
            self._save(skill)
        except Exception as e:
            logger.warning(f"Failed to record skill success for {name}: {e}")

    def _save(self, skill: Skill):
        """Persist skill to disk."""
        os.makedirs(skill.skill_dir, exist_ok=True)
        import dataclasses
        with open(skill.skill_file, "w") as f:
            json.dump(dataclasses.asdict(skill), f, indent=2)
```

### Enhanced `SkillLoader` class

Enhance the existing `SkillLoader` to integrate with `SkillWriter`:

```python
class SkillLoader:
    """
    Loads skills at task start. Injects relevant skills into agent context.
    Extended to work with dynamically created skills.
    """

    def __init__(self, config: dict):
        self.config = config
        self.writer = SkillWriter()
        self._cache: dict[str, Skill] = {}

    def load(self, task: str) -> list[Skill]:
        """
        Load skills relevant to the given task.
        Called by AgentLoop at the start of System 2 tasks.
        Returns list of Skill objects to inject into context.
        """
        relevant = self.writer.find_relevant(task, limit=3)
        for skill in relevant:
            logger.info(f"Skill loaded for task: {skill.name}")
        return relevant

    def get(self, name: str) -> Optional[Skill]:
        """Get a specific skill by name."""
        try:
            return self.writer.load_skill(name)
        except FileNotFoundError:
            return None

    def to_context_string(self, skills: list[Skill]) -> str:
        """Format skills list as context block for AgentLoop prompt."""
        if not skills:
            return ""
        blocks = [s.to_context_block() for s in skills]
        return "# Relevant Skills\n" + "\n\n".join(blocks)
```

### `SkillExecutor` class

```python
class SkillExecutor:
    """
    Runs a skill with given parameters.
    Wraps AgentLoop.process() with skill context injected.
    """

    def __init__(self, agent_loop, skill_writer: SkillWriter):
        self.loop = agent_loop
        self.writer = skill_writer

    def execute(self, skill_name: str, params: dict = {}) -> str:
        """Execute a named skill with the given parameters."""
        skill = self.writer.load_skill(skill_name)

        # Build parameterised task from skill steps
        steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(skill.steps))
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

        result = self.loop.process(task, context=context)
        self.writer.record_success(skill_name, tokens_used=0)  # tokens tracked by loop
        return result
```

---

## FEATURE 13.2 — LoRA FINE-TUNING PIPELINE

### `TrainingExample` dataclass

```python
@dataclass
class TrainingExample:
    """A single training example for LoRA fine-tuning."""
    instruction: str       # the task/question
    response: str          # the agent's successful response
    task_type: str         # from TASK_TYPES canonical list
    tokens: int
    quality_score: float   # 0.0–1.0, estimated from success signals
    created_at: int = field(default_factory=lambda: int(time.time()))
    skill_name: str = ""   # if this came from skill execution
```

### `LoRAPipeline` class

```python
class LoRAPipeline:
    """
    Collects successful task completions as training data.
    When enough examples accumulate, fine-tunes local Ollama model.

    Uses Axolotl (https://github.com/axolotl-ai-cloud/axolotl) for LoRA training.
    Base model: llama3.1:8b (same as OllamaProvider default)
    Output: ~/.nanobot/models/nanobot-lora/
    """

    DATASET_PATH = os.path.expanduser("~/.nanobot/training/dataset.jsonl")
    MODEL_OUTPUT_PATH = os.path.expanduser("~/.nanobot/models/nanobot-lora")
    MIN_EXAMPLES_TO_TRAIN = 100  # don't fine-tune with fewer examples

    def __init__(self, config: dict):
        self.config = config
        lora_cfg = config.get("lora", {})
        self.min_examples = lora_cfg.get("min_examples", self.MIN_EXAMPLES_TO_TRAIN)
        self.auto_train = lora_cfg.get("auto_train", False)  # safety: off by default
        os.makedirs(os.path.dirname(self.DATASET_PATH), exist_ok=True)

    def collect(self, instruction: str, response: str,
                task_type: str, tokens: int, quality_score: float = 0.8):
        """
        Add a successful task completion to the training dataset.
        Appends to JSONL file atomically.
        """
        example = TrainingExample(
            instruction=instruction,
            response=response,
            task_type=task_type,
            tokens=tokens,
            quality_score=quality_score,
        )
        # Convert to Alpaca format for training
        alpaca = {
            "instruction": example.instruction,
            "input": "",
            "output": example.response,
        }
        with open(self.DATASET_PATH, "a") as f:
            f.write(json.dumps(alpaca) + "\n")

        count = self.dataset_size()
        logger.info(f"Training example collected ({count} total)")

        if self.auto_train and count >= self.min_examples and count % 100 == 0:
            self._trigger_training()

    def dataset_size(self) -> int:
        """Return current number of training examples."""
        if not os.path.exists(self.DATASET_PATH):
            return 0
        with open(self.DATASET_PATH) as f:
            return sum(1 for line in f if line.strip())

    def export_dataset(self, output_path: str = None) -> str:
        """Export dataset to specified path (default: timestamp copy)."""
        import shutil
        ts = int(time.time())
        dest = output_path or os.path.expanduser(
            f"~/.nanobot/training/dataset_{ts}.jsonl"
        )
        shutil.copy(self.DATASET_PATH, dest)
        return dest

    def _trigger_training(self):
        """
        Trigger LoRA fine-tuning in a background subprocess.
        Requires Axolotl installed: pip install axolotl
        """
        def _train():
            logger.info(f"LoRA training starting with {self.dataset_size()} examples")
            config_path = self._write_axolotl_config()
            import subprocess
            result = subprocess.run(
                ["python", "-m", "axolotl.cli.train", config_path],
                capture_output=True, text=True, timeout=3600
            )
            if result.returncode == 0:
                logger.info("LoRA training completed successfully")
            else:
                logger.warning(f"LoRA training failed: {result.stderr[:500]}")

        thread = threading.Thread(target=_train, daemon=True)
        thread.start()

    def _write_axolotl_config(self) -> str:
        """Write Axolotl training config YAML."""
        import yaml
        config = {
            "base_model": "meta-llama/Meta-Llama-3.1-8B",
            "model_type": "LlamaForCausalLM",
            "tokenizer_type": "LlamaTokenizer",
            "datasets": [{"path": self.DATASET_PATH, "type": "alpaca"}],
            "output_dir": self.MODEL_OUTPUT_PATH,
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
        config_path = os.path.expanduser("~/.nanobot/training/axolotl_config.yaml")
        with open(config_path, "w") as f:
            yaml.dump(config, f)
        return config_path

    def training_stats(self) -> dict:
        """Return training pipeline statistics."""
        return {
            "dataset_size": self.dataset_size(),
            "min_to_train": self.min_examples,
            "ready_to_train": self.dataset_size() >= self.min_examples,
            "auto_train": self.auto_train,
            "model_exists": os.path.exists(self.MODEL_OUTPUT_PATH),
            "dataset_path": self.DATASET_PATH,
        }
```

---

## CONFIG KEYS TO ADD

```json
{
  "skills": {
    "enabled": true,
    "auto_create_after_novel_system2": true,
    "skills_dir": "~/nanobot/skills"
  },
  "lora": {
    "enabled": false,
    "auto_train": false,
    "min_examples": 100,
    "base_model": "meta-llama/Meta-Llama-3.1-8B",
    "dataset_path": "~/.nanobot/training/dataset.jsonl",
    "output_dir": "~/.nanobot/models/nanobot-lora"
  }
}
```

---

## TEST REQUIREMENTS

**File:** `~/nanobot/tests/test_skills.py`

```python
class TestSkillWriter:
    def test_create_skill_saves_to_disk()
    def test_duplicate_name_raises()
    def test_invalid_name_format_raises()
    def test_update_increments_version()
    def test_delete_removes_directory()
    def test_load_skill_from_disk()
    def test_list_sorted_by_success_count()
    def test_find_relevant_matches_triggers()
    def test_record_success_updates_stats()

class TestSkill:
    def test_to_context_block_format()
    def test_skill_file_path_correct()

class TestSkillLoader:
    def test_load_returns_relevant_skills()
    def test_get_returns_none_if_not_found()
    def test_to_context_string_formats_correctly()

class TestSkillExecutor:
    def test_execute_builds_parameterised_task()
    def test_execute_injects_system_prompt()

class TestLoRAPipeline:
    def test_collect_appends_to_jsonl()
    def test_dataset_size_counts_lines()
    def test_export_creates_copy()
    def test_training_stats_correct()
    def test_auto_train_disabled_by_default()
    def test_axolotl_config_written_correctly()
```

---

## CROSS-REFERENCES

- **Phase 1** (MemoryRouter): `SkillWriter.record_success()` should call `memory.save("procedure", skill_to_dict(skill))` to persist skill stats in memory alongside the file system
- **Phase 2** (AgentLoop): AgentLoop calls `skill_loader.load(task)` at start of System 2 tasks and injects result into context. Also calls `lora_pipeline.collect(instruction, response, task_type, tokens)` after every successful System 2 completion
- **Phase 4** (ModelRouter): Once LoRA training produces a model at `~/.nanobot/models/nanobot-lora/`, ModelRouter should route `task_type="local_finetune"` to it
- **Phase 15** (Observability): Wrap `SkillExecutor.execute()` and `LoRAPipeline._trigger_training()` with trace spans

All canonical names are in [MASTER_REFERENCE.md](./MASTER_REFERENCE.md).
