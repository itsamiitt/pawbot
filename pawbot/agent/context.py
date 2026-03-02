"""Context builder for assembling agent prompts.

Phase 3 — Context Builder Upgrades:
  3.1: Context Budget Enforcement (ContextBudget, count_tokens)
  3.2: Relevance-Based Context Loading (_load_relevant_episodes, _load_user_md_relevant)
  3.3: Prompt Cache Markers (_add_cache_markers, _track_cache_hits)
  3.4: Task-Aware Lazy Loading (TaskTypeDetector, TASK_CONTEXT_MAP)
"""

from __future__ import annotations

import base64
import mimetypes
import platform
import re
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from pawbot.agent.memory import MemoryStore
from pawbot.agent.skills import SkillsLoader

if TYPE_CHECKING:
    from pawbot.providers.base import LLMProvider
    from pawbot.session.manager import Session


# ─── Phase 3.1: Token Counting ──────────────────────────────────────────────


def count_tokens(text: str) -> int:
    """Returns approximate token count for text.

    Uses tiktoken if available, falls back to word-count approximation.
    """
    if not text:
        return 0
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except (ImportError, Exception):
        # Approximation: words * 1.3
        return int(len(text.split()) * 1.3)


# ─── Phase 3.1: Context Budget Enforcement ──────────────────────────────────
# These values are canonical — do not change without updating MASTER_REFERENCE.md

CONTEXT_BUDGET = {
    "system_prompt": 150,
    "user_facts": 100,
    "goal_state": 100,
    "reflections": 150,
    "episode_memory": 200,
    "procedure_memory": 150,
    "file_context": 500,
    "conversation": 200,
    "tool_results": 250,
}
CONTEXT_TOTAL_CEILING = 1800


class ContextBudget:
    """Enforces hard token limits per context section.

    Prevents unbounded context growth by tracking and limiting tokens
    across all sections. Supports optional summarization when content
    significantly exceeds budget.
    """

    def __init__(self):
        self.budgets = CONTEXT_BUDGET.copy()
        self.used: dict[str, int] = {k: 0 for k in CONTEXT_BUDGET}

    def enforce(
        self,
        section_name: str,
        content: str,
        summarizer=None,
    ) -> str:
        """Truncates content to section budget.

        If content exceeds budget: truncate at sentence boundary.
        If a summarizer fn is provided and content is > 2x budget: summarize first.
        Logs token usage every 10 messages.
        """
        if not content:
            self.used[section_name] = 0
            return ""

        limit = self.budgets.get(section_name, 200)
        token_count = count_tokens(content)

        if token_count <= limit:
            self.used[section_name] = token_count
            return content

        # If very large, summarize first
        if summarizer and token_count > limit * 2:
            try:
                content = summarizer(content, limit)
                token_count = count_tokens(content)
            except Exception as e:
                logger.warning("Summarizer failed for {}: {}", section_name, e)

        # Truncate at sentence boundary
        truncated = self._truncate_at_sentence(content, limit)
        self.used[section_name] = count_tokens(truncated)
        return truncated

    def _truncate_at_sentence(self, text: str, token_limit: int) -> str:
        """Cut at the last complete sentence that fits within token_limit."""
        sentences = re.split(r"(?<=[.!?])\s+", text)
        result = []
        total = 0
        for sentence in sentences:
            t = count_tokens(sentence)
            if total + t > token_limit:
                break
            result.append(sentence)
            total += t
        return " ".join(result) if result else text[: token_limit * 4]

    def total_used(self) -> int:
        """Return total tokens used across all sections."""
        return sum(self.used.values())

    def log_usage(self):
        """Called every 10 messages to log token distribution."""
        logger.info(
            "Context budget: {} | total={}/{}",
            self.used,
            self.total_used(),
            CONTEXT_TOTAL_CEILING,
        )


# ─── Phase 3.4: Task Type Detection ─────────────────────────────────────────
# Canonical task types — from MASTER_REFERENCE.md

TASK_TYPES = [
    "casual_chat",
    "coding_task",
    "deployment_task",
    "memory_task",
    "planning_task",
    "research_task",
    "debugging_task",
]

