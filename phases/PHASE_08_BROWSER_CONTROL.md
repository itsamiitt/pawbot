# PHASE 8 — BROWSER INTELLIGENCE MCP SERVER
> **Cross-reference:** [MASTER_REFERENCE.md](./MASTER_REFERENCE.md)  
> **Implementation Days:** Day 22 (accessibility mode), Day 23 (vision + anti-detection)  
> **Primary File:** `~/.nanobot/mcp-servers/browser/server.py` (NEW)  
> **Test File:** `~/nanobot/tests/test_browser_mcp.py`  
> **Config registration key:** `mcp_servers.browser`  
> **Session storage path:** `~/.nanobot/browser-sessions/`  
> **Dependencies:** `playwright>=1.40.0`

---

## BEFORE YOU START

```bash
mkdir -p ~/.nanobot/mcp-servers/browser
mkdir -p ~/.nanobot/browser-sessions
pip install playwright
playwright install chromium firefox webkit
```

Add to `~/.nanobot/config.json`:

```json
{
  "mcp_servers": {
    "browser": {
      "path": "~/.nanobot/mcp-servers/browser/server.py",
      "requires_confirmation": false,
      "enabled": true
    }
  },
  "tools": {
    "browser": {
      "headless": true,
      "stealth_mode": true
    }
  }
}
```

---

## ARCHITECTURE: TWO BROWSER MODES

**Mode A — Accessibility tree (default)**  
Uses `page.accessibility.snapshot()` for structured text representation.  
No screenshots needed for interaction. Preferred for: forms, buttons, navigation.

**Mode B — Vision mode (fallback)**  
Takes screenshot, describes for coordinate identification.  
Used when accessibility fails: canvas elements, custom widgets, dynamic UIs.

---

## SESSION MANAGEMENT

```python
#!/usr/bin/env python3
"""
Browser Intelligence MCP Server
Registered as: mcp_servers.browser in ~/.nanobot/config.json
Session cookies: ~/.nanobot/browser-sessions/{session_name}.json
"""
import asyncio
import base64
import json
import os
import time
import random
import logging
from pathlib import Path
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger("nanobot.mcp.browser")

SESSIONS_DIR = os.path.expanduser("~/.nanobot/browser-sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)

# Global state — one playwright instance shared across all tool calls
_playwright = None
_browser: Browser = None
_contexts: dict[str, BrowserContext] = {}  # session_name → context
_pages: dict[str, Page] = {}               # session_name → active page

# ── Anti-detection Configuration ─────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = {runtime: {}};
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
"""


async def _get_browser():
    """Initialize browser on first call, reuse after that."""
    global _playwright, _browser
    if _browser is None or not _browser.is_connected():
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(headless=True)
    return _browser


async def _get_context(session: str = "default") -> BrowserContext:
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
            with open(cookie_file) as f:
                cookies = json.load(f)
            await context.add_cookies(cookies)
            logger.info(f"Browser: restored session cookies for '{session}'")

        _contexts[session] = context

    return _contexts[session]


async def _get_page(session: str = "default") -> Page:
    """Get or create page for session."""
    global _pages
    if session not in _pages or _pages[session].is_closed():
        context = await _get_context(session)
        _pages[session] = await context.new_page()
    return _pages[session]


# ── Human-like Behavior Middleware ────────────────────────────────────────────

async def _human_pause(pre: bool = True, post: bool = True):
    """Add human-like delays around actions."""
    if pre:
        await asyncio.sleep(random.uniform(0.2, 0.8))
    if post:
        await asyncio.sleep(random.uniform(0.1, 0.3))


async def _find_element(page: Page, target: str):
    """
    Find page element using accessibility tree first, then CSS, then fail.
    Never uses vision model internally — that's handled by caller.
    target formats:
      "Submit button"     → accessibility text search
      "aria:submit"       → ARIA label
      "css:#submit-btn"   → CSS selector
      "x:450,y:300"       → coordinates (return tuple)
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

    # Accessibility tree text search
    el = page.get_by_text(target, exact=False).first
    return {"type": "locator", "locator": el}
```

---

## TOOL IMPLEMENTATIONS

### browser_open

```python
def browser_open(url: str, session: str = "default",
                 wait_for: str = "networkidle",
                 restore_session: bool = True) -> dict:
    """Open URL in named browser session."""
    async def _run():
        page = await _get_page(session)
        try:
            response = await page.goto(url, wait_until=wait_for, timeout=30000)
            title = await page.title()
            final_url = page.url
            # Brief text preview
            text = await page.inner_text("body")
            preview = text[:300].strip()
            return {
                "success": True,
                "title": title,
                "url": final_url,
                "status": response.status if response else None,
                "preview": preview,
            }
        except Exception as e:
            return {"success": False, "error": str(e), "url": url}

    return asyncio.run(_run())
```

### browser_see

