"""Headless browser engine — Playwright-based browser automation.

Phase 8: Provides the core browser automation layer for PawBot.
Manages headless Chromium instances with session persistence,
domain blocking, and concurrent tab management.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
    - Domain blocking for SSRF prevention
    """

    MAX_PAGES = 5              # Max concurrent tabs
    PAGE_TIMEOUT_MS = 30_000   # 30s default navigation timeout
    SCREENSHOT_DIR = Path.home() / ".pawbot" / "browser" / "screenshots"
    STORAGE_DIR = Path.home() / ".pawbot" / "browser" / "storage"

    # Domains that are always blocked (SSRF prevention)
    _DEFAULT_BLOCKED = [
        "*.onion",                 # Tor hidden services
        "localhost",               # Prevent SSRF
        "127.0.0.1",
        "0.0.0.0",
        "169.254.169.254",         # AWS metadata endpoint
        "metadata.google.internal",  # GCP metadata
    ]

    def __init__(
        self,
        headless: bool = True,
        blocked_domains: list[str] | None = None,
        allowed_domains: list[str] | None = None,
        max_pages: int = 5,
        page_timeout_ms: int = 30_000,
        persist_state: bool = True,
        js_execution: bool = True,
    ):
        self._headless = headless
        self._blocked_domains = (blocked_domains or []) + self._DEFAULT_BLOCKED
        self._allowed_domains = allowed_domains or []  # Empty = all allowed
        self._max_pages = max_pages
        self._page_timeout_ms = page_timeout_ms
        self._persist_state = persist_state
        self._js_execution = js_execution

        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
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
        except ImportError:
            raise RuntimeError(
                "Playwright not installed. Run: pip install playwright && python -m playwright install chromium"
            )

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self._headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        # Create browser context, optionally restoring saved state
        storage_file = self.STORAGE_DIR / "state.json"
        context_kwargs: dict[str, Any] = {
            "viewport": {"width": 1280, "height": 720},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        }
        if self._persist_state and storage_file.exists():
            context_kwargs["storage_state"] = str(storage_file)

        self._context = await self._browser.new_context(**context_kwargs)
        self._context.set_default_timeout(self._page_timeout_ms)

        # Phase 8.3: Domain blocking via route interception
        if self._blocked_domains:
            await self._context.route("**/*", self._check_domain)

        self._started = True
        logger.info("Browser engine started (headless={})", self._headless)

    async def stop(self) -> None:
        """Shut down the browser and save state."""
        if not self._started:
            return

        # Save storage state (cookies, localStorage)
        if self._persist_state:
            try:
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

    @property
    def is_running(self) -> bool:
        """Check if the browser engine is running."""
        return self._started

    # ── Navigation & Content ──────────────────────────────────────────────

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

        url_hash = hashlib.md5(page.url.encode()).hexdigest()[:8]
        filename = f"screenshot_{tab_id}_{url_hash}.png"
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

    # ── Interaction ───────────────────────────────────────────────────────

    async def click(self, selector: str, tab_id: str = "main") -> bool:
        """Click an element."""
        await self._ensure_started()
        page = self._pages.get(tab_id)
        if not page:
            raise ValueError(f"No page with tab_id '{tab_id}'")
        try:
            await page.click(selector, timeout=10_000)
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
            await page.fill(selector, value, timeout=10_000)
            return True
        except Exception as e:
            logger.warning("Fill failed on '{}': {}", selector, e)
            return False

    async def select(self, selector: str, value: str, tab_id: str = "main") -> bool:
        """Select an option from a dropdown."""
        await self._ensure_started()
        page = self._pages.get(tab_id)
        if not page:
            raise ValueError(f"No page with tab_id '{tab_id}'")
        try:
            await page.select_option(selector, value, timeout=10_000)
            return True
        except Exception as e:
            logger.warning("Select failed on '{}': {}", selector, e)
            return False

    async def evaluate_js(self, expression: str, tab_id: str = "main") -> Any:
        """Execute JavaScript in the page context."""
        if not self._js_execution:
            raise RuntimeError("JavaScript execution is disabled in browser config")
        await self._ensure_started()
        page = self._pages.get(tab_id)
        if not page:
            raise ValueError(f"No page with tab_id '{tab_id}'")
        return await page.evaluate(expression)

    async def wait_for_selector(
        self, selector: str, tab_id: str = "main", timeout_ms: int = 10_000
    ) -> bool:
        """Wait for an element to appear on the page."""
        await self._ensure_started()
        page = self._pages.get(tab_id)
        if not page:
            raise ValueError(f"No page with tab_id '{tab_id}'")
        try:
            await page.wait_for_selector(selector, timeout=timeout_ms)
            return True
        except Exception:
            return False

    # ── Tab Management ────────────────────────────────────────────────────

    async def get_cookies(self, tab_id: str = "main") -> list[dict]:
        """Get all cookies for the current context."""
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

    # ── Internal ──────────────────────────────────────────────────────────

    async def _ensure_started(self) -> None:
        if not self._started:
            await self.start()

    async def _get_or_create_page(self, tab_id: str) -> Any:
        if tab_id in self._pages:
            return self._pages[tab_id]
        if len(self._pages) >= self._max_pages:
            # Close oldest tab
            oldest = next(iter(self._pages))
            await self.close_tab(oldest)
        page = await self._context.new_page()
        self._pages[tab_id] = page
        return page

    async def _check_domain(self, route: Any) -> None:
        """Block requests to disallowed domains (SSRF prevention)."""
        url = route.request.url
        hostname = urlparse(url).hostname or ""

        # If allowed_domains is set, only allow those
        if self._allowed_domains:
            if not any(fnmatch.fnmatch(hostname, pat) for pat in self._allowed_domains):
                logger.warning("Blocked browser request to unlisted domain: {}", hostname)
                await route.abort("blockedbyclient")
                return

        # Check blocked domains
        for pattern in self._blocked_domains:
            if fnmatch.fnmatch(hostname, pattern):
                logger.warning("Blocked browser request to: {}", hostname)
                await route.abort("blockedbyclient")
                return

        await route.continue_()
