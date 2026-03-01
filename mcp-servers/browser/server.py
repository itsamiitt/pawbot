#!/usr/bin/env python3
"""Browser Intelligence MCP Server.

Registered as: mcp_servers.browser in ~/.pawbot/config.json
Session cookies: ~/.pawbot/browser-sessions/{session_name}.json
Dependencies: playwright>=1.40.0
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

try:
    from playwright.async_api import (
        async_playwright,
        Browser,
        BrowserContext,
        Page,
    )

    _PLAYWRIGHT_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    _PLAYWRIGHT_AVAILABLE = False
    async_playwright = None  # type: ignore[assignment, misc]
    Browser = None  # type: ignore[assignment, misc]
    BrowserContext = None  # type: ignore[assignment, misc]
    Page = None  # type: ignore[assignment, misc]


# ── Logging ───────────────────────────────────────────────────────────────────


def _configure_logger() -> logging.Logger:
    log_path = Path.home() / ".pawbot" / "logs" / "pawbot.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _logger = logging.getLogger("pawbot.mcp.browser")
    if not _logger.handlers:
        _logger.setLevel(logging.INFO)
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        _logger.addHandler(handler)
        _logger.propagate = False
    return _logger


logger = _configure_logger()
mcp = FastMCP(name="browser")

# ── Session & Configuration Paths ─────────────────────────────────────────────

SESSIONS_DIR = os.path.expanduser("~/.pawbot/browser-sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)

# ── Global State ──────────────────────────────────────────────────────────────
# One playwright instance shared across all tool calls within this process.

_playwright_instance = None
_browser: Any = None
_contexts: dict[str, Any] = {}   # session_name → BrowserContext
_pages: dict[str, Any] = {}      # session_name → active Page

# ── Anti-Detection Configuration ──────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = {runtime: {}};
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
"""

# ── Tool Registry ─────────────────────────────────────────────────────────────

TOOL_NAMES = [
    "browser_open",
    "browser_see",
    "browser_click",
    "browser_type",
    "browser_form_fill",
    "browser_extract",
    "browser_wait",
    "browser_scroll",
    "browser_key",
    "browser_script",
    "browser_download",
    "browser_pdf",
    "browser_session_save",
    "browser_multi_tab",
]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _require_playwright() -> dict[str, Any] | None:
    """Return an error dict if playwright is not installed, else None."""
    if not _PLAYWRIGHT_AVAILABLE:
        return {"error": "playwright is not installed — run: pip install playwright && playwright install chromium"}
    return None


def _truncate(text: str | None, limit: int) -> str:
    return (text or "")[:limit]