```python
def browser_see(session: str = "default", mode: str = "text") -> dict:
    """
    Returns current page state.
    Modes: text (default), screenshot, accessibility, full
    """
    async def _run():
        page = await _get_page(session)
        result = {"url": page.url, "title": await page.title()}

        if mode in ("text", "full"):
            # Extract clean body text
            text = await page.evaluate("""() => {
                const clone = document.body.cloneNode(true);
                clone.querySelectorAll('nav, footer, script, style, header').forEach(e => e.remove());
                return clone.innerText;
            }""")
            result["text"] = text[:3000]

        if mode in ("screenshot", "full"):
            screenshot_bytes = await page.screenshot(type="png")
            result["screenshot"] = base64.b64encode(screenshot_bytes).decode()

        if mode in ("accessibility", "full"):
            snapshot = await page.accessibility.snapshot()
            result["accessibility"] = json.dumps(snapshot, indent=2)[:5000]

        return result

    return asyncio.run(_run())
```

### browser_click

```python
def browser_click(target: str, session: str = "default",
                  double: bool = False, right: bool = False,
                  verify: bool = False) -> dict:
    """
    Click element. Finding order: accessibility → CSS → vision (if needed).
    """
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
                    button=button, click_count=click_count
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

            result = {"success": True, "target": target}
            if verify:
                screenshot_bytes = await page.screenshot(type="png")
                result["screenshot"] = base64.b64encode(screenshot_bytes).decode()
            return result

        except Exception as e:
            return {"success": False, "error": str(e), "target": target}

    return asyncio.run(_run())
```

### browser_type

```python
def browser_type(field: str, text: str, session: str = "default",
                 clear_first: bool = True, press_enter: bool = False,
                 tab_after: bool = False) -> dict:
    """Type into field with human-like character delays."""
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
            return {"success": True, "field": field, "chars_typed": len(text)}

        except Exception as e:
            return {"success": False, "error": str(e), "field": field}

    return asyncio.run(_run())
```

### browser_form_fill

```python
def browser_form_fill(fields: dict, submit: bool = False,
                      session: str = "default") -> dict:
    """
    Fill multiple form fields.
    fields: {"field_description": value, ...}
    bool value → checkbox, str → text, list → dropdown
    """
    async def _run():
        page = await _get_page(session)
        filled = []
        errors = []

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
                submit_btn = page.get_by_role("button", name=re.compile("submit|send|save", re.I)).first
                await submit_btn.click()
                await page.wait_for_load_state("networkidle", timeout=5000)
                screenshot_bytes = await page.screenshot(type="png")
                result_screenshot = base64.b64encode(screenshot_bytes).decode()
            except Exception as e:
                errors.append({"field": "submit", "error": str(e)})

        return {
            "filled": filled,
            "errors": errors,
            "screenshot": result_screenshot,
        }

    import re
    return asyncio.run(_run())
```

### browser_extract

```python
def browser_extract(what: str, selector: str = "",
                    session: str = "default") -> dict:
    """
    Extract structured data from page.
    what: text, links, tables, forms, images, metadata, screenshot
    """
    async def _run():
        page = await _get_page(session)
        target = page.locator(selector) if selector else page

        if what == "text":
            text = await page.inner_text("body")
            return {"text": text[:5000]}

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

    return asyncio.run(_run())
```

### browser_wait

```python
def browser_wait(condition: str, timeout_seconds: int = 30,
                 session: str = "default") -> dict:
    """
    Wait for condition to be true.
    Conditions: load, networkidle, url_changes, element:{desc}, no_element:{desc}, text:{text}
    """
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
                    timeout=timeout_ms
                )

            elif condition.startswith("element:"):
                desc = condition[8:]
                el_info = await _find_element(page, desc)
                await el_info["locator"].wait_for(timeout=timeout_ms)

            elif condition.startswith("no_element:"):
                desc = condition[11:]
                el_info = await _find_element(page, desc)
                await el_info["locator"].wait_for(state="hidden", timeout=timeout_ms)

            elif condition.startswith("text:"):
                text = condition[5:]
                await page.wait_for_function(
                    f"document.body.innerText.includes('{text}')",
                    timeout=timeout_ms
                )

            screenshot_bytes = await page.screenshot(type="png")
            return {
                "success": True, "condition": condition,
                "url": page.url,
                "screenshot": base64.b64encode(screenshot_bytes).decode(),
            }

        except Exception as e:
            return {"success": False, "error": str(e), "condition": condition}

    return asyncio.run(_run())
```

### browser_scroll, browser_key, browser_script

```python
def browser_scroll(direction: str, amount: int = 500,
                   session: str = "default") -> dict:
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
    return asyncio.run(_run())


def browser_key(key: str, session: str = "default") -> dict:
    """Press keyboard key or hotkey combo like Control+a."""
    async def _run():
        page = await _get_page(session)
        await page.keyboard.press(key)
        return {"success": True, "key": key}
    return asyncio.run(_run())


def browser_script(script: str, session: str = "default") -> dict:
    """Execute JavaScript in page context."""
    async def _run():
        page = await _get_page(session)
        try:
            result = await page.evaluate(script)
            return {"success": True, "result": json.dumps(result, default=str)[:5000]}
        except Exception as e:
            return {"success": False, "error": str(e)}
    return asyncio.run(_run())
```

