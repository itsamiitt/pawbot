"""Exec approval system — require explicit approval for dangerous tool executions (Phase 12.4).

Policies:
  - never: Auto-approve everything
  - high_risk: Only require approval for high/critical risk tools
  - auto_safe: Auto-approve low-risk tools, ask for everything else
  - always: Require approval for all tools (unless pre-approved)

Approval workflow:
  1. Agent calls needs_approval(tool_name, risk_level)
  2. If approval needed, agent calls request_approval() → gets request_id
  3. Human approves/denies via API or CLI
  4. Agent polls get_status() or awaits wait_for_approval()
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from pathlib import Path
from typing import Any

from loguru import logger


APPROVALS_FILE = Path.home() / ".pawbot" / "exec-approvals.json"


class ExecApprovalPolicy:
    """Policy constants for exec approval."""
    NEVER = "never"          # Never require approval
    HIGH_RISK = "high_risk"  # Only for high/critical risk tools
    ALWAYS = "always"        # Always require approval
    AUTO_SAFE = "auto_safe"  # Auto-approve safe tools, ask for others


class ExecApprovalManager:
    """Manages approval requests for tool executions.

    Supports per-tool approval with optional "remember" to auto-approve
    future executions of the same tool.
    """

    def __init__(
        self,
        policy: str = ExecApprovalPolicy.HIGH_RISK,
        approvals_file: Path | None = None,
    ):
        self.policy = policy
        self._file = approvals_file or APPROVALS_FILE
        self._pending: dict[str, dict[str, Any]] = {}
        self._approved_patterns: set[str] = set()
        self._load_config()

    def _load_config(self) -> None:
        """Load approval configuration (pre-approved patterns)."""
        if self._file.exists():
            try:
                data = json.loads(self._file.read_text(encoding="utf-8"))
                defaults = data.get("defaults", {})
                for pattern, action in defaults.items():
                    if action == "approve":
                        self._approved_patterns.add(pattern)
            except Exception:
                pass

    # ── Policy Check ──────────────────────────────────────────────────────

    def needs_approval(self, tool_name: str, risk_level: str) -> bool:
        """Check if a tool execution needs human approval.

        Args:
            tool_name: Name of the tool to execute.
            risk_level: Risk level string (low, caution, high, critical).

        Returns:
            True if human approval is required.
        """
        # Pre-approved tools skip the check
        if tool_name in self._approved_patterns:
            return False

        if self.policy == ExecApprovalPolicy.NEVER:
            return False
        if self.policy == ExecApprovalPolicy.ALWAYS:
            return True
        if self.policy == ExecApprovalPolicy.HIGH_RISK:
            return risk_level in ("high", "critical")
        if self.policy == ExecApprovalPolicy.AUTO_SAFE:
            return risk_level not in ("low",)
        return False

    # ── Request Management ────────────────────────────────────────────────

    def request_approval(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        agent_id: str = "main",
    ) -> str:
        """Create an approval request. Returns request_id."""
        request_id = secrets.token_hex(16)
        self._pending[request_id] = {
            "tool_name": tool_name,
            "arguments": arguments or {},
            "agent_id": agent_id,
            "created_at": time.time(),
            "status": "pending",
        }
        logger.info(
            "Approval requested for tool '{}' (request: {})",
            tool_name,
            request_id[:8],
        )
        return request_id

    def approve(self, request_id: str, remember: bool = False) -> bool:
        """Approve a pending request.

        Args:
            request_id: The ID of the pending request.
            remember: If True, auto-approve future executions of this tool.
        """
        if request_id not in self._pending:
            return False
        self._pending[request_id]["status"] = "approved"

        if remember:
            tool_name = self._pending[request_id]["tool_name"]
            self._approved_patterns.add(tool_name)
            self._save_config()

        return True

    def deny(self, request_id: str) -> bool:
        """Deny a pending request."""
        if request_id not in self._pending:
            return False
        self._pending[request_id]["status"] = "denied"
        return True

    def get_status(self, request_id: str) -> str:
        """Get status of an approval request.

        Returns:
            "pending", "approved", "denied", or "not_found".
        """
        req = self._pending.get(request_id)
        if not req:
            return "not_found"
        return req["status"]

    def get_request(self, request_id: str) -> dict[str, Any] | None:
        """Get full request details."""
        return self._pending.get(request_id)

    async def wait_for_approval(
        self, request_id: str, timeout: float = 300.0
    ) -> bool:
        """Wait for a pending approval to be resolved.

        Returns:
            True if approved, False if denied or timed out.
        """
        start = time.time()
        while time.time() - start < timeout:
            status = self.get_status(request_id)
            if status == "approved":
                return True
            if status == "denied":
                return False
            await asyncio.sleep(0.5)

        # Timeout → deny
        self.deny(request_id)
        return False

    def list_pending(self) -> list[dict[str, Any]]:
        """List all pending approval requests."""
        return [
            {"request_id": rid, **req}
            for rid, req in self._pending.items()
            if req["status"] == "pending"
        ]

    def clear_resolved(self) -> int:
        """Remove all non-pending requests. Returns count removed."""
        to_remove = [
            rid for rid, req in self._pending.items() if req["status"] != "pending"
        ]
        for rid in to_remove:
            del self._pending[rid]
        return len(to_remove)

    def _save_config(self) -> None:
        """Save approved patterns to disk."""
        data = {
            "version": 1,
            "defaults": {p: "approve" for p in sorted(self._approved_patterns)},
            "agents": {},
        }
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
