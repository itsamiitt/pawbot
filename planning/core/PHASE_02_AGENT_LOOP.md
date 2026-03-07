# PHASE 2 — AGENT LOOP INTELLIGENCE
> **Cross-reference:** [MASTER_REFERENCE.md](./MASTER_REFERENCE.md)  
> **Implementation Days:** Day 8 (2.1), Day 9 (2.2), Day 10 (2.3), Day 26 (2.4), Weeks 5–8 (2.5)  
> **Primary File:** `~/nanobot/agent/loop.py`  
> **Test File:** `~/nanobot/tests/test_agent_loop.py`  
> **Depends on:** Phase 1 (memory.py — MemoryRouter), Phase 4 (router.py — ModelRouter)

---

## BEFORE YOU START — READ THESE FILES

```bash
cat ~/nanobot/agent/loop.py        # find existing process() method signature
cat ~/nanobot/agent/memory.py      # understand MemoryRouter interface (Phase 1)
cat ~/nanobot/session/             # understand how session state is managed
```

**Existing interface to preserve:** The `AgentLoop` class and its main entry point method (likely `process()` or `run()`). **Do not rename or change the signature of that method.** The CLI in `cli/` calls it directly.

---

## FEATURE 2.1 — DUAL SYSTEM ROUTER

**New class:** `ComplexityClassifier`  
**Location:** `agent/loop.py` (add before `AgentLoop` class)  
**Activation point:** Very first line inside `AgentLoop.process()`, before any context loading or LLM calls

### Complexity Score Signals

```python
class ComplexityClassifier:
    """
    Scores an incoming message from 0.0 (trivial) to 1.0 (maximum complexity).
    Score determines which execution path the agent takes.
    """

    KEYWORD_SIGNALS = {
        "refactor", "deploy", "debug", "architect", "design",
        "implement", "migrate", "integrate", "analyze", "optimize"
    }

    URGENCY_SIGNALS = {"urgent", "asap", "broken", "down"}

    def score(self, message: str) -> float:
        score = 0.0
        words = message.lower().split()
        word_set = set(words)

        # Signal: long message
        if len(words) > 100:
            score += 0.2

        # Signal: contains complexity keywords
        if word_set & self.KEYWORD_SIGNALS:
            score += 0.2

        # Signal: references multiple files/components (look for .py, .js, / patterns)
        import re
        file_refs = re.findall(r'\b\w+\.\w{2,4}\b|/\w+', message)
        if len(file_refs) >= 2:
            score += 0.15

        # Signal: deep "why" or "how does" questions
        if any(message.lower().startswith(p) for p in ["why ", "how does "]):
            score += 0.1

        # Signal: references past failure
        failure_words = {"error", "failed", "broke", "crash", "exception", "traceback"}
        if word_set & failure_words:
            score += 0.15

        # Signal: spans multiple topics (crude check: sentence count > 3 with varied subjects)
        sentences = re.split(r'[.!?]', message)
        if len(sentences) > 3:
            score += 0.1

        # Signal: urgency
        if word_set & self.URGENCY_SIGNALS:
            score += 0.1

        return min(1.0, round(score, 2))
```

### System Path Definitions

```python
# These constants are referenced in loop.py, context.py, and router.py
# DO NOT change these values — they are in MASTER_REFERENCE.md

SYSTEM_1_MAX   = 0.3   # fast path
SYSTEM_1_5_MAX = 0.7   # ReAct path
SYSTEM_2_MIN   = 0.7   # deliberative path

SYSTEM_PATHS = {
    "system_1": {
        "max_iterations": 5,
        "context_mode": "minimal",      # used by context.py TaskTypeDetector
        "model_hint": "cheap",          # passed to ModelRouter
    },
    "system_1_5": {
        "max_iterations": 15,
        "context_mode": "standard",
        "model_hint": "balanced",
    },
    "system_2": {
        "max_iterations": 50,
        "context_mode": "full",
        "model_hint": "best",
        "use_tree_of_thoughts": True,   # triggers ThoughtTreePlanner (Feature 2.4)
        "pre_task_reflection": True,    # triggers Feature 2.2
    },
}

def get_system_path(complexity_score: float) -> str:
    if complexity_score <= SYSTEM_1_MAX:
        return "system_1"
    elif complexity_score <= SYSTEM_1_5_MAX:
        return "system_1_5"
    else:
        return "system_2"
```

