# PHASE 3 — CONTEXT BUILDER UPGRADES
> **Cross-reference:** [MASTER_REFERENCE.md](./MASTER_REFERENCE.md)  
> **Implementation Days:** Day 3 (3.1), Day 4 (3.3), Day 6 (3.4), Weeks 5–8 (3.2)  
> **Primary File:** `~/nanobot/agent/context.py`  
> **Test File:** `~/nanobot/tests/test_context.py`  
> **Depends on:** Phase 1 (memory.py — MemoryRouter), Phase 2 (session state keys)

---

## BEFORE YOU START — READ THESE FILES

```bash
cat ~/nanobot/agent/context.py     # find ContextBuilder class and build() method
cat ~/nanobot/agent/loop.py        # find where context.build() is called
cat ~/nanobot/workspace/SOUL.md    # understand system prompt content
cat ~/nanobot/workspace/USER.md    # understand user facts content
```

**Existing interface to preserve:** The `ContextBuilder.build()` method signature and return type. `loop.py` calls this method and passes the result directly to the LLM API call.

---

## FEATURE 3.1 — CONTEXT BUDGET ENFORCEMENT

**New class:** `ContextBudget`  
**New function:** `count_tokens`  
**Location:** `agent/context.py`  
**Dependency to add:** `tiktoken>=0.5.0` in `pyproject.toml`

### Token Counter

```python
def count_tokens(text: str) -> int:
    """
    Returns approximate token count for text.
    Uses tiktoken if available, falls back to word-count approximation.
    """
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except ImportError:
        # Approximation: words * 1.3
        return int(len(text.split()) * 1.3)
```

### Budget Class

```python
# These values are canonical — do not change without updating MASTER_REFERENCE.md
CONTEXT_BUDGET = {
    "system_prompt":    150,
    "user_facts":       100,
    "goal_state":       100,
    "reflections":      150,
    "episode_memory":   200,
    "procedure_memory": 150,
    "file_context":     500,
    "conversation":     200,
    "tool_results":     250,
}
CONTEXT_TOTAL_CEILING = 1800

class ContextBudget:
    def __init__(self):
        self.budgets = CONTEXT_BUDGET.copy()
        self.used = {k: 0 for k in CONTEXT_BUDGET}

    def enforce(self, section_name: str, content: str,
                summarizer=None) -> str:
        """
        Truncates content to section budget.
        If content exceeds budget: truncate at sentence boundary.
        If a summarizer fn is provided and content is > 2x budget: summarize first.
        Logs token usage every 10 messages.
        """
        limit = self.budgets.get(section_name, 200)
        token_count = count_tokens(content)

        if token_count <= limit:
            self.used[section_name] = token_count
            return content

        # If very large, summarize first
        if summarizer and token_count > limit * 2:
            content = summarizer(content, limit)
            token_count = count_tokens(content)

        # Truncate at sentence boundary
        truncated = self._truncate_at_sentence(content, limit)
        self.used[section_name] = count_tokens(truncated)
        return truncated

    def _truncate_at_sentence(self, text: str, token_limit: int) -> str:
        """Cut at the last complete sentence that fits within token_limit."""
        import re
        sentences = re.split(r'(?<=[.!?])\s+', text)
        result = []
        total = 0
        for sentence in sentences:
            t = count_tokens(sentence)
            if total + t > token_limit:
                break
            result.append(sentence)
            total += t
        return " ".join(result) if result else text[:token_limit * 4]

    def total_used(self) -> int:
        return sum(self.used.values())

    def log_usage(self):
        """Called every 10 messages to log token distribution."""
        logger.info(f"Context budget: {self.used} | total={self.total_used()}/{CONTEXT_TOTAL_CEILING}")
```

### Integration into ContextBuilder.build()

Wrap every section with `budget.enforce()`:

```python
def build(self, message: str, session) -> list[dict]:
    budget = ContextBudget()

    system_prompt_raw = self._load_soul_md()
    system_prompt = budget.enforce("system_prompt", system_prompt_raw)

    user_facts_raw = self._load_user_md_relevant(message)
    user_facts = budget.enforce("user_facts", user_facts_raw)

    # ... etc for each section ...

    # Log every 10 messages
    msg_count = session.get("message_count", 0)
    if msg_count % 10 == 0:
        budget.log_usage()
```

---

## FEATURE 3.2 — RELEVANCE-BASED CONTEXT LOADING