# What each task type needs from context
TASK_CONTEXT_MAP = {
    "casual_chat": ["system_prompt", "conversation"],
    "coding_task": [
        "system_prompt",
        "user_facts",
        "file_context",
        "episode_memory",
        "procedure_memory",
    ],
    "deployment_task": [
        "system_prompt",
        "file_context",
        "episode_memory",
        "procedure_memory",
    ],
    "memory_task": ["system_prompt", "episode_memory"],
    "planning_task": [
        "system_prompt",
        "goal_state",
        "episode_memory",
        "procedure_memory",
    ],
    "research_task": ["system_prompt", "tool_results"],
    "debugging_task": [
        "system_prompt",
        "file_context",
        "episode_memory",
        "tool_results",
    ],
}


class TaskTypeDetector:
    """Classifies a user message into one of the canonical TASK_TYPES.

    Uses keyword detection first (zero LLM cost), then optionally falls
    back to a local model classification if the message is ambiguous.
    """

    # Keyword → task type mapping (checked first — zero LLM cost)
    KEYWORD_MAP = {
        "coding_task": {
            "implement",
            "code",
            "function",
            "class",
            "refactor",
            "test",
            "bug",
            "syntax",
            "module",
            "script",
        },
        "deployment_task": {
            "deploy",
            "server",
            "nginx",
            "pm2",
            "docker",
            "ssl",
            "restart",
            "service",
            "production",
        },
        "debugging_task": {
            "error",
            "exception",
            "traceback",
            "crash",
            "failed",
            "broken",
            "debug",
            "not working",
            "stack trace",
        },
        "planning_task": {
            "plan",
            "strategy",
            "goal",
            "roadmap",
            "next steps",
            "prioritize",
            "schedule",
            "decide",
        },
        "research_task": {
            "search",
            "find",
            "look up",
            "research",
            "what is",
            "how does",
            "explain",
            "compare",
        },
        "memory_task": {
            "remember",
            "recall",
            "what did",
            "when did",
            "have i",
            "did we",
            "you said",
        },
    }

    def detect(self, message: str, model_router=None) -> str:
        """Returns one of TASK_TYPES.

        Tries keyword detection first (zero cost).
        Falls back to local model classification if ambiguous.
        """
        msg_lower = message.lower()
        scores: dict[str, int] = {}

        for task_type, keywords in self.KEYWORD_MAP.items():
            score = sum(1 for kw in keywords if kw in msg_lower)
            if score > 0:
                scores[task_type] = score

        if scores:
            return max(scores, key=scores.get)  # type: ignore[arg-type]

        # Ambiguous — use local model
        if model_router:
            return self._classify_with_model(message, model_router)

        return "casual_chat"  # safe default

    def _classify_with_model(self, message: str, model_router) -> str:
        """Use a local model to classify the task type."""
        prompt = (
            f"Classify this message as one of: "
            f"{', '.join(TASK_TYPES)}. "
            f"Respond with ONLY the task type, nothing else.\n"
            f"Message: {message[:200]}"
        )
        try:
            result = (
                model_router.call(
                    task_type="result_compress",
                    complexity=0.0,
                    prompt=prompt,
                )
                .strip()
                .lower()
            )
            return result if result in TASK_TYPES else "casual_chat"
        except Exception as e:  # noqa: F841
            return "casual_chat"


# ─── Phase 3.2: Key Concept Extraction ──────────────────────────────────────

# Technology names (simple keyword list)
TECH_KEYWORDS = {
    "python",
    "javascript",
    "typescript",
    "react",
    "postgres",
    "redis",
    "docker",
    "nginx",
    "fastapi",
    "django",
    "node",
    "sqlite",
    "mongodb",
    "aws",
    "gcp",
    "azure",
    "linux",
    "ubuntu",
    "git",
}

# Action verbs for coding/deployment tasks
ACTION_VERBS = {
    "deploy",
    "refactor",
    "debug",
    "fix",
    "install",
    "configure",
    "migrate",
    "backup",
    "rollback",
    "test",
    "build",
    "run",
}

