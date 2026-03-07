"""Agent loop: the core processing engine."""

from __future__ import annotations

# ── Section 1 imports (Phase 1 + Phase 2) ────────────────────────────────────
from pawbot.agent.compactor import compactor
from pawbot.agent.lane_queue import lane_queue  # noqa: F401 — used by session manager

import asyncio
import json
import re
import threading
import time
import weakref
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from loguru import logger

from pawbot.agent.context import ContextBuilder
from pawbot.agent.memory import MemoryStore
from pawbot.agent.output_sanitizer import redact_secrets, scan_output
from pawbot.agent.subagent import SubagentManager
from pawbot.observability.metrics import metrics
from pawbot.agent.tools.cron import CronTool
from pawbot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from pawbot.agent.tools.message import MessageTool
from pawbot.agent.tools.registry import ToolRegistry
from pawbot.agent.tools.shell import ExecTool
from pawbot.agent.tools.spawn import SpawnTool
from pawbot.agent.tools.web import WebFetchTool, WebSearchTool
from pawbot.bus.events import InboundMessage, OutboundMessage
from pawbot.bus.queue import MessageBus
from pawbot.providers.base import LLMProvider
from pawbot.session.manager import Session, SessionManager

if TYPE_CHECKING:
    from pawbot.config.schema import ChannelsConfig, ExecToolConfig
    from pawbot.cron.service import CronService