**Location:** `agent/context.py`  
**Replaces:** Recency-based episode loading with semantic similarity  
**Depends on:** Phase 1 (ChromaEpisodeStore.search())  
**Implementation day:** Weeks 5–8

### Episode Loading

**Old behavior:** Load last 3 episodes by timestamp  
**New behavior:** Load top 3 episodes by semantic similarity to current message

```python
def _load_relevant_episodes(self, message: str, memory_router) -> str:
    """
    Returns top 3 semantically relevant episodes as compressed paragraphs.
    Replaces recency-based loading.
    """
    # Extract key concepts for better search
    concepts = self._extract_key_concepts(message)
    search_query = " ".join(concepts) if concepts else message[:100]

    # Search ChromaDB via MemoryRouter
    episodes = memory_router.search(query=search_query, limit=5)

    # Filter by salience and type
    relevant = [
        e for e in episodes
        if e.get("type") == "episode"
        and e.get("salience", 1.0) > 0.3
    ][:3]  # top 3

    # Format each as one paragraph maximum
    formatted = []
    for ep in relevant:
        text = ep.get("text", ep.get("content", {}).get("text", ""))
        formatted.append(text[:300])  # one compressed paragraph

    return "\n---\n".join(formatted)

def _extract_key_concepts(self, text: str) -> list[str]:
    """
    Extracts noun phrases, function names, tech names, action verbs.
    Simple regex + keyword approach — zero LLM cost.
    """
    import re
    words = text.split()

    # Function/file names: CamelCase, snake_case.ext, path/patterns
    code_refs = re.findall(r'\b[A-Z][a-zA-Z]{2,}\b|\b\w+\.\w{2,4}\b|\b\w+_\w+\b', text)

    # Technology names (simple keyword list)
    TECH_KEYWORDS = {
        "python", "javascript", "typescript", "react", "postgres", "redis",
        "docker", "nginx", "fastapi", "django", "node", "sqlite", "mongodb",
        "aws", "gcp", "azure", "linux", "ubuntu", "git"
    }
    tech_found = [w.lower() for w in words if w.lower() in TECH_KEYWORDS]

    # Action verbs for coding/deployment tasks
    ACTION_VERBS = {
        "deploy", "refactor", "debug", "fix", "install", "configure",
        "migrate", "backup", "rollback", "test", "build", "run"
    }
    actions = [w.lower() for w in words if w.lower() in ACTION_VERBS]

    return list(set(code_refs[:5] + tech_found[:3] + actions[:3]))
```

### USER.md Relevance Loading

Parse USER.md into sections by topic headings, include only relevant ones:

```python
def _load_user_md_relevant(self, message: str) -> str:
    """
    Only returns USER.md sections relevant to current task type.
    A coding task doesn't need the user's meeting schedule.
    """
    task_type = self.session.get("task_type", "general")
    with open(self.user_md_path) as f:
        content = f.read()

    # Split by markdown headings
    import re
    sections = re.split(r'\n## ', content)

    # Task type → relevant heading keywords
    RELEVANT_SECTIONS = {
        "coding_task":     ["project", "code", "tech", "stack", "preference", "style"],
        "deployment_task": ["server", "deploy", "infra", "host", "domain"],
        "casual_chat":     ["personal", "about", "preference"],
        "planning_task":   ["goal", "project", "priority"],
    }

    relevant_keywords = RELEVANT_SECTIONS.get(task_type, ["preference"])
    relevant = []
    for section in sections:
        section_lower = section.lower()
        if any(kw in section_lower for kw in relevant_keywords):
            relevant.append(section)

    return "\n## ".join(relevant) if relevant else sections[0]  # at minimum return first section
```

---

## FEATURE 3.3 — PROMPT CACHE MARKERS

**Location:** `agent/context.py`  
**Only applies to:** Anthropic API calls — check provider type before applying  
**Minimum tokens to cache:** 100 (Anthropic requirement)

### Implementation

```python
def _add_cache_markers(self, messages: list[dict], provider_type: str) -> list[dict]:
    """
    Wraps cacheable content blocks with Anthropic cache_control markers.
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
                            "cache_control": {"type": "ephemeral"}
                        }
                    ]
                }
                logger.debug(f"Cache marker applied to system prompt: "
                             f"{count_tokens(content)} tokens")
        modified.append(msg)
    return modified
```