### browser_download and browser_pdf

```python
def browser_download(url: str = "", click_target: str = "",
                     save_path: str = "", session: str = "default") -> dict:
    save_path = os.path.expanduser(save_path) if save_path else os.path.expanduser("~/Downloads/")
    os.makedirs(save_path if os.path.isdir(save_path) else os.path.dirname(save_path), exist_ok=True)

    async def _run():
        page = await _get_page(session)
        if url:
            # Direct download
            import httpx
            r = httpx.get(url, follow_redirects=True, timeout=60)
            fname = url.split("/")[-1].split("?")[0] or "download"
            fpath = os.path.join(save_path, fname) if os.path.isdir(save_path) else save_path
            with open(fpath, "wb") as f:
                f.write(r.content)
            return {"path": fpath, "size_bytes": len(r.content)}

        if click_target:
            async with page.expect_download() as download_info:
                await browser_click(click_target, session=session)
            download = await download_info.value
            fname = download.suggested_filename or "download"
            fpath = os.path.join(save_path, fname) if os.path.isdir(save_path) else save_path
            await download.save_as(fpath)
            return {"path": fpath, "size_bytes": os.path.getsize(fpath)}

        return {"error": "Either url or click_target required"}

    return asyncio.run(_run())


def browser_pdf(path: str = "", full_page: bool = True,
                session: str = "default") -> dict:
    path = os.path.expanduser(path) if path else os.path.expanduser("~/Downloads/page.pdf")
    async def _run():
        page = await _get_page(session)
        await page.pdf(path=path, print_background=True, full_page=full_page)
        return {"path": path, "size_bytes": os.path.getsize(path)}
    return asyncio.run(_run())
```

### browser_session_save and browser_multi_tab

```python
def browser_session_save(session: str = "default", name: str = "") -> dict:
    """Save session cookies to disk."""
    name = name or session
    async def _run():
        context = await _get_context(session)
        cookies = await context.cookies()
        fpath = os.path.join(SESSIONS_DIR, f"{name}.json")
        with open(fpath, "w") as f:
            json.dump(cookies, f, indent=2)
        return {"saved": True, "path": fpath, "cookies": len(cookies)}
    return asyncio.run(_run())


def browser_multi_tab(urls: list, extract: str = "text",
                      max_parallel: int = 5) -> dict:
    """Open multiple URLs in parallel, return extracted content."""
    async def _run_one(url: str, session_name: str) -> dict:
        open_result = browser_open(url, session=session_name)
        if not open_result["success"]:
            return {"url": url, "error": open_result.get("error")}
        extract_result = browser_extract(extract, session=session_name)
        return {"url": url, "title": open_result.get("title"), "content": extract_result}

    async def _run_all():
        semaphore = asyncio.Semaphore(min(max_parallel, 10))
        async def _bounded(url, i):
            async with semaphore:
                return await _run_one(url, f"multi_tab_{i}")
        tasks = [_bounded(url, i) for i, url in enumerate(urls)]
        return await asyncio.gather(*tasks)

    return {"results": asyncio.run(_run_all())}
```

---

## TEST REQUIREMENTS

**File:** `~/nanobot/tests/test_browser_mcp.py`

```python
class TestBrowserMCP:
    # Server lifecycle
    def test_server_starts_without_error()
    def test_list_tools_returns_all_tools()
    def test_handles_invalid_args_gracefully()

    # browser_open
    def test_opens_url_returns_title_and_url()
    def test_error_status_code_included_in_result()
    def test_session_cookies_restored_on_creation()

    # browser_see
    def test_text_mode_removes_nav_and_footer()
    def test_screenshot_mode_returns_base64()
    def test_accessibility_mode_returns_json()

    # browser_click
    def test_click_by_text()
    def test_click_by_css_selector()
    def test_click_by_coordinates()
    def test_pre_click_delay_applied()

    # browser_type
    def test_type_clears_field_first()
    def test_type_delay_per_character()

    # browser_extract
    def test_extract_links_returns_list()
    def test_extract_tables_returns_nested_list()
    def test_extract_metadata_returns_title()

    # browser_wait
    def test_wait_for_text_success()
    def test_wait_timeout_returns_error_not_exception()

    # Anti-detection
    def test_context_has_non_webdriver_navigator()
    def test_random_viewport_size()

    # Multi-tab
    def test_multi_tab_respects_max_parallel()
    def test_multi_tab_returns_results_in_order()

    # Dependency failure
    def test_error_when_playwright_not_installed()
```

---

## CROSS-REFERENCES

- **Phase 2** (loop.py): browser tool failures trigger `_record_failure()` → self-correction
- **Phase 9** (app_control): does NOT share any code with this server — separate files
- **Phase 14** (security): irreversible browser actions (form submits to payment pages) may be gated
- Session storage path `~/.nanobot/browser-sessions/` — canonical from **MASTER_REFERENCE.md**
- Config key `tools.browser.headless` and `tools.browser.stealth_mode` read from `~/.nanobot/config.json`

All canonical paths in [MASTER_REFERENCE.md](./MASTER_REFERENCE.md).