# -- Extracted modules (Phase R4) --
from pawbot.agent.classifier import (  # noqa: E402
    SYSTEM_1_MAX,
    SYSTEM_1_5_MAX,
    SYSTEM_2_MIN,
    SYSTEM_PATHS,
    ComplexityClassifier,
    _FLEET_TRIGGER_KEYWORDS,
    get_system_path,
)
from pawbot.agent.planner import ThoughtTreePlanner  # noqa: E402


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Classifies message complexity (System 1/1.5/2 routing)
    3. Runs pre-task reflection for complex tasks
    4. Builds context with history, memory, skills
    5. Calls the LLM
    6. Executes tool calls with self-correction protocol
    7. Sends responses back
    8. Runs post-task learning asynchronously
    """

    _TOOL_RESULT_MAX_CHARS = 500

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 40,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        memory_window: int = 100,
        reasoning_effort: str | None = None,
        brave_api_key: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        memory_db_path: str | None = None,
        memory_session_id: str | None = None,
    ):
        from pawbot.config.schema import ExecToolConfig
        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.memory_window = memory_window
        self.reasoning_effort = reasoning_effort
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self.memory_db_path = memory_db_path
        self.memory_session_id = memory_session_id or f"workspace:{self.workspace}"
        self.memory_config = (
            {"memory": {"backends": {"sqlite": {"path": memory_db_path}}}}
            if memory_db_path
            else {}
        )

        self.context = ContextBuilder(workspace)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            reasoning_effort=reasoning_effort,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._consolidating: set[str] = set()  # Session keys with consolidation in progress
        self._consolidation_tasks: set[asyncio.Task] = set()  # Strong refs to in-flight tasks
        self._consolidation_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._processing_lock = asyncio.Lock()

        # ── Phase 1: Resilient LLM caller (retry + timeout) ──────────────────
        from pawbot.providers.resilience import ResilientLLMCaller
        self._llm_caller = ResilientLLMCaller(
            max_retries=3,
            timeout_seconds=120.0,
            base_delay=1.0,
        )

        # â”€â”€ Phase 2: Complexity classifier â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        self._classifier = ComplexityClassifier()

        # â”€â”€ Phase 2.5: Self-correction state (reset per-message) â”€â”€â”€â”€â”€â”€â”€
        self.failure_count = 0
        self.failure_log: list[dict] = []
        self.current_step = 0

        # â”€â”€ Phase 2: Memory router for Phase 2.2/2.3 â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        self._memory_router = None  # Lazily initialized

        # ── Phase 8: Browser engine (lazy init) ──────────────────────────────
        self._browser_engine = None  # Set up in _register_default_tools if enabled

        self._register_default_tools()

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
            path_append=self.exec_config.path_append,
        ))
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tools.register(WebFetchTool())
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(SpawnTool(manager=self.subagents))
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))

        # Phase 8: Browser tools (only when sandbox.browser.enabled = true)
        self._try_register_browser_tools()

    def _try_register_browser_tools(self) -> None:
        """Register browser tools if enabled in config."""
        try:
            from pawbot.config.loader import load_config
            config = load_config()
            browser_cfg = config.sandbox.browser
        except Exception:
            return  # Config not available or sandbox not configured

        if not browser_cfg.enabled:
            return

        from pawbot.agent.tools.browser_tool import BROWSER_TOOLS, get_browser_engine, set_browser_engine
        from pawbot.browser.engine import BrowserEngine

        # Create engine with config
        engine = BrowserEngine(
            headless=browser_cfg.headless,
            blocked_domains=browser_cfg.blocked_domains,
            allowed_domains=browser_cfg.allowed_domains,
            max_pages=browser_cfg.max_pages,
            page_timeout_ms=browser_cfg.page_timeout_ms,
            persist_state=browser_cfg.persist_state,
            js_execution=browser_cfg.js_execution,
        )
        set_browser_engine(engine)
        self._browser_engine = engine

        # Register all 5 browser tools
        for tool_cls in BROWSER_TOOLS:
            self.tools.register(tool_cls())

        logger.info("Browser tools enabled ({} tools registered)", len(BROWSER_TOOLS))

    def _get_filtered_tools(self) -> list[dict]:
        """Get tool definitions filtered by agent allow/deny lists (Phase 9.4)."""
        all_tools = self.tools.get_definitions()

        try:
            from pawbot.config.loader import load_config
            config = load_config()
            tools_cfg = config.agents.tools
        except Exception:
            return all_tools

        if not tools_cfg.allow and not tools_cfg.deny:
            return all_tools

        import fnmatch
        filtered = []
        for tool in all_tools:
            name = tool.get("function", {}).get("name", "")
            # Deny list takes priority
            if any(fnmatch.fnmatch(name, p) for p in tools_cfg.deny):
                continue
            # Allow list (empty = allow all)
            if tools_cfg.allow:
                if not any(fnmatch.fnmatch(name, p) for p in tools_cfg.allow):
                    continue
            filtered.append(tool)
        return filtered

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from pawbot.agent.tools.mcp import connect_mcp_servers
        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
            self._mcp_connected = True
        except Exception as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception as e:  # noqa: F841
                    pass
                self._mcp_stack = None
        finally:
            self._mcp_connecting = False

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for all tools that need routing info."""
        for name in ("message", "spawn", "cron"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    tool.set_context(channel, chat_id, *([message_id] if name == "message" else []))

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>â€¦</think> blocks that some models embed in content."""
        if not text:
            return None
        return re.sub(r"<think>[\s\S]*?</think>", "", text).strip() or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hint, e.g. 'web_search("query")'."""
        def _fmt(tc):
            args = (tc.arguments[0] if isinstance(tc.arguments, list) else tc.arguments) or {}
            val = next(iter(args.values()), None) if isinstance(args, dict) else None
            if not isinstance(val, str):
                return tc.name
            return f'{tc.name}("{val[:40]}â€¦")' if len(val) > 40 else f'{tc.name}("{val}")'
        return ", ".join(_fmt(tc) for tc in tool_calls)

    async def _process_tool_calls(
        self,
        response,
        messages: list[dict],
        tools_used: list[str],
        execution_trace: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> list[dict]:
        """Execute tool calls from one model response and append outputs to messages."""
        if on_progress:
            clean = self._strip_think(response.content)
            if clean:
                await on_progress(clean)
            await on_progress(self._tool_hint(response.tool_calls), tool_hint=True)

        tool_call_dicts = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                },
            }
            for tc in response.tool_calls
        ]
        messages = self.context.add_assistant_message(
            messages,
            response.content,
            tool_call_dicts,
            reasoning_content=response.reasoning_content,
            thinking_blocks=response.thinking_blocks,
        )

        for tool_call in response.tool_calls:
            tools_used.append(tool_call.name)
            args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
            logger.info("Tool call: {}({})", tool_call.name, args_str[:200])
            metrics.tool_calls.inc()
            _tool_start = time.monotonic()
            result = await self.tools.execute(tool_call.name, tool_call.arguments)
            metrics.tool_latency.observe((time.monotonic() - _tool_start) * 1000)
            messages = self.context.add_tool_result(
                messages,
                tool_call.id,
                tool_call.name,
                result,
            )

            trace_entry = {
                "step": self.current_step,
                "action": f"{tool_call.name}({args_str[:100]})",
                "result": str(result)[:200] if result else "done",
                "timestamp": int(time.time()),
            }
            execution_trace.append(trace_entry)

            if result and isinstance(result, str) and (
                result.startswith("Error:")
                or result.startswith("error:")
                or "traceback" in result.lower()[:200]
            ):
                metrics.tool_errors.inc()
                self._record_failure(result[:200], tool_call.name)

            selected_approach = self._session_meta.get("selected_approach")
            if selected_approach and self.failure_count >= 5:
                fallback = selected_approach.get("fallback")
                if fallback and not self._session_meta.get("using_fallback_approach"):
                    logger.info("ToT: switching to fallback approach '{}'", fallback.get("name"))
                    self._session_meta["using_fallback_approach"] = True
                    self._session_meta["active_approach"] = fallback
                    self.failure_count = 0

        return messages

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> tuple[str | None, list[str], list[dict], list[dict]]:
        """Run the agent iteration loop.

        Returns:
            (final_content, tools_used, messages, execution_trace)
            execution_trace is a list of step dicts for post-task learning.
        """
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        execution_trace: list[dict] = []

        while iteration < self.max_iterations:
            iteration += 1
            self.current_step = iteration

            # ← Phase 2: compact context before every LLM call
            messages = await compactor.compact_if_needed(messages, self.model)

            # ← Phase 1: context overflow protection
            from pawbot.providers.context_limits import check_context_overflow
            is_overflow, est_tokens, ctx_limit = check_context_overflow(messages, self.model)
            if is_overflow:
                logger.warning(
                    "Context overflow detected: ~{} tokens > {} limit. Forcing compaction.",
                    est_tokens, ctx_limit,
                )
                messages = await compactor.force_compact(
                    messages, target_tokens=int(ctx_limit * 0.7)
                )

            # ← Phase 1: resilient LLM call with retry + timeout
            # ← Phase 7: metrics instrumentation
            metrics.llm_calls.inc()
            _llm_start = time.monotonic()
            try:
                response = await self._llm_caller.call(
                    self.provider.chat,
                    messages=messages,
                    tools=self._get_filtered_tools(),
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    reasoning_effort=self.reasoning_effort,
                )
            except Exception:
                metrics.llm_errors.inc()
                raise
            finally:
                metrics.llm_latency.observe((time.monotonic() - _llm_start) * 1000)

            # Track token usage if available
            if response.usage:
                metrics.llm_tokens_in.inc(response.usage.get("prompt_tokens", 0))
                metrics.llm_tokens_out.inc(response.usage.get("completion_tokens", 0))

            if response.has_tool_calls:
                messages = await self._process_tool_calls(
                    response=response,
                    messages=messages,
                    tools_used=tools_used,
                    execution_trace=execution_trace,
                    on_progress=on_progress,
                )

            else:
                clean = self._strip_think(response.content)
                # Don't persist error responses to session history â€” they can
                # poison the context and cause permanent 400 loops (#1303).
                if response.finish_reason == "error":
                    logger.error("LLM returned error: {}", (clean or "")[:200])
                    final_content = clean or "Sorry, I encountered an error calling the AI model."
                    self._record_failure(final_content[:200], "llm_call")
                    break
                messages = self.context.add_assistant_message(
                    messages, clean, reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
                final_content = clean
                break

        if final_content is None and iteration >= self.max_iterations:
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            final_content = (
                f"I reached the maximum number of tool call iterations ({self.max_iterations}) "
                "without completing the task. You can try breaking the task into smaller steps."
            )

        return final_content, tools_used, messages, execution_trace

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        # ── Phase 1: Register graceful shutdown handler ───────────────────
        import signal
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self._graceful_shutdown()))
            except NotImplementedError:
                # Windows doesn't support add_signal_handler for SIGTERM
                pass

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            if msg.content.strip().lower() == "/stop":
                await self._handle_stop(msg)
            else:
                task = asyncio.create_task(self._dispatch(msg))
                self._active_tasks.setdefault(msg.session_key, []).append(task)
                task.add_done_callback(
                    lambda t, k=msg.session_key: (
                        self._active_tasks.get(k, []).remove(t)
                        if t in self._active_tasks.get(k, [])
                        else None
                    )
                )

    async def _graceful_shutdown(self) -> None:
        """Graceful shutdown: cancel active tasks, flush state, close MCP."""
        logger.info("Graceful shutdown initiated...")
        self._running = False

        # 1. Cancel all active tasks
        all_tasks = []
        for session_tasks in self._active_tasks.values():
            all_tasks.extend(session_tasks)

        for task in all_tasks:
            if not task.done():
                task.cancel()

        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)
            logger.info("Cancelled {} active tasks", len(all_tasks))

        # 2. Cancel subagents
        try:
            cancelled = await self.subagents.cancel_all()
            if cancelled:
                logger.info("Cancelled {} subagents", cancelled)
        except Exception:
            logger.exception("Error cancelling subagents during shutdown")

        # 3. Save all sessions
        try:
            self.sessions.save_all()
            logger.info("Sessions saved")
        except Exception:
            logger.exception("Error saving sessions during shutdown")

        # 4. Close MCP connections
        await self.close_mcp()

        # 5. Stop browser engine (Phase 8)
        if self._browser_engine:
            try:
                await self._browser_engine.stop()
                logger.info("Browser engine stopped")
            except Exception:
                logger.exception("Error stopping browser during shutdown")

        logger.info("Graceful shutdown complete")

    async def _handle_stop(self, msg: InboundMessage) -> None:
        """Cancel all active tasks and subagents for the session."""
        tasks = self._active_tasks.pop(msg.session_key, [])
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        sub_cancelled = await self.subagents.cancel_by_session(msg.session_key)
        total = cancelled + sub_cancelled
        content = f"â¹ Stopped {total} task(s)." if total else "No active task to stop."
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message under the global lock."""
        async with self._processing_lock:
            try:
                response = await self._process_message(msg)
                if response is not None:
                    await self.bus.publish_outbound(response)
                elif msg.channel == "cli":
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="", metadata=msg.metadata or {},
                    ))
            except asyncio.CancelledError:
                logger.info("Task cancelled for session {}", msg.session_key)
                raise
            except Exception as e:  # noqa: F841
                logger.exception("Error processing message for session {}", msg.session_key)
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="Sorry, I encountered an error.",
                ))

    async def close_mcp(self) -> None:
        """Close MCP connections."""
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    def _setup_session(
        self,
        msg: InboundMessage,
        session_key: str | None,
    ) -> tuple[str, Session]:
        """Resolve session key and return the active session object."""
        key = session_key or msg.session_key
        return key, self.sessions.get_or_create(key)

    async def _handle_system_message(self, msg: InboundMessage) -> OutboundMessage:
        """Handle internal system channel messages."""
        channel, chat_id = (
            msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id)
        )
        logger.info("Processing system message from {}", msg.sender_id)
        key = f"{channel}:{chat_id}"
        session = self.sessions.get_or_create(key)
        self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))
        history = session.get_history(max_messages=self.memory_window)
        messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            channel=channel,
            chat_id=chat_id,
        )
        final_content, _, all_msgs, _ = await self._run_agent_loop(messages)
        self._save_turn(session, all_msgs, 1 + len(history))
        self.sessions.save(session)
        return OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=final_content or "Background task completed.",
        )

    @staticmethod
    def _help_text() -> str:
        return (
            "pawbot commands:\n"
            "/new - Start a new conversation\n"
            "/stop - Stop the current task\n"
            "/help - Show available commands"
        )

    async def _handle_slash_command(
        self,
        msg: InboundMessage,
        session: Session,
    ) -> OutboundMessage | None:
        """Handle slash commands. Return None when message is not a command."""
        cmd = msg.content.strip().lower()
        if cmd == "/new":
            lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())
            self._consolidating.add(session.key)
            try:
                async with lock:
                    snapshot = session.messages[session.last_consolidated:]
                    if snapshot:
                        temp = Session(key=session.key)
                        temp.messages = list(snapshot)
                        if not await self._consolidate_memory(temp, archive_all=True):
                            return OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content="Memory archival failed, session not cleared. Please try again.",
                            )
            except Exception:
                logger.exception("/new archival failed for {}", session.key)
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="Memory archival failed, session not cleared. Please try again.",
                )
            finally:
                self._consolidating.discard(session.key)

            session.clear()
            self.sessions.save(session)
            self.sessions.invalidate(session.key)
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="New session started.",
            )
        if cmd == "/help":
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=self._help_text(),
            )
        return None

    def _build_response(
        self,
        msg: InboundMessage,
        final_content: str,
        all_msgs: list[dict],
        session: Session,
        history_len: int,
    ) -> OutboundMessage:
        """Persist turn state and build the outbound response."""
        leaks = scan_output(final_content or "")
        if leaks:
            logger.warning("Detected {} secret(s) in output, redacting", len(leaks))
            final_content = redact_secrets(final_content or "")
            for entry in reversed(all_msgs):
                if entry.get("role") == "assistant" and entry.get("content"):
                    entry["content"] = final_content
                    break

        self._save_turn(session, all_msgs, 1 + history_len)
        self.sessions.save(session)
        try:
            from pawbot.canvas.server import record_canvas_session

            record_canvas_session(
                session.key,
                final_content,
                metadata={
                    "agent_id": (msg.metadata or {}).get("agent_id", ""),
                    "channel": msg.channel,
                    "chat_id": msg.chat_id,
                },
            )
        except Exception as exc:
            logger.debug("Canvas session record skipped: {}", exc)
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=msg.metadata or {},
        )

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        # ── Phase 7: Track message processing ─────────────────────────────
        metrics.messages_processed.inc()
        # â”€â”€ Phase 2.5: Reset self-correction state for each message â”€â”€â”€â”€â”€
        self.failure_count = 0
        self.failure_log = []
        self.current_step = 0
        self._session_meta = {}  # Per-message metadata for Phase 2

        if msg.channel == "system":
            return await self._handle_system_message(msg)

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        key, session = self._setup_session(msg, session_key)

        slash_result = await self._handle_slash_command(msg, session)
        if slash_result is not None:
            return slash_result

        complexity_score = self._classifier.score(msg.content)
        system_path = get_system_path(complexity_score)
        path_config = SYSTEM_PATHS[system_path]

        self._session_meta["complexity_score"] = complexity_score
        self._session_meta["system_path"] = system_path
        self._session_meta["context_mode"] = path_config["context_mode"]
        session.metadata["complexity_score"] = complexity_score
        session.metadata["system_path"] = system_path
        session.metadata["context_mode"] = path_config["context_mode"]

        logger.info("Complexity: {:.2f} â†’ {}", complexity_score, system_path)

                # Phase 18: Fleet Commander delegation for multi-agent tasks
        if self._is_fleet_worthy(msg.content, complexity_score):
            try:
                from pawbot.fleet.commander import FleetCommander
                from pawbot.fleet.models import FleetConfig
                commander = FleetCommander(
                    config=FleetConfig(),
                    workspace=self.workspace,
                    memory_router=self._get_memory_router(),
                )
                fleet_result = await commander.plan_and_execute(msg.content)
                return self._build_response(
                    msg, fleet_result, [], session, 0,
                )
            except Exception as exc:
                logger.warning("Fleet delegation failed, falling back to agent loop: {}", exc)

        if path_config.get("pre_task_reflection") and system_path == "system_2":
            self._run_pre_task_reflection(msg.content, session)

        skill_names: list[str] = []
        if system_path == "system_2":
            try:
                loaded_skills = self.context.skills.load(msg.content)
                skill_names = [s.name for s in loaded_skills]
                if skill_names:
                    session.metadata["active_skills"] = skill_names
                    self._session_meta["active_skills"] = skill_names
            except Exception as e:
                logger.warning("Skill loading failed, proceeding without skills: {}", e)

        if path_config.get("use_tree_of_thoughts"):
            task_type = session.metadata.get("task_type", "general")
            if task_type in ["coding_task", "architecture"]:
                try:
                    memory = self._get_memory_router()
                    planner = ThoughtTreePlanner(self.provider, self.model, memory)
                    selected_approach = await planner.plan(msg.content)
                    self._session_meta["selected_approach"] = selected_approach
                    session.metadata["selected_approach_name"] = selected_approach["primary"]["name"]
                except Exception as e:
                    logger.warning("ToT planning failed, proceeding without: {}", e)

        saved_max_iterations = self.max_iterations
        self.max_iterations = max(path_config.get("max_iterations", self.max_iterations), self.max_iterations)
        try:
            unconsolidated = len(session.messages) - session.last_consolidated
            if unconsolidated >= self.memory_window and session.key not in self._consolidating:
                self._consolidating.add(session.key)
                lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())

                async def _consolidate_and_unlock():
                    try:
                        async with lock:
                            await self._consolidate_memory(session)
                    finally:
                        self._consolidating.discard(session.key)
                        _task = asyncio.current_task()
                        if _task is not None:
                            self._consolidation_tasks.discard(_task)

                _task = asyncio.create_task(_consolidate_and_unlock())
                self._consolidation_tasks.add(_task)

            self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
            if message_tool := self.tools.get("message"):
                if isinstance(message_tool, MessageTool):
                    message_tool.start_turn()

            history = session.get_history(max_messages=self.memory_window)
            initial_messages = self.context.build_messages(
                history=history,
                current_message=msg.content,
                skill_names=skill_names,
                media=msg.media if msg.media else None,
                channel=msg.channel,
                chat_id=msg.chat_id,
            )

            async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
                meta = dict(msg.metadata or {})
                meta["_progress"] = True
                meta["_tool_hint"] = tool_hint
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=content,
                        metadata=meta,
                    )
                )

            final_content, _, all_msgs, execution_trace = await self._run_agent_loop(
                initial_messages,
                on_progress=on_progress or _bus_progress,
            )
        finally:
            self.max_iterations = saved_max_iterations

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        loop_success = self.failure_count == 0
        last_error = self.failure_log[-1]["error"] if self.failure_log else None

        self._run_post_task_learning(
            task=msg.content,
            success=loop_success,
            execution_trace=execution_trace,
            failure_reason=last_error,
            session_key=key,
        )

        response = self._build_response(msg, final_content, all_msgs, session, len(history))

        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            return None

        preview = response.content[:120] + "..." if len(response.content) > 120 else response.content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        return response

    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Phase 2 â€” Helper Methods
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

        # Phase 18: Fleet-worthiness check
    @staticmethod
    def _is_fleet_worthy(content: str, complexity_score: float) -> bool:
        """Check if a message should be delegated to Fleet Commander.

        A task is fleet-worthy when:
        - Complexity score >= 0.8 (deep system_2 territory), AND
        - The message contains fleet trigger keywords suggesting multi-agent work
        """
        if complexity_score < 0.8:
            return False
        content_lower = content.lower()
        return any(kw in content_lower for kw in _FLEET_TRIGGER_KEYWORDS)

    def _get_memory_router(self):
        """Lazily initialize and return a MemoryRouter instance."""
        if self._memory_router is None:
            try:
                from pawbot.agent.memory import MemoryRouter
                self._memory_router = MemoryRouter(
                    session_id=self.memory_session_id,
                    config=self.memory_config,
                )
            except Exception as e:
                logger.warning("MemoryRouter init failed: {} â€” Phase 2 memory features degraded", e)
        return self._memory_router

    # â”€â”€ Phase 2.2: Pre-Task Reflection Check â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _run_pre_task_reflection(self, task_description: str, session: Session) -> None:
        """
        Loads relevant past lessons and procedures into session context.
        Called before the main loop for System 2 tasks.
        """
        memory = self._get_memory_router()
        if memory is None:
            return

        try:
            # Query reflections
            reflections = memory.search(query=task_description, limit=5)
            relevant_reflections = [
                r for r in reflections
                if r.get("type") == "reflection"
                and r.get("confidence", 0) > 0.7
            ][:3]  # top 3 only

            # Query procedures
            procedures = memory.search(query=task_description, limit=5)
            relevant_procedures = [
                p for p in procedures
                if p.get("type") == "procedure"
                and p.get("success_count", 0) > 2
            ][:1]  # top 1 only

            # Store in session for context.py to inject
            if relevant_reflections:
                session.metadata["pre_task_reflections"] = relevant_reflections
                self._session_meta["pre_task_reflections"] = relevant_reflections
                logger.info("Pre-task check: {} reflections loaded", len(relevant_reflections))

            if relevant_procedures:
                session.metadata["pre_task_procedure"] = relevant_procedures[0]
                self._session_meta["pre_task_procedure"] = relevant_procedures[0]
                logger.info(
                    "Pre-task check: procedure '{}' loaded",
                    relevant_procedures[0].get("name", "unknown"),
                )
        except Exception as e:
            logger.warning("Pre-task reflection failed: {}", e)

    # â”€â”€ Phase 2.3: Post-Task Learning â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _run_post_task_learning(
        self,
        task: str,
        success: bool,
        execution_trace: list[dict],
        failure_reason: Optional[str] = None,
        session_key: str = "",
    ) -> None:
        """
        Fires in a background thread after agent responds.
        Never blocks the user-facing response.
        """
        thread = threading.Thread(
            target=self.__post_task_learning_sync,
            args=(task, success, execution_trace, failure_reason, session_key),
            daemon=True,
        )
        thread.start()

    def __post_task_learning_sync(
        self,
        task: str,
        success: bool,
        execution_trace: list[dict],
        failure_reason: Optional[str],
        session_key: str,
    ) -> None:
        """Synchronous post-task learning worker running in background thread."""
        memory = self._get_memory_router()
        if memory is None:
            return

        try:
            if success:
                system_path = self._session_meta.get("system_path", "system_1")
                if system_path == "system_2":
                    # Check if this task type has an existing procedure
                    existing_procs = memory.search(query=task, limit=3)
                    existing_proc = next(
                        (p for p in existing_procs
                         if p.get("type") == "procedure"
                         and p.get("similarity", 0) > 0.8),
                        None,
                    )

                    if existing_proc:
                        # Increment success count
                        memory.update(existing_proc["id"], {
                            **existing_proc,
                            "success_count": existing_proc.get("success_count", 0) + 1,
                            "last_used": int(time.time()),
                        })
                    else:
                        # Extract and save new procedure
                        steps = self._extract_steps_from_trace(execution_trace)
                        if steps:
                            memory.save("procedure", {
                                "name": task[:80],
                                "triggers": [task],
                                "steps": steps,
                                "preconditions": [],
                                "success_count": 1,
                                "last_used": int(time.time()),
                            })

                # Save episode summary
                memory.save("episode", {
                    "text": f"Completed: {task[:200]}. Steps: {len(execution_trace)}",
                    "goal": task[:200],
                    "success": True,
                    "timestamp": int(time.time()),
                    "session_id": session_key,
                })
            else:
                # Generate and save reflection
                reflection = self._generate_reflection_sync(execution_trace, failure_reason)
                if reflection:
                    memory.save("reflection", reflection)
                    logger.info("Reflection saved: {}", reflection.get("rule", "unknown"))

                # Save episode summary
                memory.save("episode", {
                    "text": f"Failed: {task[:200]}. Reason: {failure_reason}",
                    "goal": task[:200],
                    "success": False,
                    "timestamp": int(time.time()),
                    "session_id": session_key,
                })

        except Exception as e:
            logger.warning("Post-task learning failed: {}", e)

    def _extract_steps_from_trace(self, trace: list[dict]) -> list[str]:
        """Summarize execution trace into a list of step strings."""
        return [
            f"{i+1}. {step.get('action', 'unknown')} â†’ {step.get('result', 'done')[:80]}"
            for i, step in enumerate(trace)
            if step.get("action")
        ]

    def _generate_reflection_sync(
        self,
        trace: list[dict],
        failure_reason: Optional[str],
    ) -> Optional[dict]:
        """
        Generate a structured reflection from a failed task.
        Uses simple heuristics rather than an LLM call from background thread.
        """
        if not failure_reason:
            return None

        # Classify failure type
        failure_lower = failure_reason.lower()
        if "timeout" in failure_lower:
            failure_type = "timeout"
        elif "not found" in failure_lower or "missing" in failure_lower:
            failure_type = "missing_check"
        elif "permission" in failure_lower or "denied" in failure_lower:
            failure_type = "wrong_tool"
        elif "assume" in failure_lower:
            failure_type = "assumption_error"
        else:
            failure_type = "other"

        return {
            "failure_type": failure_type,
            "lesson": f"Task failed: {failure_reason[:200]}",
            "rule": f"Verify prerequisites before attempting: {failure_reason[:100]}",
            "applies_to": ["general"],
            "confidence": 0.7,
            "timestamp": int(time.time()),
        }

    # â”€â”€ Phase 2.5: Self-Correction Protocol â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _record_failure(self, error: str, action: str) -> None:
        """Record a failure event and apply escalating correction."""
        self.failure_count += 1
        self.failure_log.append({
            "step": self.current_step,
            "error": error[:200],
            "action": action,
            "timestamp": int(time.time()),
        })
        self._session_meta["execution_trace_errors"] = self.failure_log
        self._apply_correction_level()

    def _apply_correction_level(self) -> None:
        """Apply escalating correction strategy based on failure count."""
        fc = self.failure_count

        if fc <= 2:
            # Level 1: Retry with slight variation
            logger.info("Correction L1: retrying (attempt {})", fc)
            self._session_meta["retry_hint"] = (
                f"Attempt {fc}: try a slightly different approach"
            )

        elif fc <= 4:
            # Level 2: Replan the current subgoal
            logger.info("Correction L2: replanning subgoal")
            memory = self._get_memory_router()
            if memory is not None:
                try:
                    recent_errors = " ".join(
                        f["error"] for f in self.failure_log[-2:]
                    )
                    reflections = memory.search(query=recent_errors, limit=3)
                    correction_reflections = [
                        r for r in reflections if r.get("type") == "reflection"
                    ]
                    self._session_meta["correction_reflections"] = correction_reflections
                except Exception as e:  # noqa: F841
                    pass
            self._session_meta["replan_signal"] = True

        elif fc <= 6:
            # Level 3: Switch strategy / activate ToT fallback
            logger.info("Correction L3: changing strategy")
            selected = self._session_meta.get("selected_approach")
            if selected and selected.get("fallback"):
                self._session_meta["active_approach"] = selected["fallback"]
                self._session_meta["using_fallback_approach"] = True
                self.failure_count = 0  # reset for new approach
            else:
                self._session_meta["strategy_change_signal"] = True

        else:
            # Level 4: Escalate to user â€” pause and wait
            logger.warning(
                "Correction L4: escalating to user after {} failures", fc
            )
            self._escalate_to_user()

    def _escalate_to_user(self) -> None:
        """
        Sends structured escalation message to user.
        Marks the session as paused for escalation.
        """
        task = self._session_meta.get("current_task", "current task")
        errors_summary = "; ".join(
            set(f["error"][:80] for f in self.failure_log[-3:])
        )

        options = [
            "A) Retry with more information â€” describe any constraints I should know",
            "B) Simplify the task â€” tell me a smaller first step to try",
            "C) Cancel this task and try a different approach",
        ]

        message = (
            f"I've tried {self.failure_count} approaches on '{task}' and "
            f"encountered: {errors_summary}.\n\n"
            f"I need your guidance. Options:\n" +
            "\n".join(options)
        )

        logger.warning("Escalation to user: {}", message[:200])
        self._session_meta["paused_for_escalation"] = True
        self._session_meta["escalation_message"] = message

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime
        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages â€” they poison session context
            if role == "tool" and isinstance(content, str) and len(content) > self._TOOL_RESULT_MAX_CHARS:
                entry["content"] = content[:self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    continue
                if isinstance(content, list):
                    entry["content"] = [
                        {"type": "text", "text": "[image]"} if (
                            c.get("type") == "image_url"
                            and c.get("image_url", {}).get("url", "").startswith("data:image/")
                        ) else c for c in content
                    ]
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()

    async def _consolidate_memory(self, session, archive_all: bool = False) -> bool:
        """Delegate to MemoryStore.consolidate(). Returns True on success."""
        return await MemoryStore(
            self.workspace,
            session_id=self.memory_session_id,
            memory_config=self.memory_config,
        ).consolidate(
            session, self.provider, self.model,
            archive_all=archive_all, memory_window=self.memory_window,
        )

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """Process a message directly (for CLI or cron usage)."""
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        response = await self._process_message(msg, session_key=session_key, on_progress=on_progress)
        return response.content if response else ""