Cache hit tracking:

```python
def _track_cache_hits(self, api_response: dict):
    """Log cache hit rate from Anthropic API response usage field."""
    usage = api_response.get("usage", {})
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_write = usage.get("cache_creation_input_tokens", 0)
    total = usage.get("input_tokens", 1)
    if cache_read > 0:
        hit_rate = cache_read / total
        logger.info(f"Prompt cache: {cache_read} tokens cached ({hit_rate:.0%} hit rate)")
```

---

## FEATURE 3.4 — TASK-AWARE LAZY LOADING

**New class:** `TaskTypeDetector`  
**Location:** `agent/context.py`  
**Goal:** Load only what the task type actually needs — nothing more

### Task Type Detector

```python
# Canonical task types — from MASTER_REFERENCE.md
TASK_TYPES = ["casual_chat", "coding_task", "deployment_task",
              "memory_task", "planning_task", "research_task", "debugging_task"]

# What each task type needs from context
TASK_CONTEXT_MAP = {
    "casual_chat":     ["system_prompt", "conversation"],
    "coding_task":     ["system_prompt", "user_facts", "file_context",
                        "episode_memory", "procedure_memory"],
    "deployment_task": ["system_prompt", "file_context",
                        "episode_memory", "procedure_memory"],
    "memory_task":     ["system_prompt", "episode_memory"],
    "planning_task":   ["system_prompt", "goal_state",
                        "episode_memory", "procedure_memory"],
    "research_task":   ["system_prompt", "tool_results"],
    "debugging_task":  ["system_prompt", "file_context",
                        "episode_memory", "tool_results"],
}

class TaskTypeDetector:
    # Keyword → task type mapping (checked first — zero LLM cost)
    KEYWORD_MAP = {
        "coding_task":     {"implement", "code", "function", "class", "refactor",
                           "test", "bug", "syntax", "module", "script"},
        "deployment_task": {"deploy", "server", "nginx", "pm2", "docker",
                           "ssl", "restart", "service", "production"},
        "debugging_task":  {"error", "exception", "traceback", "crash", "failed",
                           "broken", "debug", "not working", "stack trace"},
        "planning_task":   {"plan", "strategy", "goal", "roadmap", "next steps",
                           "prioritize", "schedule", "decide"},
        "research_task":   {"search", "find", "look up", "research", "what is",
                           "how does", "explain", "compare"},
        "memory_task":     {"remember", "recall", "what did", "when did",
                           "have i", "did we", "you said"},
    }

    def detect(self, message: str, model_router=None) -> str:
        """
        Returns one of TASK_TYPES.
        Tries keyword detection first (zero cost).
        Falls back to local model classification if ambiguous.
        """
        msg_lower = message.lower()
        scores = {}

        for task_type, keywords in self.KEYWORD_MAP.items():
            score = sum(1 for kw in keywords if kw in msg_lower)
            if score > 0:
                scores[task_type] = score

        if scores:
            return max(scores, key=scores.get)

        # Ambiguous — use local model
        if model_router:
            return self._classify_with_model(message, model_router)

        return "casual_chat"  # safe default

    def _classify_with_model(self, message: str, model_router) -> str:
        prompt = (f"Classify this message as one of: "
                  f"{', '.join(TASK_TYPES)}. "
                  f"Respond with ONLY the task type, nothing else.\n"
                  f"Message: {message[:200]}")
        try:
            result = model_router.call(
                task_type="result_compress",
                complexity=0.0,
                prompt=prompt
            ).strip().lower()
            return result if result in TASK_TYPES else "casual_chat"
        except Exception:
            return "casual_chat"
```

### Integration into ContextBuilder.build()