def _run_async(coro):
    """Run an async coroutine synchronously.

    Handles the case where an event loop may or may not already be running.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # We're inside an already-running event loop (e.g. FastMCP's event loop).
        # Create a new loop in a thread to avoid "cannot call asyncio.run() while
        # another loop is running".
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=120)
    else:
        return asyncio.run(coro)


# ── Browser Lifecycle ─────────────────────────────────────────────────────────


async def _get_browser():
    """Initialize browser on first call, reuse after that."""
    global _playwright_instance, _browser
    if _browser is None or not _browser.is_connected():
        _playwright_instance = await async_playwright().start()
        _browser = await _playwright_instance.chromium.launch(headless=True)
        logger.info("Browser: launched headless Chromium instance")
    return _browser


async def _get_context(session: str = "default") -> Any:
    """Get or create browser context for named session."""
    global _contexts
    if session not in _contexts:
        browser = await _get_browser()
        viewport_width = random.randint(1280, 1400)
        viewport_height = random.randint(700, 800)
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": viewport_width, "height": viewport_height},
            locale="en-US",
            timezone_id="America/New_York",
        )
        await context.add_init_script(STEALTH_INIT_SCRIPT)

        # Restore saved cookies if available
        cookie_file = os.path.join(SESSIONS_DIR, f"{session}.json")
        if os.path.exists(cookie_file):
            with open(cookie_file, encoding="utf-8") as f:
                cookies = json.load(f)
            await context.add_cookies(cookies)
            logger.info("Browser: restored session cookies for '%s'", session)

        _contexts[session] = context
        logger.info(
            "Browser: created context '%s' viewport=%dx%d",
            session, viewport_width, viewport_height,
        )

    return _contexts[session]


async def _get_page(session: str = "default") -> Any:
    """Get or create page for session."""
    global _pages
    if session not in _pages or _pages[session].is_closed():
        context = await _get_context(session)
        _pages[session] = await context.new_page()
    return _pages[session]


# ── Human-like Behaviour Middleware ───────────────────────────────────────────


async def _human_pause(pre: bool = True, post: bool = True):
    """Add human-like delays around actions."""
    if pre:
        await asyncio.sleep(random.uniform(0.2, 0.8))
    if post:
        await asyncio.sleep(random.uniform(0.1, 0.3))


# ── Element Finder ────────────────────────────────────────────────────────────


async def _find_element(page, target: str) -> dict[str, Any]:
    """Find page element using accessibility tree first, then CSS, then coords.

    Target formats:
        "Submit button"     → accessibility text search
        "aria:submit"       → ARIA label
        "css:#submit-btn"   → CSS selector
        "x:450,y:300"       → coordinates (returns coord dict)
    """
    if target.startswith("x:") and "y:" in target:
        parts = target.replace("x:", "").replace("y:", "").split(",")
        return {"type": "coords", "x": int(parts[0]), "y": int(parts[1])}

    if target.startswith("css:"):
        selector = target[4:]
        el = page.locator(selector).first
        return {"type": "locator", "locator": el}

    if target.startswith("aria:"):
        label = target[5:]
        el = page.get_by_label(label).first
        return {"type": "locator", "locator": el}

    # Default: accessibility tree text search
    el = page.get_by_text(target, exact=False).first
    return {"type": "locator", "locator": el}


# ══════════════════════════════════════════════════════════════════════════════
#  TOOL IMPLEMENTATIONS
# ══════════════════════════════════════════════════════════════════════════════


def list_tools() -> dict[str, Any]:
    """Return explicit tool inventory for tests and diagnostics."""
    return {"tools": TOOL_NAMES.copy(), "count": len(TOOL_NAMES)}


@mcp.tool()
def browser_open(
    url: str,
    session: str = "default",
    wait_for: str = "networkidle",
    restore_session: bool = True,
) -> dict[str, Any]:
    """Open URL in named browser session."""
    missing = _require_playwright()
    if missing:
        return missing

    async def _run():
        page = await _get_page(session)
        try:
            response = await page.goto(url, wait_until=wait_for, timeout=30000)
            title = await page.title()
            final_url = page.url
            # Brief text preview
            text = await page.inner_text("body")
            preview = text[:300].strip()
            logger.info("Browser: opened %s → %s (status=%s)", url, title, response.status if response else "?")
            return {
                "success": True,
                "title": title,
                "url": final_url,
                "status": response.status if response else None,
                "preview": preview,
            }
        except Exception as e:
            logger.error("Browser: failed to open %s — %s", url, e)
            return {"success": False, "error": str(e), "url": url}

    return _run_async(_run())


@mcp.tool()
def browser_see(
    session: str = "default",
    mode: str = "text",
) -> dict[str, Any]:
    """Return current page state.

    Modes: text (default), screenshot, accessibility, full
    """
    missing = _require_playwright()
    if missing:
        return missing

    async def _run():
        page = await _get_page(session)
        result: dict[str, Any] = {"url": page.url, "title": await page.title()}

        if mode in ("text", "full"):
            text = await page.evaluate("""() => {
                const clone = document.body.cloneNode(true);
                clone.querySelectorAll('nav, footer, script, style, header').forEach(e => e.remove());
                return clone.innerText;
            }""")
            result["text"] = _truncate(text, 3000)

        if mode in ("screenshot", "full"):
            screenshot_bytes = await page.screenshot(type="png")
            result["screenshot"] = base64.b64encode(screenshot_bytes).decode()

        if mode in ("accessibility", "full"):
            snapshot = await page.accessibility.snapshot()
            result["accessibility"] = _truncate(json.dumps(snapshot, indent=2), 5000)

        logger.info("Browser: see mode=%s url=%s", mode, page.url)
        return result

    return _run_async(_run())


@mcp.tool()
def browser_click(
    target: str,
    session: str = "default",
    double: bool = False,
    right: bool = False,
    verify: bool = False,
) -> dict[str, Any]:
    """Click element. Finding order: accessibility → CSS → coordinates."""
    missing = _require_playwright()
    if missing:
        return missing

    async def _run():
        page = await _get_page(session)
        await _human_pause(pre=True, post=False)

        el_info = await _find_element(page, target)
        button = "right" if right else "left"
        click_count = 2 if double else 1

        try:
            if el_info["type"] == "coords":
                await page.mouse.click(
                    el_info["x"], el_info["y"],
                    button=button, click_count=click_count,
                )
            else:
                locator = el_info["locator"]
                if right:
                    await locator.click(button="right")
                elif double:
                    await locator.dblclick()
                else:
                    await locator.click()

            # Wait for network to settle (max 3s)
            try:
                await page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass

            await _human_pause(pre=False, post=True)

            result: dict[str, Any] = {"success": True, "target": target}
            if verify:
                screenshot_bytes = await page.screenshot(type="png")
                result["screenshot"] = base64.b64encode(screenshot_bytes).decode()

            logger.info("Browser: click target=%s double=%s right=%s", target, double, right)
            return result

        except Exception as e:
            logger.error("Browser: click failed target=%s — %s", target, e)
            return {"success": False, "error": str(e), "target": target}

    return _run_async(_run())


@mcp.tool()
def browser_type(
    field: str,
    text: str,
    session: str = "default",
    clear_first: bool = True,
    press_enter: bool = False,
    tab_after: bool = False,
) -> dict[str, Any]:
    """Type into field with human-like character delays."""
    missing = _require_playwright()
    if missing:
        return missing

    async def _run():
        page = await _get_page(session)
        await _human_pause(pre=True, post=False)

        el_info = await _find_element(page, field)

        try:
            if el_info["type"] == "locator":
                locator = el_info["locator"]
                if clear_first:
                    await locator.clear()
                # Type with realistic delay: 20-50ms per char ±30%
                base_delay = random.uniform(20, 50)
                for char in text:
                    await locator.type(char, delay=base_delay * random.uniform(0.7, 1.3))

            if press_enter:
                await page.keyboard.press("Enter")
            if tab_after:
                await page.keyboard.press("Tab")

            await _human_pause(pre=False, post=True)
            logger.info("Browser: typed %d chars into '%s'", len(text), field)
            return {"success": True, "field": field, "chars_typed": len(text)}

        except Exception as e:
            logger.error("Browser: type failed field=%s — %s", field, e)
            return {"success": False, "error": str(e), "field": field}

    return _run_async(_run())


@mcp.tool()
def browser_form_fill(
    fields: dict,
    submit: bool = False,
    session: str = "default",
) -> dict[str, Any]:
    """Fill multiple form fields.

    fields: {"field_description": value, ...}
    bool value → checkbox, str → text, list → dropdown
    """
    missing = _require_playwright()
    if missing:
        return missing

    async def _run():
        page = await _get_page(session)
        filled: list[str] = []
        errors: list[dict[str, Any]] = []

        for field_desc, value in fields.items():
            try:
                el_info = await _find_element(page, field_desc)
                locator = el_info["locator"]

                if isinstance(value, bool):
                    if value:
                        await locator.check()
                    else:
                        await locator.uncheck()
                elif isinstance(value, list):
                    await locator.select_option(value)
                else:
                    await locator.clear()
                    await locator.fill(str(value))

                filled.append(field_desc)
                await asyncio.sleep(random.uniform(0.1, 0.4))

            except Exception as e:
                errors.append({"field": field_desc, "error": str(e)})

        result_screenshot = None
        if submit:
            try:
                submit_btn = page.get_by_role(
                    "button", name=re.compile(r"submit|send|save", re.I)
                ).first
                await submit_btn.click()
                await page.wait_for_load_state("networkidle", timeout=5000)
                screenshot_bytes = await page.screenshot(type="png")
                result_screenshot = base64.b64encode(screenshot_bytes).decode()
            except Exception as e:
                errors.append({"field": "submit", "error": str(e)})

        logger.info("Browser: form_fill filled=%d errors=%d", len(filled), len(errors))
        return {
            "filled": filled,
            "errors": errors,
            "screenshot": result_screenshot,
        }

    return _run_async(_run())


@mcp.tool()
def browser_extract(
    what: str,
    selector: str = "",
    session: str = "default",
) -> dict[str, Any]:
    """Extract structured data from page.

    what: text, links, tables, forms, images, metadata, screenshot
    """
    missing = _require_playwright()
    if missing:
        return missing

    async def _run():
        page = await _get_page(session)

        if what == "text":
            text = await page.inner_text("body")
            return {"text": _truncate(text, 5000)}

        if what == "links":
            links = await page.evaluate("""() =>
                Array.from(document.querySelectorAll('a')).map(a => ({
                    text: a.innerText.trim(),
                    href: a.href
                })).filter(l => l.href && l.text)
            """)
            return {"links": links[:100]}

        if what == "tables":
            tables = await page.evaluate("""() =>
                Array.from(document.querySelectorAll('table')).map(t =>
                    Array.from(t.querySelectorAll('tr')).map(r =>
                        Array.from(r.querySelectorAll('td, th')).map(c => c.innerText.trim())
                    )
                )
            """)
            return {"tables": tables}

        if what == "forms":
            forms = await page.evaluate("""() =>
                Array.from(document.querySelectorAll('input, select, textarea')).map(el => ({
                    name: el.name || el.id || el.placeholder || '',
                    type: el.type || el.tagName.toLowerCase(),
                    value: el.value || '',
                    required: el.required,
                }))
            """)
            return {"forms": forms}

        if what == "images":
            imgs = await page.evaluate("""() =>
                Array.from(document.querySelectorAll('img')).map(i => ({
                    src: i.src, alt: i.alt
                }))
            """)
            return {"images": imgs[:50]}

        if what == "metadata":
            meta = await page.evaluate("""() => ({
                title: document.title,
                description: document.querySelector('meta[name=description]')?.content || '',
                canonical: document.querySelector('link[rel=canonical]')?.href || '',
                ogTitle: document.querySelector('meta[property="og:title"]')?.content || '',
                ogImage: document.querySelector('meta[property="og:image"]')?.content || '',
            })""")
            return meta

        if what == "screenshot":
            screenshot_bytes = await page.screenshot(type="png")
            return {"screenshot": base64.b64encode(screenshot_bytes).decode()}

        return {"error": f"Unknown extract type: {what}"}

    return _run_async(_run())


@mcp.tool()
def browser_wait(
    condition: str,
    timeout_seconds: int = 30,
    session: str = "default",
) -> dict[str, Any]:
    """Wait for condition to be true.

    Conditions: load, networkidle, url_changes, element:{desc}, no_element:{desc}, text:{text}
    """
    missing = _require_playwright()
    if missing:
        return missing

    async def _run():
        page = await _get_page(session)
        timeout_ms = timeout_seconds * 1000

        try:
            if condition in ("load", "networkidle"):
                await page.wait_for_load_state(condition, timeout=timeout_ms)

            elif condition == "url_changes":
                original_url = page.url
                await page.wait_for_function(
                    f"window.location.href !== '{original_url}'",
                    timeout=timeout_ms,
                )

            elif condition.startswith("element:"):
                desc = condition[8:]
                el_info = await _find_element(page, desc)
                if el_info["type"] == "locator":
                    await el_info["locator"].wait_for(timeout=timeout_ms)

            elif condition.startswith("no_element:"):
                desc = condition[11:]
                el_info = await _find_element(page, desc)
                if el_info["type"] == "locator":
                    await el_info["locator"].wait_for(state="hidden", timeout=timeout_ms)

            elif condition.startswith("text:"):
                text = condition[5:]
                # Escape single quotes in the text for JS
                escaped = text.replace("'", "\\'")
                await page.wait_for_function(
                    f"document.body.innerText.includes('{escaped}')",
                    timeout=timeout_ms,
                )

            screenshot_bytes = await page.screenshot(type="png")
            logger.info("Browser: wait condition=%s fulfilled", condition)
            return {
                "success": True,
                "condition": condition,
                "url": page.url,
                "screenshot": base64.b64encode(screenshot_bytes).decode(),
            }

        except Exception as e:
            logger.warning("Browser: wait failed condition=%s — %s", condition, e)
            return {"success": False, "error": str(e), "condition": condition}

    return _run_async(_run())


@mcp.tool()
def browser_scroll(
    direction: str,
    amount: int = 500,
    session: str = "default",
) -> dict[str, Any]:
    """Scroll the page. Directions: up, down, left, right, top, bottom."""
    missing = _require_playwright()
    if missing:
        return missing

    async def _run():
        page = await _get_page(session)
        if direction == "top":
            await page.evaluate("window.scrollTo(0, 0)")
        elif direction == "bottom":
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        elif direction == "down":
            await page.evaluate(f"window.scrollBy(0, {amount})")
        elif direction == "up":
            await page.evaluate(f"window.scrollBy(0, -{amount})")
        elif direction in ("left", "right"):
            sign = 1 if direction == "right" else -1
            await page.evaluate(f"window.scrollBy({sign * amount}, 0)")

        pos = await page.evaluate("({x: window.scrollX, y: window.scrollY})")
        return {"scroll_position": pos}

    return _run_async(_run())


@mcp.tool()
def browser_key(
    key: str,
    session: str = "default",
) -> dict[str, Any]:
    """Press keyboard key or hotkey combo like Control+a."""
    missing = _require_playwright()
    if missing:
        return missing

    async def _run():
        page = await _get_page(session)
        await page.keyboard.press(key)
        logger.info("Browser: key press '%s'", key)
        return {"success": True, "key": key}

    return _run_async(_run())


@mcp.tool()
def browser_script(
    script: str,
    session: str = "default",
) -> dict[str, Any]:
    """Execute JavaScript in page context."""
    missing = _require_playwright()
    if missing:
        return missing

    async def _run():
        page = await _get_page(session)
        try:
            result = await page.evaluate(script)
            logger.info("Browser: script executed (%d chars)", len(script))
            return {"success": True, "result": _truncate(json.dumps(result, default=str), 5000)}
        except Exception as e:
            logger.error("Browser: script failed — %s", e)
            return {"success": False, "error": str(e)}

    return _run_async(_run())


@mcp.tool()
def browser_download(
    url: str = "",
    click_target: str = "",
    save_path: str = "",
    session: str = "default",
) -> dict[str, Any]:
    """Download a file via direct URL or by clicking a download link."""
    missing = _require_playwright()
    if missing:
        return missing

    resolved_path = os.path.expanduser(save_path) if save_path else os.path.expanduser("~/Downloads/")
    os.makedirs(
        resolved_path if os.path.isdir(resolved_path) else os.path.dirname(resolved_path) or ".",
        exist_ok=True,
    )

    async def _run():
        page = await _get_page(session)
        if url:
            # Direct download via httpx
            try:
                import httpx
            except ImportError:
                return {"error": "httpx is required for direct URL downloads"}
            r = httpx.get(url, follow_redirects=True, timeout=60)
            fname = url.split("/")[-1].split("?")[0] or "download"
            fpath = os.path.join(resolved_path, fname) if os.path.isdir(resolved_path) else resolved_path
            with open(fpath, "wb") as f:
                f.write(r.content)
            logger.info("Browser: downloaded %s → %s (%d bytes)", url, fpath, len(r.content))
            return {"path": fpath, "size_bytes": len(r.content)}

        if click_target:
            async with page.expect_download() as download_info:
                el_info = await _find_element(page, click_target)
                if el_info["type"] == "locator":
                    await el_info["locator"].click()
                else:
                    await page.mouse.click(el_info["x"], el_info["y"])
            download = await download_info.value
            fname = download.suggested_filename or "download"
            fpath = os.path.join(resolved_path, fname) if os.path.isdir(resolved_path) else resolved_path
            await download.save_as(fpath)
            logger.info("Browser: downloaded via click '%s' → %s", click_target, fpath)
            return {"path": fpath, "size_bytes": os.path.getsize(fpath)}

        return {"error": "Either url or click_target required"}

    return _run_async(_run())


@mcp.tool()
def browser_pdf(
    path: str = "",
    full_page: bool = True,
    session: str = "default",
) -> dict[str, Any]:
    """Save current page as PDF."""
    missing = _require_playwright()
    if missing:
        return missing

    resolved_path = os.path.expanduser(path) if path else os.path.expanduser("~/Downloads/page.pdf")
    os.makedirs(os.path.dirname(resolved_path) or ".", exist_ok=True)

    async def _run():
        page = await _get_page(session)
        await page.pdf(path=resolved_path, print_background=True)
        size = os.path.getsize(resolved_path)
        logger.info("Browser: saved PDF to %s (%d bytes)", resolved_path, size)
        return {"path": resolved_path, "size_bytes": size}

    return _run_async(_run())


@mcp.tool()
def browser_session_save(
    session: str = "default",
    name: str = "",
) -> dict[str, Any]:
    """Save session cookies to disk."""
    missing = _require_playwright()
    if missing:
        return missing

    save_name = name or session

    async def _run():
        context = await _get_context(session)
        cookies = await context.cookies()
        fpath = os.path.join(SESSIONS_DIR, f"{save_name}.json")
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2)
        logger.info("Browser: saved session '%s' → %s (%d cookies)", save_name, fpath, len(cookies))
        return {"saved": True, "path": fpath, "cookies": len(cookies)}

    return _run_async(_run())


@mcp.tool()
def browser_multi_tab(
    urls: list,
    extract: str = "text",
    max_parallel: int = 5,
) -> dict[str, Any]:
    """Open multiple URLs in parallel, return extracted content."""
    missing = _require_playwright()
    if missing:
        return missing

    async def _run_one(target_url: str, session_name: str) -> dict[str, Any]:
        open_result = browser_open(target_url, session=session_name)
        if not open_result.get("success"):
            return {"url": target_url, "error": open_result.get("error")}
        extract_result = browser_extract(extract, session=session_name)
        return {"url": target_url, "title": open_result.get("title"), "content": extract_result}

    async def _run_all():
        semaphore = asyncio.Semaphore(min(max_parallel, 10))

        async def _bounded(target_url, i):
            async with semaphore:
                return await asyncio.to_thread(_run_one_sync, target_url, f"multi_tab_{i}")

        def _run_one_sync(target_url, session_name):
            return _run_one(target_url, session_name)

        # Run sequentially for safety (each browser_open call manages its own async)
        results = []
        for i, target_url in enumerate(urls):
            result = _run_one_sync(target_url, f"multi_tab_{i}")
            results.append(result)

        return results

    # For multi-tab, run synchronously to avoid nested event loop issues
    results = []
    for i, target_url in enumerate(urls):
        open_result = browser_open(target_url, session=f"multi_tab_{i}")
        if not open_result.get("success"):
            results.append({"url": target_url, "error": open_result.get("error")})
            continue
        extract_result = browser_extract(extract, session=f"multi_tab_{i}")
        results.append({"url": target_url, "title": open_result.get("title"), "content": extract_result})

    logger.info("Browser: multi_tab processed %d URLs", len(urls))
    return {"results": results}


# ══════════════════════════════════════════════════════════════════════════════
#  CLEANUP
# ══════════════════════════════════════════════════════════════════════════════


async def _cleanup():
    """Gracefully close all browser resources."""
    global _browser, _playwright_instance, _contexts, _pages
    for name, ctx in list(_contexts.items()):
        try:
            await ctx.close()
        except Exception:
            pass
    _contexts.clear()
    _pages.clear()
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None
    if _playwright_instance:
        try:
            await _playwright_instance.stop()
        except Exception:
            pass
        _playwright_instance = None
    logger.info("Browser: cleaned up all resources")


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════


def main() -> None:
    """Run the MCP server over stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