### Integration into AgentLoop.process()

At the very start of `process()`:

```python
def process(self, message: str, session_id: str, ...):
    # ── STEP 1: Classify complexity ──────────────────────────────────
    classifier = ComplexityClassifier()
    complexity_score = classifier.score(message)
    system_path = get_system_path(complexity_score)
    path_config = SYSTEM_PATHS[system_path]

    # Store in session state so context.py can read it
    self.session.set("complexity_score", complexity_score)
    self.session.set("system_path", system_path)
    self.session.set("context_mode", path_config["context_mode"])

    logger.info(f"Complexity: {complexity_score:.2f} → {system_path}")

    # ── STEP 2: Pre-task reflection (System 2 only) ───────────────────
    if path_config.get("pre_task_reflection") and system_path == "system_2":
        self._run_pre_task_reflection(message)

    # ── STEP 3: Plan with Tree of Thoughts (System 2, code/arch only) ─
    if path_config.get("use_tree_of_thoughts"):
        task_type = self.session.get("task_type", "general")
        if task_type in ["coding_task", "architecture"]:
            planner = ThoughtTreePlanner(self.model_router, self.memory)
            selected_approach = planner.plan(message)
            self.session.set("selected_approach", selected_approach)

    # ── STEP 4: Continue with normal loop ────────────────────────────
    max_iter = path_config["max_iterations"]
    # ... rest of existing process() logic ...
```

---

## FEATURE 2.2 — PRE-TASK REFLECTION CHECK

**Method name:** `_run_pre_task_reflection`  
**Location:** `agent/loop.py` (private method on `AgentLoop`)  
**Depends on:** `MemoryRouter.search()` from Phase 1  
**Only runs when:** `system_path == "system_2"`

```python
def _run_pre_task_reflection(self, task_description: str):
    """
    Loads relevant past lessons and procedures into session context.
    Called before the main loop for System 2 tasks.
    """
    # Query reflections
    reflections = self.memory.search(
        query=task_description,
        limit=5
    )
    # Filter to type="reflection" and confidence > 0.7
    relevant_reflections = [
        r for r in reflections
        if r.get("type") == "reflection"
        and r.get("confidence", 0) > 0.7
    ][:3]  # top 3 only

    # Query procedures
    procedures = self.memory.search(
        query=task_description,
        limit=5
    )
    relevant_procedures = [
        p for p in procedures
        if p.get("type") == "procedure"
        and p.get("success_count", 0) > 2
    ][:1]  # top 1 only

    # Store in session for context.py to inject
    if relevant_reflections:
        self.session.set("pre_task_reflections", relevant_reflections)
        logger.info(f"Pre-task check: {len(relevant_reflections)} reflections loaded")

    if relevant_procedures:
        self.session.set("pre_task_procedure", relevant_procedures[0])
        logger.info(f"Pre-task check: procedure '{relevant_procedures[0].get('name')}' loaded")
```

**How context.py uses this** (Phase 3 reads from session):

```python
# In context.py ContextBuilder.build():
reflections = session.get("pre_task_reflections", [])
if reflections:
    context_sections["reflections"] = _format_reflections(reflections)
    # Format: "Rule: {rule} — Context: {lesson}"
```

---

## FEATURE 2.3 — POST-TASK LEARNING

**Method name:** `_run_post_task_learning`  
**Location:** `agent/loop.py`  
**Runs:** Asynchronously after loop completes (zero latency for user)

