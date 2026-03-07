"""Chrome browser tools — tool definitions for PawBot agent (Phase 16.3).

These tools use the real Chrome browser via the extension + native messaging.
They are registered as available tools when the extension is connected.
"""

from __future__ import annotations

from typing import Any


CHROME_TOOLS: list[dict[str, Any]] = [
    {
        "name": "chrome_read_page",
        "description": (
            "Read the current page as an accessibility tree. Returns structured text "
            "with [ref_id] for each element. Use filter='interactive' to see only "
            "clickable/typeable elements. Use ref_id to zoom into a specific subtree."
        ),
        "parameters": {
            "filter": {
                "type": "string",
                "enum": ["all", "interactive"],
                "default": "all",
                "description": "Filter mode: 'all' for full page, 'interactive' for clickable/typeable only",
            },
            "depth": {
                "type": "integer",
                "default": 10,
                "description": "Maximum DOM traversal depth (default: 10)",
            },
            "ref_id": {
                "type": "string",
                "description": "Focus on a specific element subtree by its ref ID",
            },
        },
    },
    {
        "name": "chrome_click",
        "description": (
            "Click an element in the user's browser. Use ref_id from read_page, "
            "or coordinate [x, y]. For complex widgets (canvas, custom inputs), "
            "use chrome_click_advanced."
        ),
        "parameters": {
            "ref_id": {
                "type": "string",
                "description": "Element ref ID from read_page output",
            },
            "coordinate": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Click at [x, y] viewport coordinates",
            },
        },
    },
    {
        "name": "chrome_click_advanced",
        "description": (
            "CDP-based click — uses Chrome DevTools Protocol for complex widgets, "
            "canvas elements, and custom inputs that don't respond to DOM .click()."
        ),
        "parameters": {
            "ref_id": {
                "type": "string",
                "description": "Element ref ID from read_page",
            },
            "coordinate": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Click at [x, y] viewport coordinates",
            },
        },
    },
    {
        "name": "chrome_type",
        "description": (
            "Type text into a form field. Sets value directly and fires input/change events."
        ),
        "parameters": {
            "ref_id": {
                "type": "string",
                "required": True,
                "description": "Element ref ID from read_page",
            },
            "text": {
                "type": "string",
                "required": True,
                "description": "Text to type into the field",
            },
        },
    },
    {
        "name": "chrome_type_advanced",
        "description": (
            "CDP keystroke-level typing — sends individual key events with human-like "
            "delays. Use for form fields that validate per-keystroke."
        ),
        "parameters": {
            "ref_id": {
                "type": "string",
                "description": "Element ref ID to focus before typing",
            },
            "text": {
                "type": "string",
                "required": True,
                "description": "Text to type character by character",
            },
        },
    },
    {
        "name": "chrome_navigate",
        "description": "Navigate the active tab to a URL.",
        "parameters": {
            "url": {
                "type": "string",
                "required": True,
                "description": "URL to navigate to",
            },
        },
    },
    {
        "name": "chrome_screenshot",
        "description": "Capture a screenshot of the active tab. Returns base64 PNG.",
        "parameters": {},
    },
    {
        "name": "chrome_get_tabs",
        "description": "List all open tabs with their IDs, titles, and URLs.",
        "parameters": {},
    },
    {
        "name": "chrome_switch_tab",
        "description": "Switch to a tab by its ID.",
        "parameters": {
            "tabId": {
                "type": "integer",
                "required": True,
                "description": "Tab ID from chrome_get_tabs",
            },
        },
    },
    {
        "name": "chrome_new_tab",
        "description": "Open a new tab, optionally with a URL.",
        "parameters": {
            "url": {
                "type": "string",
                "default": "about:blank",
                "description": "URL to open in the new tab",
            },
        },
    },
    {
        "name": "chrome_close_tab",
        "description": "Close a tab by its ID.",
        "parameters": {
            "tabId": {
                "type": "integer",
                "required": True,
                "description": "Tab ID to close",
            },
        },
    },
    {
        "name": "chrome_scroll",
        "description": "Scroll the page in a direction.",
        "parameters": {
            "direction": {
                "type": "string",
                "enum": ["up", "down", "left", "right", "top", "bottom"],
                "default": "down",
                "description": "Scroll direction",
            },
            "amount": {
                "type": "integer",
                "default": 500,
                "description": "Pixels to scroll (ignored for top/bottom)",
            },
        },
    },
    {
        "name": "chrome_extract",
        "description": (
            "Extract structured data from the page: text, links, tables, forms, "
            "or metadata. Returns extracted data as JSON."
        ),
        "parameters": {
            "what": {
                "type": "string",
                "enum": ["text", "links", "tables", "forms", "metadata"],
                "default": "text",
                "description": "Type of data to extract",
            },
        },
    },
]


def get_chrome_tool_names() -> list[str]:
    """Return list of all Chrome tool names."""
    return [t["name"] for t in CHROME_TOOLS]


def get_chrome_tool(name: str) -> dict[str, Any] | None:
    """Get a Chrome tool definition by name."""
    for tool in CHROME_TOOLS:
        if tool["name"] == name:
            return tool
    return None


def map_chrome_tool_to_extension(tool_name: str) -> str | None:
    """Map PawBot tool name to extension tool name.

    e.g. 'chrome_read_page' → 'read_page'
         'chrome_click_advanced' → 'click_advanced'
    """
    if not tool_name.startswith("chrome_"):
        return None
    return tool_name[7:]  # Remove 'chrome_' prefix
