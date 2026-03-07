# Phase 8 — Browser Integration & Sandbox

> **Goal:** Give PawBot full browser control — headless automation, a Chrome extension for user-side context, and a sandboxed execution environment.  
> **Duration:** 14-21 days  
> **Risk Level:** High (external dependency, security surface)  
> **Depends On:** Phase 0 (clean imports), Phase 1 (tool timeouts), Phase 6 (security)

---

## Why This Phase Exists

OpenClaw ships with full browser integration:
- `browser.enabled: true` with auto-start
- A dedicated Chrome extension (`browser/chrome-extension/`)
- A sandboxed browser environment (`sandbox.browser`)
- Canvas system for rendering (`canvas/index.html`)

PawBot has **zero browser capability**. This phase adds it.

---

## 8.1 — Headless Browser Engine

### Problem
PawBot cannot browse the web, fill forms, scrape pages, or take screenshots. The `web_fetch` tool only does raw HTTP.

### Solution
Create a Playwright-based browser engine with session management.

**Create:** `pawbot/browser/engine.py`

```python
"""Headless browser engine — Playwright-based browser automation."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from pathlib import Path
from typing import Any

from loguru import logger


class BrowserEngine:
    """Manages headless Chromium instances for agent browsing tasks.
    
    Features:
    - Persistent browser contexts (survive across tool calls)
    - Screenshot capture (full page or element)
    - Cookie/session persistence
    - DOM querying and text extraction
    - JavaScript execution
    - Form filling and clicking
    - PDF generation
    """

    MAX_PAGES = 5          # Max concurrent tabs
    PAGE_TIMEOUT_MS = 30000  # 30s default navigation timeout
    SCREENSHOT_DIR = Path.home() / ".pawbot" / "browser" / "screenshots"
    STORAGE_DIR = Path.home() / ".pawbot" / "browser" / "storage"

    def __init__(self, headless: bool = True):
        self._headless = headless
        self._browser = None
        self._context = None
        self._pages: dict[str, Any] = {}  # tab_id -> Page
        self._started = False
        self.SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        self.STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    async def start(self) -> None:
        """Launch the browser instance."""
        if self._started:
            return
        try:
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=self._headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            # Create persistent context with storage state
            storage_file = self.STORAGE_DIR / "state.json"
            if storage_file.exists():
                self._context = await self._browser.new_context(
                    storage_state=str(storage_file),
                    viewport={"width": 1280, "height": 720},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                )
            else:
                self._context = await self._browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                )
            self._context.set_default_timeout(self.PAGE_TIMEOUT_MS)
            self._started = True
            logger.info("Browser engine started (headless={})", self._headless)
        except ImportError:
            raise RuntimeError(
                "Playwright not installed. Run: pip install playwright && python -m playwright install chromium"
            )

    async def stop(self) -> None:
        """Shut down the browser and save state."""
        if not self._started:
            return
        try:
            # Save storage state (cookies, localStorage)
            storage_file = self.STORAGE_DIR / "state.json"
            await self._context.storage_state(path=str(storage_file))
            logger.debug("Browser state saved to {}", storage_file)
        except Exception:
            logger.debug("Could not save browser state")
        try:
            await self._context.close()
            await self._browser.close()
            await self._pw.stop()
        except Exception:
            pass
        self._started = False
        self._pages.clear()
        logger.info("Browser engine stopped")

    async def navigate(self, url: str, tab_id: str = "main") -> dict[str, Any]:
        """Navigate to a URL and return page info."""
        await self._ensure_started()
        page = await self._get_or_create_page(tab_id)

        response = await page.goto(url, wait_until="domcontentloaded")
        status = response.status if response else 0
        title = await page.title()

        return {
            "tab_id": tab_id,
            "url": page.url,
            "title": title,
            "status": status,
        }

    async def screenshot(
        self, tab_id: str = "main", full_page: bool = False, selector: str | None = None
    ) -> str:
        """Take a screenshot. Returns the file path."""
        await self._ensure_started()
        page = self._pages.get(tab_id)
        if not page:
            raise ValueError(f"No page with tab_id '{tab_id}'")

        filename = f"screenshot_{tab_id}_{hashlib.md5(page.url.encode()).hexdigest()[:8]}.png"
        filepath = self.SCREENSHOT_DIR / filename

        if selector:
            element = await page.query_selector(selector)
            if element:
                await element.screenshot(path=str(filepath))
            else:
                raise ValueError(f"Element not found: {selector}")
        else:
            await page.screenshot(path=str(filepath), full_page=full_page)

        logger.debug("Screenshot saved: {}", filepath)
        return str(filepath)

    async def get_text(self, tab_id: str = "main", selector: str = "body") -> str:
        """Extract visible text from the page or a specific element."""
        await self._ensure_started()
        page = self._pages.get(tab_id)
        if not page:
            raise ValueError(f"No page with tab_id '{tab_id}'")

        element = await page.query_selector(selector)
        if not element:
            return ""
        return (await element.inner_text()).strip()

    async def get_html(self, tab_id: str = "main", selector: str = "body") -> str:
        """Get innerHTML of an element."""
        await self._ensure_started()
        page = self._pages.get(tab_id)
        if not page:
            raise ValueError(f"No page with tab_id '{tab_id}'")

        element = await page.query_selector(selector)
        if not element:
            return ""
        return await element.inner_html()

    async def click(self, selector: str, tab_id: str = "main") -> bool:
        """Click an element."""
        await self._ensure_started()
        page = self._pages.get(tab_id)
        if not page:
            raise ValueError(f"No page with tab_id '{tab_id}'")
        try:
            await page.click(selector, timeout=10000)
            return True
        except Exception as e:
            logger.warning("Click failed on '{}': {}", selector, e)
            return False

    async def fill(self, selector: str, value: str, tab_id: str = "main") -> bool:
        """Fill a form field."""
        await self._ensure_started()
        page = self._pages.get(tab_id)
        if not page:
            raise ValueError(f"No page with tab_id '{tab_id}'")
        try:
            await page.fill(selector, value, timeout=10000)
            return True
        except Exception as e:
            logger.warning("Fill failed on '{}': {}", selector, e)
            return False

    async def evaluate_js(self, expression: str, tab_id: str = "main") -> Any:
        """Execute JavaScript in the page context."""
        await self._ensure_started()
        page = self._pages.get(tab_id)
        if not page:
            raise ValueError(f"No page with tab_id '{tab_id}'")
        return await page.evaluate(expression)

    async def get_cookies(self, tab_id: str = "main") -> list[dict]:
        """Get all cookies for the current page."""
        await self._ensure_started()
        return await self._context.cookies()

    async def close_tab(self, tab_id: str) -> None:
        """Close a browser tab."""
        page = self._pages.pop(tab_id, None)
        if page:
            await page.close()

    async def list_tabs(self) -> list[dict[str, str]]:
        """List all open tabs."""
        tabs = []
        for tid, page in self._pages.items():
            try:
                title = await page.title()
                tabs.append({"tab_id": tid, "url": page.url, "title": title})
            except Exception:
                tabs.append({"tab_id": tid, "url": "unknown", "title": "error"})
        return tabs

    async def _ensure_started(self) -> None:
        if not self._started:
            await self.start()

    async def _get_or_create_page(self, tab_id: str):
        if tab_id in self._pages:
            return self._pages[tab_id]
        if len(self._pages) >= self.MAX_PAGES:
            # Close oldest tab
            oldest = next(iter(self._pages))
            await self.close_tab(oldest)
        page = await self._context.new_page()
        self._pages[tab_id] = page
        return page
```