```python
import threading

def _run_post_task_learning(self, task: str, success: bool,
                             execution_trace: list[dict],
                             failure_reason: str = None):
    """
    Fires in a background thread after agent responds.
    Never blocks the user-facing response.
    """
    thread = threading.Thread(
        target=self.__post_task_learning_sync,
        args=(task, success, execution_trace, failure_reason),
        daemon=True
    )
    thread.start()

def __post_task_learning_sync(self, task: str, success: bool,
                               execution_trace: list[dict],
                               failure_reason: str):
    try:
        if success:
            system_path = self.session.get("system_path", "system_1")
            if system_path == "system_2":
                # Check if this task type has an existing procedure
                existing_procs = self.memory.search(query=task, limit=3)
                existing_proc = next(
                    (p for p in existing_procs
                     if p.get("type") == "procedure"
                     and p.get("similarity", 0) > 0.8),
                    None
                )

                if existing_proc:
                    # Increment success count
                    self.memory.update(existing_proc["id"], {
                        **existing_proc,
                        "success_count": existing_proc.get("success_count", 0) + 1,
                        "last_used": int(time.time()),
                    })
                else:
                    # Extract and save new procedure
                    steps = self._extract_steps_from_trace(execution_trace)
                    if steps:
                        self.memory.save("procedure", {
                            "name": task[:80],  # truncate for name
                            "triggers": [task],
                            "steps": steps,
                            "preconditions": [],
                            "success_count": 1,
                            "last_used": int(time.time()),
                        })

            # Save episode summary
            self.memory.save("episode", {
                "text": f"Completed: {task}. Steps: {len(execution_trace)}",
                "goal": task,
                "success": True,
                "timestamp": int(time.time()),
                "session_id": self.session.session_id,
            })

        else:
            # Generate and save reflection
            reflection = self._generate_reflection(execution_trace, failure_reason)
            if reflection:
                self.memory.save("reflection", reflection)
                logger.info(f"Reflection saved: {reflection.get('rule', 'unknown')}")

            # Save episode summary
            self.memory.save("episode", {
                "text": f"Failed: {task}. Reason: {failure_reason}",
                "goal": task,
                "success": False,
                "timestamp": int(time.time()),
                "session_id": self.session.session_id,
            })

    except Exception as e:
        logger.warning(f"Post-task learning failed: {e}")

def _extract_steps_from_trace(self, trace: list[dict]) -> list[str]:
    """Summarize execution trace into a list of step strings."""
    return [
        f"{i+1}. {step.get('action', 'unknown')} → {step.get('result', 'done')[:80]}"
        for i, step in enumerate(trace)
        if step.get("action")
    ]

def _generate_reflection(self, trace: list[dict], failure_reason: str) -> Optional[dict]:
    """
    Uses local Ollama model to analyze failure and extract lesson.
    NOT Claude — cost optimization.
    Route: ModelRouter with task_type="result_compress", complexity=any → ollama/llama3.1:8b
    """
    # Format trace as text
    trace_text = "\n".join([
        f"Step {i+1}: {s.get('action')} → {s.get('result', '')[:100]}"
        for i, s in enumerate(trace)
    ])

    prompt = f"""Analyze this task failure. Be concise.
Failure reason: {failure_reason}
Execution trace:
{trace_text}

Respond in JSON:
{{
  "failure_type": "assumption_error|missing_check|wrong_tool|timeout|other",
  "lesson": "one sentence describing what went wrong",
  "rule": "one actionable rule for the future (start with a verb)",
  "applies_to": ["task_type_1", "task_type_2"],
  "confidence": 0.8
}}"""

    try:
        response = self.model_router.call(
            task_type="result_compress",
            complexity=0.0,
            prompt=prompt
        )
        return json.loads(response)
    except Exception as e:
        logger.warning(f"Reflection generation failed: {e}")
        return None
```

**Call site** — at the end of `process()`:

```python
# After agent responds to user, before method returns:
self._run_post_task_learning(
    task=message,
    success=loop_success,
    execution_trace=self.session.get("execution_trace", []),
    failure_reason=self.session.get("last_error")
)
```

---

## FEATURE 2.4 — TREE OF THOUGHTS PLANNER