```python
def build(self, message: str, session) -> list[dict]:
    budget = ContextBudget()

    # ── STEP 1: Detect task type ─────────────────────────────────────
    detector = TaskTypeDetector()
    task_type = detector.detect(message, self.model_router)
    session.set("task_type", task_type)  # Phase 2 (loop.py) reads this

    # ── STEP 2: Determine which sections to load ─────────────────────
    context_mode = session.get("context_mode", "standard")  # set by Phase 2
    needed_sections = TASK_CONTEXT_MAP.get(task_type, ["system_prompt", "conversation"])

    # System 2 always loads more
    if context_mode == "full":
        needed_sections = list(CONTEXT_BUDGET.keys())
    elif context_mode == "minimal":
        needed_sections = ["system_prompt", "conversation"]

    # ── STEP 3: Load and enforce budget for each needed section ──────
    context_parts = {}

    if "system_prompt" in needed_sections:
        raw = self._load_soul_md()
        context_parts["system_prompt"] = budget.enforce("system_prompt", raw)

    if "user_facts" in needed_sections:
        raw = self._load_user_md_relevant(message)
        context_parts["user_facts"] = budget.enforce("user_facts", raw)

    if "reflections" in needed_sections:
        reflections = session.get("pre_task_reflections", [])
        raw = self._format_reflections(reflections)
        context_parts["reflections"] = budget.enforce("reflections", raw)

    if "episode_memory" in needed_sections:
        raw = self._load_relevant_episodes(message, self.memory)
        context_parts["episode_memory"] = budget.enforce("episode_memory", raw)

    if "procedure_memory" in needed_sections:
        proc = session.get("pre_task_procedure")
        if proc:
            raw = self._format_procedure(proc)
            context_parts["procedure_memory"] = budget.enforce("procedure_memory", raw)

    if "file_context" in needed_sections:
        raw = session.get("file_context_raw", "")
        context_parts["file_context"] = budget.enforce("file_context", raw)

    if "conversation" in needed_sections:
        raw = self._load_conversation(session, max_messages=4)
        context_parts["conversation"] = budget.enforce("conversation", raw)

    if "tool_results" in needed_sections:
        raw = session.get("last_tool_results", "")
        context_parts["tool_results"] = budget.enforce("tool_results", raw)

    # ── STEP 4: Assemble final messages array ────────────────────────
    system_text = self._assemble_system_text(context_parts)
    messages = self._assemble_messages(context_parts, message, session)

    # ── STEP 5: Add cache markers for Anthropic ──────────────────────
    provider_type = self.model_router.current_provider_type()
    messages = self._add_cache_markers(messages, provider_type)

    return messages

def _format_reflections(self, reflections: list[dict]) -> str:
    if not reflections:
        return ""
    lines = ["LESSONS FROM PAST FAILURES:"]
    for r in reflections[:3]:
        lines.append(f"Rule: {r.get('rule', '')} — Context: {r.get('lesson', '')}")
    return "\n".join(lines)

def _format_procedure(self, proc: dict) -> str:
    if not proc:
        return ""
    lines = [f"KNOWN WORKING PROCEDURE: {proc.get('name', '')}"]
    for i, step in enumerate(proc.get("steps", []), 1):
        lines.append(f"  {i}. {step}")
    return "\n".join(lines)
```

---

## TEST REQUIREMENTS

**File:** `~/nanobot/tests/test_context.py`

```python
class TestContextBudget:
    def test_enforce_within_limit_passthrough()
    def test_enforce_truncates_at_sentence_boundary()
    def test_enforce_summarizes_when_2x_over()
    def test_total_used_sum()
    def test_log_usage_called_every_10_messages()

class TestTaskTypeDetector:
    def test_coding_keywords_detected()
    def test_deployment_keywords_detected()
    def test_debugging_error_words_detected()
    def test_ambiguous_falls_back_to_local_model()
    def test_unknown_returns_casual_chat()

class TestContextBuilder:
    def test_casual_chat_loads_minimal_sections()
    def test_coding_task_loads_file_context()
    def test_system_2_loads_all_sections()
    def test_cache_markers_applied_for_anthropic()
    def test_cache_markers_not_applied_for_openai()
    def test_relevant_episodes_loaded_not_recent()
    def test_user_md_sections_filtered_by_task_type()
    def test_total_tokens_under_ceiling()
```

---

## CROSS-REFERENCES

- **Phase 1** provides: `memory_router.search()` (used in `_load_relevant_episodes`)
- **Phase 2** writes to session: `complexity_score`, `system_path`, `context_mode`, `task_type`, `pre_task_reflections`, `pre_task_procedure`, `file_context_raw`, `last_tool_results`
- **Phase 4** provides: `model_router.call()` (used in `_classify_with_model`), `model_router.current_provider_type()` (used for cache markers)
- **Phase 7** writes to session: `file_context_raw` (code sections for context)
- **Phase 16** (CLI) does NOT interact with context.py directly

All canonical token budgets and task type names in [MASTER_REFERENCE.md](./MASTER_REFERENCE.md).