---

## 8.2 — Browser Agent Tools

Register browser actions as agent tools so the LLM can use them.

**Create:** `pawbot/agent/tools/browser_tool.py`

```python
"""Browser automation tools for the agent."""

from __future__ import annotations

from typing import Any

from loguru import logger

from pawbot.agent.tools.base import Tool, ToolResult
from pawbot.browser.engine import BrowserEngine


# Module-level singleton
_engine: BrowserEngine | None = None


def _get_engine() -> BrowserEngine:
    global _engine
    if _engine is None:
        _engine = BrowserEngine(headless=True)
    return _engine


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

    async def run(self, url: str, tab_id: str = "main") -> str:
        engine = _get_engine()
        info = await engine.navigate(url, tab_id)
        text = await engine.get_text(tab_id)
        # Truncate to avoid context overflow
        if len(text) > 8000:
            text = text[:8000] + "\n... [truncated — use get_element_text for specific sections]"
        return (
            f"**Page:** {info['title']}\n"
            f"**URL:** {info['url']}\n"
            f"**Status:** {info['status']}\n\n"
            f"---\n\n{text}"
        )


class ScreenshotTool(Tool):
    """Capture a screenshot of the current browser page."""

    name = "screenshot"
    description = "Take a screenshot of a browser tab. Returns the file path to the saved PNG image."
    parameters = {
        "type": "object",
        "properties": {
            "tab_id": {"type": "string", "default": "main"},
            "full_page": {"type": "boolean", "default": False},
            "selector": {
                "type": "string",
                "description": "CSS selector to screenshot a specific element (optional)",
            },
        },
    }

    async def run(self, tab_id: str = "main", full_page: bool = False, selector: str | None = None) -> str:
        engine = _get_engine()
        path = await engine.screenshot(tab_id, full_page, selector)
        return f"Screenshot saved: {path}"


class ClickTool(Tool):
    """Click an element on the page."""

    name = "browser_click"
    description = "Click an element on the browser page using a CSS selector."
    parameters = {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "CSS selector of the element to click"},
            "tab_id": {"type": "string", "default": "main"},
        },
        "required": ["selector"],
    }

    async def run(self, selector: str, tab_id: str = "main") -> str:
        engine = _get_engine()
        ok = await engine.click(selector, tab_id)
        if ok:
            return f"Clicked '{selector}' successfully"
        return f"Failed to click '{selector}' — element not found or not clickable"


class FillFormTool(Tool):
    """Fill a form field on the page."""

    name = "browser_fill"
    description = "Fill a form input on the browser page using a CSS selector."
    parameters = {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "CSS selector of the input field"},
            "value": {"type": "string", "description": "Value to type into the field"},
            "tab_id": {"type": "string", "default": "main"},
        },
        "required": ["selector", "value"],
    }

    async def run(self, selector: str, value: str, tab_id: str = "main") -> str:
        engine = _get_engine()
        ok = await engine.fill(selector, value, tab_id)
        if ok:
            return f"Filled '{selector}' with value"
        return f"Failed to fill '{selector}' — element not found"


class EvalJSTool(Tool):
    """Execute JavaScript in the browser page."""

    name = "browser_eval"
    description = (
        "Execute JavaScript in the browser page context and return the result. "
        "Use for extracting structured data, interacting with SPAs, or checking page state."
    )
    parameters = {
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "JavaScript expression to evaluate"},
            "tab_id": {"type": "string", "default": "main"},
        },
        "required": ["expression"],
    }

    async def run(self, expression: str, tab_id: str = "main") -> str:
        engine = _get_engine()
        result = await engine.evaluate_js(expression, tab_id)
        return str(result)


# Register all browser tools
BROWSER_TOOLS = [BrowseTool, ScreenshotTool, ClickTool, FillFormTool, EvalJSTool]
```