# Task type → relevant heading keywords for USER.md filtering
RELEVANT_SECTIONS = {
    "coding_task": ["project", "code", "tech", "stack", "preference", "style"],
    "deployment_task": ["server", "deploy", "infra", "host", "domain"],
    "casual_chat": ["personal", "about", "preference"],
    "planning_task": ["goal", "project", "priority"],
}


# ─── Phase 3: ContextBuilder ────────────────────────────────────────────────


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent.

    Phase 3 enhances the original builder with:
    - Token budget enforcement per section
    - Task-type-aware lazy loading
    - Relevance-based episode and user-facts loading
    - Anthropic prompt cache markers
    """

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "IDENTITY.md"]
    _RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"

    def __init__(self, workspace: Path, memory_router=None, model_router=None):
        self.workspace = workspace
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace)
        self.memory_router = memory_router
        self.model_router = model_router
        self._task_detector = TaskTypeDetector()

        # Paths for SOUL.md and USER.md
        self.soul_md_path = workspace / "SOUL.md"
        self.user_md_path = workspace / "USER.md"

    # ── Public interface (preserved from original) ──────────────────────

    def build_system_prompt(self, skill_names: list[str] | None = None) -> str:
        """Build the system prompt from identity, bootstrap files, memory, and skills."""
        parts = [self._get_identity()]

        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)

        memory = self.memory.get_memory_context()
        if memory:
            parts.append(f"# Memory\n\n{memory}")

        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        if skill_names:
            selected = []
            for name in skill_names:
                try:
                    skill = self.skills.get(name)
                except Exception as e:  # noqa: F841
                    skill = None
                if skill is not None:
                    selected.append(skill)
            if selected:
                selected_context = self.skills.to_context_string(selected)
                if selected_context:
                    parts.append(selected_context)

        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            parts.append(f"""# Skills

The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.
Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.