**New class:** `ThoughtTreePlanner`  
**Location:** `agent/loop.py`  
**Only activates:** `complexity_score > 0.7` AND `task_type in ["coding_task", "architecture"]`

```python
class ThoughtTreePlanner:
    def __init__(self, model_router, memory):
        self.model_router = model_router
        self.memory = memory

    def plan(self, task: str) -> dict:
        """
        Generate 3 candidate approaches, evaluate each, return best.
        Falls back to second-best if first fails during execution.
        """
        approaches = self._generate_approaches(task)
        scored = [self._score_approach(a, task) for a in approaches]
        scored.sort(key=lambda x: x["score"], reverse=True)

        # Log rejected approaches to reasoning log
        logger.info(f"ToT: selected '{scored[0]['name']}' "
                    f"(rejected: {[a['name'] for a in scored[1:]]})")

        return {
            "primary": scored[0],
            "fallback": scored[1] if len(scored) > 1 else None,
            "all": scored,
        }

    def _generate_approaches(self, task: str) -> list[dict]:
        prompt = f"""Given this task, propose 3 different technical approaches.
Task: {task}

For each approach respond in JSON array:
[
  {{
    "name": "Approach name",
    "core_idea": "One sentence description",
    "trade_offs": "Pros and cons",
    "estimated_complexity": "low|medium|high",
    "risk_level": "low|medium|high"
  }}
]
Respond with ONLY the JSON array."""

        response = self.model_router.call(
            task_type="architecture",
            complexity=0.8,
            prompt=prompt
        )
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            return [{"name": "default", "core_idea": task, "trade_offs": "", 
                     "estimated_complexity": "medium", "risk_level": "medium"}]

    def _score_approach(self, approach: dict, task: str) -> dict:
        score = 0.5  # baseline

        # Penalty for high risk irreversible approaches
        if approach.get("risk_level") == "high":
            score -= 0.2

        # Bonus for low complexity when task is not architecture
        if approach.get("estimated_complexity") == "low":
            score += 0.1

        # Check consistency with past decisions in memory
        past_decisions = self.memory.search(
            query=f"{task} {approach['name']}",
            limit=3
        )
        relevant_decisions = [d for d in past_decisions if d.get("type") == "decision"]
        if relevant_decisions:
            score += 0.15  # past precedent supports this approach

        approach["score"] = round(score, 2)
        return approach
```

**Fallback handling during execution** — add to the main loop iteration:

```python
# In the main loop body:
selected_approach = self.session.get("selected_approach")
if selected_approach and self.failure_count >= 5:
    fallback = selected_approach.get("fallback")
    if fallback and not self.session.get("using_fallback_approach"):
        logger.info(f"ToT: switching to fallback approach '{fallback['name']}'")
        self.session.set("using_fallback_approach", True)
        self.session.set("active_approach", fallback)
        # Reset failure count for the new approach
        self.failure_count = 0
```

---

## FEATURE 2.5 — SELF-CORRECTION PROTOCOL

**Location:** `agent/loop.py` — integrated into the main loop iteration  
**Depends on:** Feature 2.3 (reflection memory), Feature 2.4 (ToT fallback)

### Failure Tracking

```python
# Add to AgentLoop.__init__ or reset at start of each process() call:
self.failure_count = 0
self.failure_log = []  # list of {"step": N, "error": "...", "action": "..."}
```

A failure is defined as any of:
- Tool call returned an error response
- `code_run_checks()` (Phase 7) returned failures
- LLM produced malformed output (JSON parse error, missing required fields)
- Expected element not found (browser tool, screen find, etc.)