---

## 8.3 — Browser Sandbox Configuration

Add browser settings to the config schema and sandbox mode for security.

**File:** `pawbot/config/schema.py` — add:

```python
class BrowserSandboxConfig(BaseModel):
    """Browser sandbox configuration."""
    enabled: bool = False
    headless: bool = True
    auto_start: bool = False          # Start browser on agent boot
    max_pages: int = 5                # Max concurrent tabs
    page_timeout_ms: int = 30000      # Navigation timeout
    allowed_domains: list[str] = Field(default_factory=list)  # Empty = all allowed
    blocked_domains: list[str] = Field(
        default_factory=lambda: [
            "*.onion",                 # Tor hidden services
            "localhost:*",             # Prevent SSRF
            "127.0.0.1:*",
            "169.254.169.254",         # AWS metadata endpoint
        ]
    )
    persist_state: bool = True        # Save cookies/localStorage across sessions
    screenshot_retention_days: int = 7
    js_execution: bool = True         # Allow JS eval tool (high risk)
    download_dir: str = ""            # Where to save downloads (empty = disabled)


class SandboxConfig(BaseModel):
    """Overall sandbox configuration."""
    mode: str = "off"                 # "off", "basic", "strict"
    browser: BrowserSandboxConfig = Field(default_factory=BrowserSandboxConfig)
```

