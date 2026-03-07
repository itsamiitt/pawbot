"""Tool registry for dynamic tool management."""

import asyncio

from loguru import logger
from typing import Any

from pawbot.agent.tools.base import Tool

# Default per-tool timeout overrides (seconds)
_DEFAULT_TOOL_TIMEOUTS: dict[str, float] = {
    "exec": 120.0,         # shell commands may be slow
    "web_search": 30.0,
    "web_fetch": 45.0,
    "browser": 120.0,      # browser automation is slow
    "read_file": 10.0,
    "write_file": 10.0,
    "list_dir": 10.0,
}
_DEFAULT_TIMEOUT = 60.0


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format."""
        return [tool.to_schema() for tool in self._tools.values()]

    async def execute(self, name: str, params: dict[str, Any], timeout: float | None = None) -> str:
        """Execute a tool by name with given parameters and timeout protection.

        Args:
            name: Tool name
            params: Tool arguments
            timeout: Override timeout in seconds (default: per-tool or 60s)
        """
        _HINT = "\n\n[Analyze the error above and try a different approach.]"

        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"

        effective_timeout = timeout or _DEFAULT_TOOL_TIMEOUTS.get(name, _DEFAULT_TIMEOUT)

        try:
            errors = tool.validate_params(params)
            if errors:
                return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors) + _HINT

            result = await asyncio.wait_for(
                tool.execute(**params),
                timeout=effective_timeout,
            )
            if isinstance(result, str) and result.startswith("Error"):
                return result + _HINT
            return result
        except asyncio.TimeoutError:
            logger.warning("Tool '{}' timed out after {:.0f}s", name, effective_timeout)
            return f"Error: Tool '{name}' timed out after {effective_timeout:.0f}s" + _HINT
        except Exception as e:
            logger.error("Tool '{}' failed: {}", name, e)
            return f"Error executing {name}: {str(e)}" + _HINT


    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
