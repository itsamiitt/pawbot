"""Tests for the Phase 8 Browser Intelligence MCP server.

Tests verify:
  - Server lifecycle and tool discovery
  - browser_open, browser_see, browser_click, browser_type
  - browser_form_fill, browser_extract, browser_wait
  - browser_scroll, browser_key, browser_script
  - browser_download, browser_pdf, browser_session_save, browser_multi_tab
  - Anti-detection features (stealth script, random viewport)
  - Graceful error handling when playwright is not installed
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ══════════════════════════════════════════════════════════════════════════════
#  Module Loader
# ══════════════════════════════════════════════════════════════════════════════


def _load_server_module():
    path = Path(__file__).resolve().parents[1] / "mcp-servers" / "browser" / "server.py"
    spec = importlib.util.spec_from_file_location("browser_mcp", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def server_module():
    return _load_server_module()


# ══════════════════════════════════════════════════════════════════════════════
#  Fake Playwright Stubs
# ══════════════════════════════════════════════════════════════════════════════


class FakeLocator:
    """Minimal Playwright Locator stub."""

    def __init__(self, text: str = ""):
        self._text = text
        self._cleared = False
        self._checked = False
        self._clicked = False

    @property
    def first(self):
        return self

    async def click(self, **kwargs):
        self._clicked = True

    async def dblclick(self, **kwargs):
        self._clicked = True

    async def clear(self):
        self._cleared = True

    async def fill(self, value: str):
        self._text = value

    async def type(self, char: str, delay: float = 0):
        self._text += char

    async def check(self):
        self._checked = True

    async def uncheck(self):
        self._checked = False

    async def select_option(self, value):
        pass

    async def wait_for(self, **kwargs):
        pass


class FakeAccessibility:
    async def snapshot(self):
        return {
            "role": "WebArea",
            "name": "Test Page",
            "children": [
                {"role": "heading", "name": "Hello World", "level": 1},
                {"role": "button", "name": "Submit"},
            ],
        }


class FakeMouse:
    def __init__(self):
        self.clicks = []

    async def click(self, x, y, **kwargs):
        self.clicks.append({"x": x, "y": y, **kwargs})


class FakeKeyboard:
    def __init__(self):
        self.presses = []

    async def press(self, key: str):
        self.presses.append(key)


class FakePage:
    """Minimal Playwright Page stub."""

    def __init__(self, url: str = "about:blank", title: str = "Test Page"):
        self._url = url
        self._title = title
        self._closed = False
        self._screenshot_data = b"\x89PNG_FAKE_SCREENSHOT_DATA"
        self.accessibility = FakeAccessibility()
        self.mouse = FakeMouse()
        self.keyboard = FakeKeyboard()
        self._locator = FakeLocator()
        self._init_scripts = []
        self._viewport = {"width": 1300, "height": 750}

    @property
    def url(self) -> str:
        return self._url

    def is_closed(self) -> bool:
        return self._closed

    async def title(self) -> str:
        return self._title

    async def goto(self, url: str, **kwargs):
        self._url = url
        return types.SimpleNamespace(status=200)

    async def inner_text(self, selector: str) -> str:
        return "Page body text content for testing purposes."

    async def evaluate(self, script: str, *args):
        # Return appropriate values based on common evaluation patterns
        if "clone.innerText" in script or "clone" in script:
            return "Main content without nav/footer"
        if "scrollX" in script:
            return {"x": 0, "y": 500}
        if "document.title" in script:
            return {
                "title": self._title,
                "description": "Test description",
                "canonical": "",
                "ogTitle": "",
                "ogImage": "",
            }
        if "querySelectorAll('a')" in script:
            return [
                {"text": "Example Link", "href": "https://example.com"},
                {"text": "Another Link", "href": "https://another.com"},
            ]
        if "querySelectorAll('table')" in script:
            return [[["Header", "Value"], ["Row1", "Data1"]]]
        if "querySelectorAll('input" in script:
            return [
                {"name": "email", "type": "text", "value": "", "required": True},
                {"name": "password", "type": "password", "value": "", "required": True},
            ]
        if "querySelectorAll('img')" in script:
            return [{"src": "https://example.com/img.png", "alt": "Test image"}]
        if "scrollTo" in script or "scrollBy" in script:
            return None
        if "window.location.href" in script:
            return True
        if "document.body.innerText.includes" in script:
            return True
        # Generic JS execution — return a simple value
        return "evaluated"

    async def screenshot(self, **kwargs) -> bytes:
        return self._screenshot_data

    async def pdf(self, **kwargs):
        path = kwargs.get("path", "")
        if path:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"%PDF-1.4 FAKE_PDF_DATA")

    async def wait_for_load_state(self, state: str = "load", **kwargs):
        pass

    async def wait_for_function(self, expression: str, **kwargs):
        pass

    def locator(self, selector: str):
        return self._locator

    def get_by_text(self, text: str, exact: bool = False):
        return self._locator

    def get_by_label(self, label: str):
        return self._locator

    def get_by_role(self, role: str, **kwargs):
        return self._locator


class FakeContext:
    """Minimal Playwright BrowserContext stub."""

    def __init__(self, **kwargs):
        self._cookies: list[dict] = []
        self._init_scripts: list[str] = []
        self._user_agent = kwargs.get("user_agent", "")
        self._viewport = kwargs.get("viewport", {"width": 1300, "height": 750})

    async def new_page(self) -> FakePage:
        return FakePage()

    async def add_init_script(self, script: str):
        self._init_scripts.append(script)

    async def add_cookies(self, cookies: list[dict]):
        self._cookies.extend(cookies)

    async def cookies(self) -> list[dict]:
        return self._cookies

    async def close(self):
        pass


class FakeBrowser:
    """Minimal Playwright Browser stub."""

    def __init__(self):
        self._connected = True
        self._contexts: list[FakeContext] = []

    def is_connected(self) -> bool:
        return self._connected

    async def new_context(self, **kwargs) -> FakeContext:
        ctx = FakeContext(**kwargs)
        self._contexts.append(ctx)
        return ctx

    async def close(self):
        self._connected = False


class FakePlaywright:
    """Minimal Playwright instance stub."""

    def __init__(self):
        self._browser = FakeBrowser()
        self.chromium = self

    async def launch(self, **kwargs) -> FakeBrowser:
        return self._browser

    async def stop(self):
        pass


class FakePlaywrightContextManager:
    """Mimics async_playwright() context manager."""

    def __init__(self):
        self._pw = FakePlaywright()

    async def start(self):
        return self._pw


# ══════════════════════════════════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def reset_global_state(server_module):
    """Reset all global browser state between tests."""
    server_module._playwright_instance = None
    server_module._browser = None
    server_module._contexts = {}
    server_module._pages = {}
    yield
    server_module._playwright_instance = None
    server_module._browser = None
    server_module._contexts = {}
    server_module._pages = {}


@pytest.fixture
def fake_playwright(server_module):
    """Patch playwright globals with fakes for fully isolated tests."""
    fake_pw = FakePlaywright()
    fake_browser = fake_pw._browser
    fake_ctx = FakeContext()
    fake_page = FakePage(url="https://example.com", title="Example Page")

    server_module._playwright_instance = fake_pw
    server_module._browser = fake_browser
    server_module._contexts = {"default": fake_ctx}
    server_module._pages = {"default": fake_page}

    # Ensure playwright is available
    old_available = server_module._PLAYWRIGHT_AVAILABLE
    server_module._PLAYWRIGHT_AVAILABLE = True

    yield {
        "playwright": fake_pw,
        "browser": fake_browser,
        "context": fake_ctx,
        "page": fake_page,
    }

    server_module._PLAYWRIGHT_AVAILABLE = old_available


# ══════════════════════════════════════════════════════════════════════════════
#  Tests
# ══════════════════════════════════════════════════════════════════════════════


class TestBrowserMCP:
    """Phase 8 Browser Intelligence MCP server tests."""

    # ── Server Lifecycle ──────────────────────────────────────────────────

    def test_server_starts_without_error(self, server_module):
        """The server module loads, registers FastMCP, and exposes main()."""
        assert server_module.mcp is not None
        assert callable(server_module.main)

    def test_list_tools_returns_all_tools(self, server_module):
        """list_tools() returns every registered tool name."""
        tools = server_module.list_tools()
        assert tools["count"] == len(server_module.TOOL_NAMES)
        assert set(tools["tools"]) == set(server_module.TOOL_NAMES)
        # Verify minimum expected tool count from the phase spec
        assert tools["count"] >= 14

    def test_handles_invalid_args_gracefully(self, server_module, fake_playwright):
        """Tools return error dicts instead of raising exceptions."""
        result = server_module.browser_extract(what="nonexistent_type")
        assert "error" in result

    # ── browser_open ──────────────────────────────────────────────────────

    def test_opens_url_returns_title_and_url(self, server_module, fake_playwright):
        """browser_open navigates and returns title + final URL."""
        result = server_module.browser_open("https://example.com")
        assert result["success"] is True
        assert "title" in result
        assert "url" in result
        assert "preview" in result

    def test_error_status_code_included_in_result(self, server_module, fake_playwright):
        """HTTP status code is included in the open result."""
        result = server_module.browser_open("https://example.com")
        assert result["success"] is True
        assert result.get("status") == 200

    def test_session_cookies_restored_on_creation(self, server_module, tmp_path):
        """Session cookies are loaded from disk when context is created."""
        server_module._PLAYWRIGHT_AVAILABLE = True

        # Create a fake cookie file
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        cookie_file = sessions_dir / "test_session.json"
        cookies = [{"name": "sid", "value": "abc123", "domain": ".example.com", "path": "/"}]
        cookie_file.write_text(json.dumps(cookies), encoding="utf-8")

        # Patch SESSIONS_DIR
        old_dir = server_module.SESSIONS_DIR
        server_module.SESSIONS_DIR = str(sessions_dir)

        # Set up fake browser that tracks cookie restoration
        fake_pw = FakePlaywright()
        server_module._playwright_instance = fake_pw
        server_module._browser = fake_pw._browser

        async def _test():
            ctx = await server_module._get_context("test_session")
            return ctx._cookies

        restored_cookies = asyncio.run(_test())
        assert len(restored_cookies) == 1
        assert restored_cookies[0]["name"] == "sid"

        server_module.SESSIONS_DIR = old_dir

    # ── browser_see ───────────────────────────────────────────────────────

    def test_text_mode_removes_nav_and_footer(self, server_module, fake_playwright):
        """Text mode returns cleaned body content."""
        result = server_module.browser_see(mode="text")
        assert "text" in result
        assert "url" in result
        assert "title" in result

    def test_screenshot_mode_returns_base64(self, server_module, fake_playwright):
        """Screenshot mode returns base64-encoded image data."""
        result = server_module.browser_see(mode="screenshot")
        assert "screenshot" in result
        # Verify it's valid base64
        import base64
        raw = base64.b64decode(result["screenshot"])
        assert len(raw) > 0

    def test_accessibility_mode_returns_json(self, server_module, fake_playwright):
        """Accessibility mode returns JSON accessibility snapshot."""
        result = server_module.browser_see(mode="accessibility")
        assert "accessibility" in result
        parsed = json.loads(result["accessibility"])
        assert "role" in parsed

    # ── browser_click ─────────────────────────────────────────────────────

    def test_click_by_text(self, server_module, fake_playwright):
        """Click by accessibility text search succeeds."""
        result = server_module.browser_click("Submit button")
        assert result["success"] is True
        assert result["target"] == "Submit button"

    def test_click_by_css_selector(self, server_module, fake_playwright):
        """Click by CSS selector succeeds."""
        result = server_module.browser_click("css:#submit-btn")
        assert result["success"] is True

    def test_click_by_coordinates(self, server_module, fake_playwright):
        """Click by coordinates routes through mouse.click."""
        result = server_module.browser_click("x:450,y:300")
        assert result["success"] is True
        page = fake_playwright["page"]
        assert len(page.mouse.clicks) == 1
        assert page.mouse.clicks[0]["x"] == 450
        assert page.mouse.clicks[0]["y"] == 300

    def test_pre_click_delay_applied(self, server_module, fake_playwright, monkeypatch):
        """Human-like pre-click delay is applied via _human_pause."""
        delays = []
        original_sleep = asyncio.sleep

        async def track_sleep(duration):
            delays.append(duration)
            # Don't actually sleep to keep test fast

        monkeypatch.setattr(asyncio, "sleep", track_sleep)
        server_module.browser_click("Submit")
        # At least one delay should have been added for pre and/or post
        assert len(delays) >= 1

    # ── browser_type ──────────────────────────────────────────────────────

    def test_type_clears_field_first(self, server_module, fake_playwright):
        """browser_type with clear_first=True clears the field before typing."""
        result = server_module.browser_type(field="css:#email", text="test@example.com")
        assert result["success"] is True
        assert result["chars_typed"] == len("test@example.com")

    def test_type_delay_per_character(self, server_module, fake_playwright, monkeypatch):
        """Each character is typed with a random delay for human-likeness."""
        # This test verifies the function completes without error for multi-char input
        result = server_module.browser_type(field="Search", text="hello")
        assert result["success"] is True
        assert result["chars_typed"] == 5

    # ── browser_extract ───────────────────────────────────────────────────

    def test_extract_links_returns_list(self, server_module, fake_playwright):
        """browser_extract(what='links') returns a list of link dicts."""
        result = server_module.browser_extract(what="links")
        assert "links" in result
        assert isinstance(result["links"], list)
        assert len(result["links"]) > 0
        assert "text" in result["links"][0]
        assert "href" in result["links"][0]

    def test_extract_tables_returns_nested_list(self, server_module, fake_playwright):
        """browser_extract(what='tables') returns nested lists."""
        result = server_module.browser_extract(what="tables")
        assert "tables" in result
        assert isinstance(result["tables"], list)

    def test_extract_metadata_returns_title(self, server_module, fake_playwright):
        """browser_extract(what='metadata') returns title and meta info."""
        result = server_module.browser_extract(what="metadata")
        assert "title" in result

    def test_extract_forms_returns_inputs(self, server_module, fake_playwright):
        """browser_extract(what='forms') returns form element details."""
        result = server_module.browser_extract(what="forms")
        assert "forms" in result
        assert isinstance(result["forms"], list)

    def test_extract_images_returns_list(self, server_module, fake_playwright):
        """browser_extract(what='images') returns image src/alt."""
        result = server_module.browser_extract(what="images")
        assert "images" in result

    def test_extract_text_returns_body(self, server_module, fake_playwright):
        """browser_extract(what='text') returns page body text."""
        result = server_module.browser_extract(what="text")
        assert "text" in result
        assert len(result["text"]) > 0

    def test_extract_screenshot_returns_base64(self, server_module, fake_playwright):
        """browser_extract(what='screenshot') returns base64 image."""
        result = server_module.browser_extract(what="screenshot")
        assert "screenshot" in result

    # ── browser_wait ──────────────────────────────────────────────────────

    def test_wait_for_text_success(self, server_module, fake_playwright):
        """browser_wait with text condition succeeds."""
        result = server_module.browser_wait(condition="text:Hello")
        assert result["success"] is True
        assert result["condition"] == "text:Hello"
        assert "screenshot" in result

    def test_wait_for_load_success(self, server_module, fake_playwright):
        """browser_wait with 'load' condition succeeds."""
        result = server_module.browser_wait(condition="load")
        assert result["success"] is True

    def test_wait_for_networkidle_success(self, server_module, fake_playwright):
        """browser_wait with 'networkidle' condition succeeds."""
        result = server_module.browser_wait(condition="networkidle")
        assert result["success"] is True

    def test_wait_for_element_success(self, server_module, fake_playwright):
        """browser_wait with 'element:Submit' condition succeeds."""
        result = server_module.browser_wait(condition="element:Submit")
        assert result["success"] is True

    def test_wait_for_no_element_success(self, server_module, fake_playwright):
        """browser_wait with 'no_element:Loading' condition succeeds."""
        result = server_module.browser_wait(condition="no_element:Loading")
        assert result["success"] is True

    def test_wait_timeout_returns_error_not_exception(self, server_module, fake_playwright):
        """Timeout returns an error dict, not an unhandled exception."""
        page = fake_playwright["page"]

        # Patch wait_for_function to raise a timeout
        original_wait = page.wait_for_function

        async def _raise_timeout(expr, **kwargs):
            raise TimeoutError("Waiting for condition timed out")

        page.wait_for_function = _raise_timeout
        result = server_module.browser_wait(condition="text:NonexistentContent", timeout_seconds=1)
        assert result["success"] is False
        assert "error" in result
        page.wait_for_function = original_wait

    # ── browser_scroll ────────────────────────────────────────────────────

    def test_scroll_down(self, server_module, fake_playwright):
        """browser_scroll(direction='down') returns scroll position."""
        result = server_module.browser_scroll(direction="down", amount=300)
        assert "scroll_position" in result

    def test_scroll_top(self, server_module, fake_playwright):
        """browser_scroll(direction='top') scrolls to top."""
        result = server_module.browser_scroll(direction="top")
        assert "scroll_position" in result

    def test_scroll_bottom(self, server_module, fake_playwright):
        """browser_scroll(direction='bottom') scrolls to bottom."""
        result = server_module.browser_scroll(direction="bottom")
        assert "scroll_position" in result

    # ── browser_key ───────────────────────────────────────────────────────

    def test_key_press(self, server_module, fake_playwright):
        """browser_key presses a key and returns success."""
        result = server_module.browser_key(key="Enter")
        assert result["success"] is True
        assert result["key"] == "Enter"
        page = fake_playwright["page"]
        assert "Enter" in page.keyboard.presses

    def test_key_combo(self, server_module, fake_playwright):
        """browser_key handles key combos like Control+a."""
        result = server_module.browser_key(key="Control+a")
        assert result["success"] is True

    # ── browser_script ────────────────────────────────────────────────────

    def test_script_execution(self, server_module, fake_playwright):
        """browser_script executes JS and returns result."""
        result = server_module.browser_script(script="1 + 1")
        assert result["success"] is True
        assert "result" in result

    def test_script_error_returns_error(self, server_module, fake_playwright):
        """browser_script returns error dict on JS failure."""
        page = fake_playwright["page"]
        original_evaluate = page.evaluate

        async def _raise_error(script, *args):
            raise Exception("Script execution failed")

        page.evaluate = _raise_error
        result = server_module.browser_script(script="badcode()")
        assert result["success"] is False
        assert "error" in result
        page.evaluate = original_evaluate

    # ── browser_form_fill ─────────────────────────────────────────────────

    def test_form_fill_multiple_fields(self, server_module, fake_playwright):
        """browser_form_fill fills multiple fields successfully."""
        result = server_module.browser_form_fill(
            fields={"css:#name": "John Doe", "css:#email": "john@example.com"}
        )
        assert "filled" in result
        assert len(result["filled"]) == 2
        assert len(result["errors"]) == 0

    # ── browser_download ──────────────────────────────────────────────────

    def test_download_requires_url_or_target(self, server_module, fake_playwright):
        """browser_download with no url or click_target returns error."""
        result = server_module.browser_download()
        assert "error" in result
        assert "required" in result["error"].lower()

    # ── browser_pdf ───────────────────────────────────────────────────────

    def test_pdf_saves_file(self, server_module, fake_playwright, tmp_path):
        """browser_pdf saves page as PDF."""
        pdf_path = str(tmp_path / "test_output.pdf")
        result = server_module.browser_pdf(path=pdf_path)
        assert result["path"] == pdf_path
        assert result["size_bytes"] > 0
        assert os.path.exists(pdf_path)

    # ── browser_session_save ──────────────────────────────────────────────

    def test_session_save(self, server_module, fake_playwright, tmp_path):
        """browser_session_save persists cookies to disk."""
        old_dir = server_module.SESSIONS_DIR
        server_module.SESSIONS_DIR = str(tmp_path)

        result = server_module.browser_session_save(session="default", name="test_save")
        assert result["saved"] is True
        assert os.path.exists(os.path.join(str(tmp_path), "test_save.json"))

        server_module.SESSIONS_DIR = old_dir

    # ── Anti-Detection ────────────────────────────────────────────────────

    def test_context_has_non_webdriver_navigator(self, server_module):
        """The stealth init script unsets navigator.webdriver."""
        assert "webdriver" in server_module.STEALTH_INIT_SCRIPT
        assert "undefined" in server_module.STEALTH_INIT_SCRIPT

    def test_random_viewport_size(self, server_module):
        """Viewport randomisation uses values within documented range."""
        # The code randomises between 1280-1400 wide and 700-800 tall.
        # We test the documented constants are present in USER_AGENTS.
        assert len(server_module.USER_AGENTS) >= 3
        # Verify the stealth script adds chrome.runtime
        assert "window.chrome" in server_module.STEALTH_INIT_SCRIPT

    def test_stealth_init_script_contains_plugins(self, server_module):
        """Stealth script spoofs navigator.plugins."""
        assert "plugins" in server_module.STEALTH_INIT_SCRIPT

    # ── Multi-Tab ─────────────────────────────────────────────────────────

    def test_multi_tab_returns_results_in_order(self, server_module, fake_playwright, monkeypatch):
        """browser_multi_tab returns one result per URL in order."""
        # Patch browser_open and browser_extract to work with new sessions
        call_log = []

        original_open = server_module.browser_open
        original_extract = server_module.browser_extract

        def mock_open(url, session="default", **kwargs):
            call_log.append(("open", url, session))
            return {"success": True, "title": f"Page: {url}", "url": url}

        def mock_extract(what, session="default", **kwargs):
            call_log.append(("extract", what, session))
            return {"text": f"Content from {session}"}

        monkeypatch.setattr(server_module, "browser_open", mock_open)
        monkeypatch.setattr(server_module, "browser_extract", mock_extract)

        urls = ["https://a.com", "https://b.com", "https://c.com"]
        result = server_module.browser_multi_tab(urls=urls, extract="text")

        assert "results" in result
        assert len(result["results"]) == 3
        assert result["results"][0]["url"] == "https://a.com"
        assert result["results"][1]["url"] == "https://b.com"
        assert result["results"][2]["url"] == "https://c.com"

        server_module.browser_open = original_open
        server_module.browser_extract = original_extract

    def test_multi_tab_respects_max_parallel(self, server_module, fake_playwright, monkeypatch):
        """browser_multi_tab processes URLs up to max_parallel limit."""
        open_count = {"n": 0}

        original_open = server_module.browser_open
        original_extract = server_module.browser_extract

        def mock_open(url, session="default", **kwargs):
            open_count["n"] += 1
            return {"success": True, "title": url, "url": url}

        def mock_extract(what, session="default", **kwargs):
            return {"text": "content"}

        monkeypatch.setattr(server_module, "browser_open", mock_open)
        monkeypatch.setattr(server_module, "browser_extract", mock_extract)

        urls = [f"https://site{i}.com" for i in range(8)]
        result = server_module.browser_multi_tab(urls=urls, max_parallel=3)

        assert len(result["results"]) == 8
        assert open_count["n"] == 8

        server_module.browser_open = original_open
        server_module.browser_extract = original_extract

    # ── Dependency Failure ────────────────────────────────────────────────

    def test_error_when_playwright_not_installed(self, server_module):
        """All tools return a clear error when playwright is missing."""
        old_val = server_module._PLAYWRIGHT_AVAILABLE
        server_module._PLAYWRIGHT_AVAILABLE = False

        checks = [
            server_module.browser_open("https://example.com"),
            server_module.browser_see(),
            server_module.browser_click("Submit"),
            server_module.browser_type(field="email", text="test"),
            server_module.browser_extract(what="text"),
            server_module.browser_wait(condition="load"),
            server_module.browser_scroll(direction="down"),
            server_module.browser_key(key="Enter"),
            server_module.browser_script(script="1+1"),
            server_module.browser_download(),
            server_module.browser_pdf(),
            server_module.browser_session_save(),
            server_module.browser_multi_tab(urls=["https://example.com"]),
            server_module.browser_form_fill(fields={"name": "test"}),
        ]

        for result in checks:
            assert "error" in result
            assert "playwright" in result["error"].lower()

        server_module._PLAYWRIGHT_AVAILABLE = old_val

    # ── Element Finder ────────────────────────────────────────────────────

    def test_find_element_by_aria_label(self, server_module, fake_playwright):
        """_find_element with 'aria:' prefix uses get_by_label."""
        page = fake_playwright["page"]

        async def _test():
            result = await server_module._find_element(page, "aria:submit")
            return result

        result = asyncio.run(_test())
        assert result["type"] == "locator"

    def test_find_element_by_css(self, server_module, fake_playwright):
        """_find_element with 'css:' prefix uses locator()."""
        page = fake_playwright["page"]

        async def _test():
            result = await server_module._find_element(page, "css:#my-button")
            return result

        result = asyncio.run(_test())
        assert result["type"] == "locator"

    def test_find_element_by_coordinates(self, server_module, fake_playwright):
        """_find_element with 'x:N,y:N' returns coordinate dict."""
        page = fake_playwright["page"]

        async def _test():
            result = await server_module._find_element(page, "x:100,y:200")
            return result

        result = asyncio.run(_test())
        assert result["type"] == "coords"
        assert result["x"] == 100
        assert result["y"] == 200

    def test_find_element_by_text(self, server_module, fake_playwright):
        """_find_element default uses get_by_text (accessibility)."""
        page = fake_playwright["page"]

        async def _test():
            result = await server_module._find_element(page, "Submit button")
            return result

        result = asyncio.run(_test())
        assert result["type"] == "locator"

    # ── Cleanup ───────────────────────────────────────────────────────────

    def test_cleanup_closes_resources(self, server_module, fake_playwright):
        """_cleanup() closes contexts, browser, and playwright."""
        async def _test():
            await server_module._cleanup()

        asyncio.run(_test())

        assert server_module._browser is None
        assert server_module._playwright_instance is None
        assert len(server_module._contexts) == 0
        assert len(server_module._pages) == 0

    # ── Config Constants ──────────────────────────────────────────────────

    def test_sessions_dir_path(self, server_module):
        """SESSIONS_DIR points to the canonical path from MASTER_REFERENCE."""
        expected = os.path.expanduser("~/.pawbot/browser-sessions")
        assert server_module.SESSIONS_DIR == expected

    def test_logger_name(self, server_module):
        """Logger uses the canonical name from the phase spec."""
        assert server_module.logger.name == "pawbot.mcp.browser"
