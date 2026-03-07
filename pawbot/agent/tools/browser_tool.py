"""Browser automation tools for the agent (Phase 8.2).

Registers 5 browser tools that the LLM can invoke:
  - browse         — Navigate to a URL and extract text
  - screenshot     — Capture a screenshot
  - browser_click  — Click an element by CSS selector
  - browser_fill   — Fill a form field
  - browser_eval   — Execute JavaScript in page context
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from pawbot.agent.tools.base import Tool
from pawbot.browser.engine import BrowserEngine


# ── Module-level singleton ────────────────────────────────────────────────────

_engine: BrowserEngine | None = None


def get_browser_engine() -> BrowserEngine:
    """Get or create the global BrowserEngine singleton."""
    global _engine
    if _engine is None:
        _engine = BrowserEngine(headless=True)
    return _engine


def set_browser_engine(engine: BrowserEngine) -> None:
    """Replace the global browser engine (used for testing or custom config)."""
    global _engine
    _engine = engine


# ── Tools ─────────────────────────────────────────────────────────────────────


class BrowseTool(Tool):
    """Navigate to a URL and return page content."""

    name = "browse"
    description = (
        "Navigate to a URL in a headless browser. Returns the page title, URL, "
        "and visible text content. Use this for JavaScript-rendered pages that "
        "web_fetch cannot handle."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to navigate to"},
            "tab_id": {
                "type": "string",
                "description": "Tab identifier (default: 'main'). Use different IDs for multiple tabs.",
                "default": "main",
            },
        },
        "required": ["url"],
    }

    async def execute(self, url: str, tab_id: str = "main", **kwargs: Any) -> str:
        engine = get_browser_engine()
        info = await engine.navigate(url, tab_id)
        text = await engine.get_text(tab_id)
        # Truncate to avoid context window overflow
        if len(text) > 8000:
            text = text[:8000] + "\n... [truncated — use browser_eval to extract specific data]"
        return (
            f"**Page:** {info['title']}\n"
            f"**URL:** {info['url']}\n"
            f"**Status:** {info['status']}\n\n"
            f"---\n\n{text}"
        )


class ScreenshotTool(Tool):
    """Capture a screenshot of the current browser page."""

    name = "screenshot"
    description = (
        "Take a screenshot of a browser tab. Returns the file path to the "
        "saved PNG image. Use after 'browse' to visually inspect a page."
    )
    parameters = {
        "type": "object",
        "properties": {
            "tab_id": {
                "type": "string",
                "description": "Tab to screenshot (default: 'main')",
                "default": "main",
            },
            "full_page": {
                "type": "boolean",
                "description": "Capture the full scrollable page (default: false)",
                "default": False,
            },
            "selector": {
                "type": "string",
                "description": "CSS selector to screenshot a specific element (optional)",
            },
        },
    }

    async def execute(
        self, tab_id: str = "main", full_page: bool = False,
        selector: str | None = None, **kwargs: Any,
    ) -> str:
        engine = get_browser_engine()
        path = await engine.screenshot(tab_id, full_page, selector)
        return f"Screenshot saved: {path}"


class ClickTool(Tool):
    """Click an element on the page."""

    name = "browser_click"
    description = (
        "Click an element on the browser page using a CSS selector. "
        "Use after 'browse' to interact with buttons, links, etc."
    )
    parameters = {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "CSS selector of the element to click",
            },
            "tab_id": {
                "type": "string",
                "description": "Tab to interact with (default: 'main')",
                "default": "main",
            },
        },
        "required": ["selector"],
    }

    async def execute(self, selector: str, tab_id: str = "main", **kwargs: Any) -> str:
        engine = get_browser_engine()
        ok = await engine.click(selector, tab_id)
        if ok:
            return f"Clicked '{selector}' successfully"
        return f"Error: Failed to click '{selector}' — element not found or not clickable"


class FillFormTool(Tool):
    """Fill a form field on the page."""

    name = "browser_fill"
    description = (
        "Fill a form input on the browser page. Clears the field first, "
        "then types the value. Use for text inputs, textareas, etc."
    )
    parameters = {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "CSS selector of the input field",
            },
            "value": {
                "type": "string",
                "description": "Value to type into the field",
            },
            "tab_id": {
                "type": "string",
                "description": "Tab to interact with (default: 'main')",
                "default": "main",
            },
        },
        "required": ["selector", "value"],
    }

    async def execute(self, selector: str, value: str, tab_id: str = "main", **kwargs: Any) -> str:
        engine = get_browser_engine()
        ok = await engine.fill(selector, value, tab_id)
        if ok:
            return f"Filled '{selector}' with value"
        return f"Error: Failed to fill '{selector}' — element not found"


class EvalJSTool(Tool):
    """Execute JavaScript in the browser page."""

    name = "browser_eval"
    description = (
        "Execute JavaScript in the browser page context and return the result. "
        "Use for extracting structured data, interacting with SPAs, or checking "
        "page state. Returns the serialized result."
    )
    parameters = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "JavaScript expression to evaluate",
            },
            "tab_id": {
                "type": "string",
                "description": "Tab to execute in (default: 'main')",
                "default": "main",
            },
        },
        "required": ["expression"],
    }

    async def execute(self, expression: str, tab_id: str = "main", **kwargs: Any) -> str:
        engine = get_browser_engine()
        result = await engine.evaluate_js(expression, tab_id)
        return str(result)


# ── Registry helper ───────────────────────────────────────────────────────────

BROWSER_TOOLS: list[type[Tool]] = [
    BrowseTool,
    ScreenshotTool,
    ClickTool,
    FillFormTool,
    EvalJSTool,
]