```python
def _record_failure(self, error: str, action: str):
    self.failure_count += 1
    self.failure_log.append({
        "step": self.current_step,
        "error": error[:200],
        "action": action,
        "timestamp": int(time.time()),
    })
    self.session.set("execution_trace_errors", self.failure_log)
    self._apply_correction_level()

def _apply_correction_level(self):
    """Apply escalating correction strategy based on failure count."""
    fc = self.failure_count

    if fc <= 2:
        # Level 1: Retry with slight variation
        logger.info(f"Correction L1: retrying (attempt {fc})")
        self.session.set("retry_hint", f"Attempt {fc}: try a slightly different approach")

    elif fc <= 4:
        # Level 2: Replan the current subgoal
        logger.info(f"Correction L2: replanning subgoal")
        # Load relevant reflections for this failure type
        reflections = self.memory.search(
            query=" ".join([f["error"] for f in self.failure_log[-2:]]),
            limit=3
        )
        self.session.set("correction_reflections",
                         [r for r in reflections if r.get("type") == "reflection"])
        self.session.set("replan_signal", True)

    elif fc <= 6:
        # Level 3: Switch strategy / activate ToT fallback
        logger.info(f"Correction L3: changing strategy")
        selected = self.session.get("selected_approach")
        if selected and selected.get("fallback"):
            self.session.set("active_approach", selected["fallback"])
            self.session.set("using_fallback_approach", True)
            self.failure_count = 0  # reset for new approach
        else:
            self.session.set("strategy_change_signal", True)

    else:
        # Level 4: Escalate to user — pause and wait
        logger.warning(f"Correction L4: escalating to user after {fc} failures")
        self._escalate_to_user()

def _escalate_to_user(self):
    """
    Sends structured escalation message to user.
    Pauses loop execution.
    """
    task = self.session.get("current_task", "current task")
    errors_summary = "; ".join(
        set(f["error"][:80] for f in self.failure_log[-3:])
    )

    options = [
        "A) Retry with more information — describe any constraints I should know",
        "B) Simplify the task — tell me a smaller first step to try",
        "C) Cancel this task and try a different approach",
    ]

    message = (
        f"I've tried {self.failure_count} approaches on '{task}' and "
        f"encountered: {errors_summary}.\n\n"
        f"I need your guidance. Options:\n" +
        "\n".join(options)
    )

    # Send via MessageBus (channel-agnostic)
    self.bus.send(message)
    self.session.set("paused_for_escalation", True)
    # Loop exits — will resume when user responds
```

---

## TEST REQUIREMENTS

**File:** `~/nanobot/tests/test_agent_loop.py`

```python
class TestComplexityClassifier:
    def test_short_message_scores_low()
    def test_complex_keywords_raise_score()
    def test_urgency_adds_score()
    def test_score_capped_at_1_0()
    def test_system_path_selection()

class TestPreTaskReflection:
    def test_loads_reflections_for_system_2()
    def test_skips_low_confidence_reflections()
    def test_no_op_for_system_1()

class TestPostTaskLearning:
    def test_saves_episode_on_success()
    def test_saves_procedure_for_novel_system2_task()
    def test_increments_existing_procedure_count()
    def test_saves_reflection_on_failure()
    def test_runs_async_no_blocking()

class TestThoughtTreePlanner:
    def test_generates_three_approaches()
    def test_selects_highest_score()
    def test_provides_fallback()
    def test_logs_rejected_approaches()

class TestSelfCorrection:
    def test_level_1_at_failure_1_2()
    def test_level_2_at_failure_3_4_loads_reflections()
    def test_level_3_switches_to_fallback_approach()
    def test_level_4_escalates_to_user_and_pauses()
    def test_never_loops_forever()
```

---

## CROSS-REFERENCES

- **Phase 1** provides: `MemoryRouter.search()`, `MemoryRouter.save()`, `MemoryRouter.update()`
- **Phase 3** reads from session: `complexity_score`, `system_path`, `context_mode`, `pre_task_reflections`, `pre_task_procedure`
- **Phase 4** is called by: `ThoughtTreePlanner._generate_approaches()` (model_router.call), `_generate_reflection()` (model_router.call)
- **Phase 7** triggers `_record_failure()` when `code_run_checks()` fails
- **Phase 8** triggers `_record_failure()` when browser tools fail
- **Phase 11** (CronScheduler) is NOT used by this phase

All canonical class names and constants in [MASTER_REFERENCE.md](./MASTER_REFERENCE.md).