**Domain blocking in engine.py** — add route interception:

```python
# In BrowserEngine.start(), after creating context:
if config.blocked_domains:
    await self._context.route(
        "**/*",
        lambda route: self._check_domain(route, config.blocked_domains),
    )

async def _check_domain(self, route, blocked: list[str]) -> None:
    """Block requests to disallowed domains."""
    import fnmatch
    url = route.request.url
    from urllib.parse import urlparse
    domain = urlparse(url).hostname or ""
    for pattern in blocked:
        if fnmatch.fnmatch(domain, pattern.replace("*.", "")):
            logger.warning("Blocked browser request to: {}", domain)
            await route.abort("blockedbyclient")
            return
    await route.continue_()
```

---

## 8.4 — Browser Lifecycle Management

Integrate browser start/stop with the agent lifecycle.

**File:** `pawbot/agent/loop.py` — add to `AgentLoop.__init__`:

```python
# Browser engine (lazy init)
self._browser_enabled = config.get("sandbox", {}).get("browser", {}).get("enabled", False)
self._browser_engine: BrowserEngine | None = None

if self._browser_enabled and config.get("sandbox", {}).get("browser", {}).get("auto_start", False):
    asyncio.create_task(self._init_browser())
```

**Add to `_graceful_shutdown`:**

```python
# 5. Close browser
if self._browser_engine:
    try:
        await self._browser_engine.stop()
        logger.info("Browser engine stopped")
    except Exception:
        logger.exception("Error stopping browser during shutdown")
```

---

## 8.5 — Screenshot Retention Cleanup

**Create:** `pawbot/browser/cleanup.py`

```python
"""Clean up old browser screenshots and cached data."""

import time
from pathlib import Path

from loguru import logger


def cleanup_screenshots(
    directory: Path | None = None,
    max_age_days: int = 7,
) -> int:
    """Delete screenshots older than max_age_days. Returns count deleted."""
    if directory is None:
        directory = Path.home() / ".pawbot" / "browser" / "screenshots"
    if not directory.exists():
        return 0

    cutoff = time.time() - (max_age_days * 86400)
    deleted = 0

    for f in directory.glob("*.png"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            deleted += 1

    if deleted:
        logger.info("Cleaned up {} old screenshots", deleted)
    return deleted
```

---

## Prerequisites

```bash
pip install "playwright>=1.48.0"
python -m playwright install chromium
```

Add to `pyproject.toml`:
```toml
[project.optional-dependencies]
browser = ["playwright>=1.48.0"]
```

---

## Verification Checklist — Phase 8 Complete

- [ ] `playwright` installed and Chromium downloaded
- [ ] `pawbot/browser/engine.py` — `BrowserEngine` class with navigate, screenshot, click, fill, eval_js
- [ ] `pawbot/agent/tools/browser_tool.py` — 5 tools registered (browse, screenshot, click, fill, eval_js)
- [ ] `BrowserSandboxConfig` in `config/schema.py` with domain blocking
- [ ] Browser auto-starts if `sandbox.browser.auto_start: true`
- [ ] Browser shuts down gracefully on agent shutdown
- [ ] Screenshots older than `screenshot_retention_days` are cleaned up
- [ ] Blocked domains (localhost, AWS metadata, .onion) are intercepted
- [ ] Agent can browse a JS-rendered page and extract text via `browse` tool
- [ ] All tests pass: `pytest tests/ -v --tb=short`
