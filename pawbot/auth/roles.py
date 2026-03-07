"""Role and scope definitions for Pawbot access control.

Phase 19: Defines the role hierarchy and scope permissions.

Roles:
  owner    — Full access to everything
  operator — Admin, can approve actions and pair devices
  member   — Standard read/write access
  viewer   — Read-only access
  node     — Backend agent execution (workers, subagents)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ═══════════════════════════════════════════════════════════════════════════════
#  Scope Definitions
# ═══════════════════════════════════════════════════════════════════════════════

# Individual scopes — granular permission atoms
SCOPES = {
    # Admin scopes
    "admin":              "Full administrative access",
    "admin.config":       "Read and modify configuration",
    "admin.users":        "Manage users and devices",

    # Agent scopes
    "agent.execute":      "Execute agent tasks",
    "agent.tools":        "Use agent tools",
    "agent.fleet":        "Manage fleet workers",

    # Read scopes
    "read":               "Read access to all data",
    "read.memory":        "Read memory entries",
    "read.logs":          "Read system logs",
    "read.config":        "Read configuration",
    "read.status":        "Read agent status",

    # Write scopes
    "write":              "Write access to data",
    "write.memory":       "Create/update memories",
    "write.config":       "Modify configuration",
    "write.skills":       "Install/remove skills",
    "write.workflows":    "Create/modify workflows",

    # Approval scopes
    "approvals":          "Approve tool executions",
    "approvals.tools":    "Approve dangerous tool calls",
    "approvals.deploy":   "Approve deployments",

    # Pairing scopes
    "pairing":            "Pair new devices",
    "pairing.approve":    "Approve pairing requests",
    "pairing.revoke":     "Revoke paired devices",

    # Channel scopes
    "channels":           "Access communication channels",
    "channels.send":      "Send messages via channels",
    "channels.manage":    "Manage channel configurations",
}


# ═══════════════════════════════════════════════════════════════════════════════
#  Role Definitions
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class Role:
    """A named role with a set of scopes."""

    name: str
    description: str
    scopes: list[str]
    is_admin: bool = False

    def has_scope(self, scope: str) -> bool:
        """Check if this role has a specific scope (supports wildcard *)."""
        if "*" in self.scopes:
            return True
        if scope in self.scopes:
            return True
        # Check parent scope (e.g., "read" covers "read.memory")
        parts = scope.split(".")
        for i in range(len(parts)):
            parent = ".".join(parts[:i + 1])
            if parent in self.scopes:
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "scopes": self.scopes,
            "is_admin": self.is_admin,
        }


# Pre-defined roles
ROLES: dict[str, Role] = {
    "owner": Role(
        name="owner",
        description="Full access — system owner",
        scopes=["*"],
        is_admin=True,
    ),
    "operator": Role(
        name="operator",
        description="Admin operations — can approve actions and pair devices",
        scopes=[
            "admin", "approvals", "pairing",
            "read", "write",
            "agent.execute", "agent.tools", "agent.fleet",
            "channels",
        ],
        is_admin=True,
    ),
    "member": Role(
        name="member",
        description="Standard access — read/write but no admin",
        scopes=[
            "read", "write",
            "agent.execute", "agent.tools",
            "channels.send",
        ],
    ),
    "viewer": Role(
        name="viewer",
        description="Read-only access",
        scopes=["read"],
    ),
    "node": Role(
        name="node",
        description="Backend agent worker — execute tasks and use tools",
        scopes=[
            "agent.execute", "agent.tools",
            "read.memory", "read.config", "read.status",
            "write.memory",
        ],
    ),
}


def get_role(name: str) -> Role | None:
    """Get a role by name."""
    return ROLES.get(name)


def validate_scopes(scopes: list[str]) -> tuple[bool, list[str]]:
    """Validate a list of scopes. Returns (valid, bad_scopes)."""
    bad = [s for s in scopes if s != "*" and s not in SCOPES]
    return len(bad) == 0, bad