{skills_summary}""")

        return "\n\n---\n\n".join(parts)

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        session: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call.

        If a session object is provided, uses Phase 3 enhanced context
        building with budget enforcement and task-aware loading.
        Otherwise falls back to original behavior for compatibility.
        """
        if session is not None:
            return self._build_enhanced_messages(
                history=history,
                current_message=current_message,
                session=session,
                skill_names=skill_names,
                media=media,
                channel=channel,
                chat_id=chat_id,
            )

        # Original behavior for backward compatibility
        return [
            {"role": "system", "content": self.build_system_prompt(skill_names)},
            *history,
            {"role": "user", "content": self._build_runtime_context(channel, chat_id)},
            {"role": "user", "content": self._build_user_content(current_message, media)},
        ]

    def build(self, message: str, session) -> list[dict[str, Any]]:
        """Phase 3 enhanced context build.

        Detects task type, loads only needed context sections under budget,
        applies cache markers for Anthropic, and returns the assembled messages.
        """
        budget = ContextBudget()

        # ── STEP 1: Detect task type ─────────────────────────────────────
        task_type = self._task_detector.detect(message, self.model_router)
        session.metadata["task_type"] = task_type

        # ── STEP 2: Determine which sections to load ─────────────────────
        context_mode = session.metadata.get("context_mode", "standard")
        needed_sections = TASK_CONTEXT_MAP.get(
            task_type, ["system_prompt", "conversation"]
        )

        # System 2 always loads more
        if context_mode == "full":
            needed_sections = list(CONTEXT_BUDGET.keys())
        elif context_mode == "minimal":
            needed_sections = ["system_prompt", "conversation"]

        # ── STEP 3: Load and enforce budget for each needed section ──────
        context_parts: dict[str, str] = {}

        if "system_prompt" in needed_sections:
            raw = self._load_soul_md()
            context_parts["system_prompt"] = budget.enforce("system_prompt", raw)

        if "user_facts" in needed_sections:
            raw = self._load_user_md_relevant(message, session)
            context_parts["user_facts"] = budget.enforce("user_facts", raw)

        if "reflections" in needed_sections:
            reflections = session.metadata.get("pre_task_reflections", [])
            raw = self._format_reflections(reflections)
            context_parts["reflections"] = budget.enforce("reflections", raw)

        if "episode_memory" in needed_sections:
            raw = self._load_relevant_episodes(message, self.memory_router)
            context_parts["episode_memory"] = budget.enforce("episode_memory", raw)

        if "procedure_memory" in needed_sections:
            proc = session.metadata.get("pre_task_procedure")
            if proc:
                raw = self._format_procedure(proc)
                context_parts["procedure_memory"] = budget.enforce(
                    "procedure_memory", raw
                )

        if "file_context" in needed_sections:
            raw = session.metadata.get("file_context_raw", "")
            context_parts["file_context"] = budget.enforce("file_context", raw)

        if "conversation" in needed_sections:
            raw = self._load_conversation(session, max_messages=4)
            context_parts["conversation"] = budget.enforce("conversation", raw)

        if "tool_results" in needed_sections:
            raw = session.metadata.get("last_tool_results", "")
            context_parts["tool_results"] = budget.enforce("tool_results", raw)

        if "goal_state" in needed_sections:
            raw = session.metadata.get("goal_state", "")
            context_parts["goal_state"] = budget.enforce("goal_state", raw)

        # ── STEP 4: Assemble final messages array ────────────────────────
        messages = self._assemble_messages(context_parts, message, session)

        # ── STEP 5: Add cache markers for Anthropic ──────────────────────
        provider_type = self._get_provider_type()
        messages = self._add_cache_markers(messages, provider_type)

        # ── STEP 6: Log usage periodically ───────────────────────────────
        msg_count = session.metadata.get("message_count", 0)
        if msg_count > 0 and msg_count % 10 == 0:
            budget.log_usage()

        return messages

    # ── Private: original methods (preserved) ───────────────────────────

    def _get_identity(self) -> str:
        """Get the core identity section."""
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return f"""# pawbot 🐈

You are pawbot, a helpful AI assistant.

## Runtime
{runtime}

## Workspace
Your workspace is at: {workspace_path}
- Long-term memory: {workspace_path}/memory/MEMORY.md (write important facts here)
- History log: {workspace_path}/memory/HISTORY.md (grep-searchable). Each entry starts with [YYYY-MM-DD HH:MM].
- Custom skills: {workspace_path}/skills/{{skill-name}}/SKILL.md

## pawbot Guidelines
- State intent before tool calls, but NEVER predict or claim results before receiving them.
- Before modifying a file, read it first. Do not assume files or directories exist.
- After writing or editing a file, re-read it if accuracy matters.
- If a tool call fails, analyze the error before retrying with a different approach.
- Ask for clarification when the request is ambiguous.

Reply directly with text for conversations. Only use the 'message' tool to send to a specific chat channel."""

    @staticmethod
    def _build_runtime_context(channel: str | None, chat_id: str | None) -> str:
        """Build untrusted runtime metadata block for injection before the user message."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = time.strftime("%Z") or "UTC"
        lines = [f"Current Time: {now} ({tz})"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        return ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines)

    def _load_bootstrap_files(self) -> str:
        """Load all bootstrap files from workspace."""
        parts = []

        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text

        images = []
        for path in media:
            p = Path(path)
            mime, _ = mimetypes.guess_type(path)
            if not p.is_file() or not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(p.read_bytes()).decode()
            images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

        if not images:
            return text
        return images + [{"type": "text", "text": text}]

    def add_tool_result(
        self, messages: list[dict[str, Any]],
        tool_call_id: str, tool_name: str, result: str,
    ) -> list[dict[str, Any]]:
        """Add a tool result to the message list."""
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result})
        return messages

    def add_assistant_message(
        self, messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
        thinking_blocks: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """Add an assistant message to the message list."""
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        if reasoning_content is not None:
            msg["reasoning_content"] = reasoning_content
        if thinking_blocks:
            msg["thinking_blocks"] = thinking_blocks
        messages.append(msg)
        return messages

    # ── Phase 3.1: SOUL.md and USER.md loading ──────────────────────────

    def _load_soul_md(self) -> str:
        """Load the SOUL.md system prompt file."""
        if self.soul_md_path.exists():
            try:
                return self.soul_md_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("Failed to load SOUL.md: {}", e)
        return ""

    # ── Phase 3.2: Relevance-Based Context Loading ──────────────────────

    def _load_relevant_episodes(self, message: str, memory_router) -> str:
        """Returns top 3 semantically relevant episodes as compressed paragraphs.

        Replaces recency-based loading with semantic similarity search.
        """
        if memory_router is None:
            return ""

        try:
            # Extract key concepts for better search
            concepts = self._extract_key_concepts(message)
            search_query = " ".join(concepts) if concepts else message[:100]

            # Search ChromaDB via MemoryRouter
            episodes = memory_router.search(query=search_query, limit=5)

            # Filter by salience and type
            relevant = [
                e
                for e in episodes
                if e.get("type") == "episode" and e.get("salience", 1.0) > 0.3
            ][:3]  # top 3

            # Format each as one paragraph maximum
            formatted = []
            for ep in relevant:
                text = ep.get("text", "")
                if not text:
                    content = ep.get("content", {})
                    if isinstance(content, dict):
                        text = content.get("text", "")
                    elif isinstance(content, str):
                        text = content
                formatted.append(text[:300])  # one compressed paragraph

            return "\n---\n".join(formatted) if formatted else ""
        except Exception as e:
            logger.warning("Episode loading failed: {}", e)
            return ""

    def _extract_key_concepts(self, text: str) -> list[str]:
        """Extracts noun phrases, function names, tech names, action verbs.

        Simple regex + keyword approach — zero LLM cost.
        """
        words = text.split()

        # Function/file names: CamelCase, snake_case.ext, path/patterns
        code_refs = re.findall(
            r"\b[A-Z][a-zA-Z]{2,}\b|\b\w+\.\w{2,4}\b|\b\w+_\w+\b", text
        )

        # Technology names
        tech_found = [w.lower() for w in words if w.lower() in TECH_KEYWORDS]

        # Action verbs for coding/deployment tasks
        actions = [w.lower() for w in words if w.lower() in ACTION_VERBS]

        return list(set(code_refs[:5] + tech_found[:3] + actions[:3]))

    def _load_user_md_relevant(self, message: str, session=None) -> str:
        """Only returns USER.md sections relevant to current task type.

        A coding task doesn't need the user's meeting schedule.
        """
        if not self.user_md_path.exists():
            return ""

        try:
            content = self.user_md_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to load USER.md: {}", e)
            return ""

        task_type = "general"
        if session is not None:
            task_type = session.metadata.get("task_type", "general") if hasattr(session, "metadata") else "general"

        # Split by markdown headings
        sections = re.split(r"\n## ", content)

        # Task type → relevant heading keywords
        relevant_keywords = RELEVANT_SECTIONS.get(task_type, ["preference"])
        relevant = []
        for section in sections:
            section_lower = section.lower()
            if any(kw in section_lower for kw in relevant_keywords):
                relevant.append(section)

        return (
            "\n## ".join(relevant) if relevant else sections[0] if sections else ""
        )

    # ── Phase 3.3: Prompt Cache Markers ─────────────────────────────────

    def _add_cache_markers(
        self, messages: list[dict], provider_type: str
    ) -> list[dict]:
        """Wraps cacheable content blocks with Anthropic cache_control markers.

        Only modifies messages if provider_type == "anthropic".
        Cacheable sections: system prompt, user facts, code index.
        """
        if provider_type != "anthropic":
            return messages  # passthrough for non-Anthropic providers

        modified = []
        for msg in messages:
            if msg.get("role") == "system":
                # Wrap system message content for caching
                content = msg.get("content", "")
                if count_tokens(content) >= 100:
                    msg = {
                        "role": "system",
                        "content": [
                            {
                                "type": "text",
                                "text": content,
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                    }
                    logger.debug(
                        "Cache marker applied to system prompt: {} tokens",
                        count_tokens(content),
                    )
            modified.append(msg)
        return modified

    @staticmethod
    def _track_cache_hits(api_response: dict):
        """Log cache hit rate from Anthropic API response usage field."""
        usage = api_response.get("usage", {})
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_write = usage.get("cache_creation_input_tokens", 0)
        total = usage.get("input_tokens", 1)
        if cache_read > 0:
            hit_rate = cache_read / total
            logger.info(
                "Prompt cache: {} tokens cached ({:.0%} hit rate)",
                cache_read,
                hit_rate,
            )

    # ── Phase 3.4: Helpers for build() ──────────────────────────────────

    def _format_reflections(self, reflections: list[dict]) -> str:
        """Format reflections for context injection."""
        if not reflections:
            return ""
        lines = ["LESSONS FROM PAST FAILURES:"]
        for r in reflections[:3]:
            lines.append(
                f"Rule: {r.get('rule', '')} — Context: {r.get('lesson', '')}"
            )
        return "\n".join(lines)

    def _format_procedure(self, proc: dict) -> str:
        """Format a proven procedure for context injection."""
        if not proc:
            return ""
        lines = [f"KNOWN WORKING PROCEDURE: {proc.get('name', '')}"]
        for i, step in enumerate(proc.get("steps", []), 1):
            lines.append(f"  {i}. {step}")
        return "\n".join(lines)

    def _load_conversation(self, session, max_messages: int = 4) -> str:
        """Load recent conversation history for context."""
        try:
            history = session.get_history(max_messages=max_messages)
            if not history:
                return ""
            parts = []
            for msg in history:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                if content:
                    parts.append(f"{role}: {content[:200]}")
            return "\n".join(parts)
        except Exception as e:  # noqa: F841
            return ""

    def _assemble_messages(
        self, context_parts: dict[str, str], message: str, session
    ) -> list[dict[str, Any]]:
        """Assemble context parts into a messages array for the LLM."""
        messages: list[dict[str, Any]] = []

        # System message: combine system_prompt with any user_facts and reflections
        system_text_parts = []
        if sp := context_parts.get("system_prompt"):
            system_text_parts.append(sp)
        if uf := context_parts.get("user_facts"):
            system_text_parts.append(f"## User Facts\n{uf}")
        if rf := context_parts.get("reflections"):
            system_text_parts.append(f"## Reflections\n{rf}")
        if pm := context_parts.get("procedure_memory"):
            system_text_parts.append(f"## Procedures\n{pm}")
        if em := context_parts.get("episode_memory"):
            system_text_parts.append(f"## Relevant Memories\n{em}")
        if fc := context_parts.get("file_context"):
            system_text_parts.append(f"## File Context\n{fc}")
        if gs := context_parts.get("goal_state"):
            system_text_parts.append(f"## Goal State\n{gs}")

        if system_text_parts:
            messages.append(
                {"role": "system", "content": "\n\n".join(system_text_parts)}
            )

        # Conversation history (if loaded)
        if conv := context_parts.get("conversation"):
            messages.append({"role": "user", "content": f"Recent conversation:\n{conv}"})

        # Tool results (if loaded)
        if tr := context_parts.get("tool_results"):
            messages.append({"role": "user", "content": f"Tool results:\n{tr}"})

        # Current user message
        messages.append({"role": "user", "content": message})

        return messages

    def _build_enhanced_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        session,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build messages using Phase 3 enhanced context building.

        Uses budget enforcement and task-aware loading while preserving
        the original message structure.
        """
        budget = ContextBudget()

        # Detect task type
        task_type = self._task_detector.detect(current_message, self.model_router)
        session.metadata["task_type"] = task_type

        # Build system prompt with budget enforcement
        system_prompt_raw = self.build_system_prompt(skill_names)
        system_prompt = budget.enforce("system_prompt", system_prompt_raw)

        # Build message list
        messages = [
            {"role": "system", "content": system_prompt},
            *history,
            {"role": "user", "content": self._build_runtime_context(channel, chat_id)},
            {"role": "user", "content": self._build_user_content(current_message, media)},
        ]

        # Add cache markers for Anthropic
        provider_type = self._get_provider_type()
        messages = self._add_cache_markers(messages, provider_type)

        # Log usage periodically
        msg_count = session.metadata.get("message_count", 0)
        if msg_count > 0 and msg_count % 10 == 0:
            budget.log_usage()

        return messages

    def _get_provider_type(self) -> str:
        """Get the current provider type for cache marker decisions."""
        if self.model_router is not None:
            try:
                return self.model_router.current_provider_type()
            except Exception as e:  # noqa: F841
                pass
        return "unknown"
