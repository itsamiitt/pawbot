"""Tests for Phase 8 — Browser Integration & Sandbox."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pawbot.browser.engine import BrowserEngine
from pawbot.browser.cleanup import cleanup_screenshots
from pawbot.agent.tools.browser_tool import (
    BrowseTool,
    ScreenshotTool,
    ClickTool,
    FillFormTool,
    EvalJSTool,
    BROWSER_TOOLS,
    get_browser_engine,
    set_browser_engine,
)
from pawbot.config.schema import BrowserSandboxConfig, SandboxConfig


# ── Config Schema Tests ──────────────────────────────────────────────────────


class TestBrowserSandboxConfig:
    """Test BrowserSandboxConfig defaults and validation."""

    def test_defaults(self):
        cfg = BrowserSandboxConfig()
        assert cfg.enabled is False
        assert cfg.headless is True
        assert cfg.auto_start is False
        assert cfg.max_pages == 5
        assert cfg.page_timeout_ms == 30_000
        assert cfg.persist_state is True
        assert cfg.js_execution is True
        assert len(cfg.blocked_domains) >= 4  # At least the core SSRF domains
        assert "localhost" in cfg.blocked_domains
        assert "169.254.169.254" in cfg.blocked_domains

    def test_enabled_override(self):
        cfg = BrowserSandboxConfig(enabled=True, headless=False, max_pages=3)
        assert cfg.enabled is True
        assert cfg.headless is False
        assert cfg.max_pages == 3

    def test_sandbox_config_nesting(self):
        cfg = SandboxConfig()
        assert cfg.mode == "off"
        assert isinstance(cfg.browser, BrowserSandboxConfig)
        assert cfg.browser.enabled is False

    def test_config_root_has_sandbox(self):
        from pawbot.config.schema import Config
        cfg = Config()
        assert hasattr(cfg, "sandbox")
        assert isinstance(cfg.sandbox, SandboxConfig)


# ── BrowserEngine Unit Tests ─────────────────────────────────────────────────


class TestBrowserEngineInit:
    """Test BrowserEngine initialisation without Playwright."""

    def test_defaults(self):
        engine = BrowserEngine()
        assert engine._headless is True
        assert engine.is_running is False
        assert engine._max_pages == 5

    def test_custom_config(self):
        engine = BrowserEngine(
            headless=False,
            max_pages=3,
            page_timeout_ms=15_000,
            js_execution=False,
        )
        assert engine._headless is False
        assert engine._max_pages == 3
        assert engine._page_timeout_ms == 15_000
        assert engine._js_execution is False

    def test_blocked_domains_include_defaults(self):
        engine = BrowserEngine(blocked_domains=["evil.com"])
        assert "evil.com" in engine._blocked_domains
        assert "localhost" in engine._blocked_domains  # default always included
        assert "169.254.169.254" in engine._blocked_domains

    def test_directories_created(self, tmp_path):
        """Engine should create screenshot and storage dirs."""
        engine = BrowserEngine()
        # Just check the class attributes exist
        assert engine.SCREENSHOT_DIR is not None
        assert engine.STORAGE_DIR is not None


class TestBrowserEngineDomainBlocking:
    """Test domain blocking logic (without actual browser)."""

    @pytest.fixture
    def engine(self):
        return BrowserEngine(
            blocked_domains=["evil.com", "*.malware.net"],
            allowed_domains=[],
        )

    @pytest.fixture
    def allowed_engine(self):
        return BrowserEngine(
            allowed_domains=["example.com", "*.trusted.org"],
        )

    @pytest.mark.asyncio
    async def test_blocked_domain_exact(self, engine):
        route = AsyncMock()
        route.request.url = "https://evil.com/page"
        await engine._check_domain(route)
        route.abort.assert_called_once_with("blockedbyclient")

    @pytest.mark.asyncio
    async def test_blocked_domain_wildcard(self, engine):
        route = AsyncMock()
        route.request.url = "https://test.malware.net/exploit"
        await engine._check_domain(route)
        route.abort.assert_called_once_with("blockedbyclient")

    @pytest.mark.asyncio
    async def test_blocked_localhost(self, engine):
        route = AsyncMock()
        route.request.url = "http://localhost:8080/admin"
        await engine._check_domain(route)
        route.abort.assert_called_once_with("blockedbyclient")

    @pytest.mark.asyncio
    async def test_blocked_aws_metadata(self, engine):
        route = AsyncMock()
        route.request.url = "http://169.254.169.254/latest/meta-data/"
        await engine._check_domain(route)
        route.abort.assert_called_once_with("blockedbyclient")

    @pytest.mark.asyncio
    async def test_allowed_domain_passes(self, engine):
        route = AsyncMock()
        route.request.url = "https://example.com/page"
        await engine._check_domain(route)
        route.continue_.assert_called_once()

    @pytest.mark.asyncio
    async def test_allowed_list_blocks_unlisted(self, allowed_engine):
        route = AsyncMock()
        route.request.url = "https://unknown-site.com/page"
        await allowed_engine._check_domain(route)
        route.abort.assert_called_once_with("blockedbyclient")

    @pytest.mark.asyncio
    async def test_allowed_list_permits_listed(self, allowed_engine):
        route = AsyncMock()
        route.request.url = "https://example.com/page"
        await allowed_engine._check_domain(route)
        route.continue_.assert_called_once()


# ── Browser Tool Tests ────────────────────────────────────────────────────────


class TestBrowserTools:
    """Test browser tools conform to the Tool interface."""

    def test_all_tools_count(self):
        assert len(BROWSER_TOOLS) == 5

    @pytest.mark.parametrize("tool_cls", BROWSER_TOOLS)
    def test_tool_has_required_attrs(self, tool_cls):
        tool = tool_cls()
        assert hasattr(tool, "name")
        assert hasattr(tool, "description")
        assert hasattr(tool, "parameters")
        assert hasattr(tool, "execute")

    @pytest.mark.parametrize("tool_cls", BROWSER_TOOLS)
    def test_tool_schema(self, tool_cls):
        tool = tool_cls()
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert "name" in schema["function"]
        assert "description" in schema["function"]
        assert "parameters" in schema["function"]

    def test_browse_tool_name(self):
        assert BrowseTool().name == "browse"

    def test_screenshot_tool_name(self):
        assert ScreenshotTool().name == "screenshot"

    def test_click_tool_name(self):
        assert ClickTool().name == "browser_click"

    def test_fill_tool_name(self):
        assert FillFormTool().name == "browser_fill"

    def test_eval_tool_name(self):
        assert EvalJSTool().name == "browser_eval"


class TestBrowserToolExecution:
    """Test tool execution with mocked engine."""

    @pytest.fixture(autouse=True)
    def mock_engine(self):
        engine = AsyncMock(spec=BrowserEngine)
        set_browser_engine(engine)
        yield engine
        set_browser_engine(None)

    @pytest.mark.asyncio
    async def test_browse_returns_page_info(self, mock_engine):
        mock_engine.navigate.return_value = {
            "tab_id": "main",
            "url": "https://example.com",
            "title": "Example",
            "status": 200,
        }
        mock_engine.get_text.return_value = "Hello World"

        tool = BrowseTool()
        result = await tool.execute(url="https://example.com")
        assert "Example" in result
        assert "200" in result
        assert "Hello World" in result

    @pytest.mark.asyncio
    async def test_browse_truncates_long_text(self, mock_engine):
        mock_engine.navigate.return_value = {
            "tab_id": "main", "url": "https://example.com",
            "title": "Test", "status": 200,
        }
        mock_engine.get_text.return_value = "x" * 10000

        tool = BrowseTool()
        result = await tool.execute(url="https://example.com")
        assert "truncated" in result

    @pytest.mark.asyncio
    async def test_screenshot_returns_path(self, mock_engine):
        mock_engine.screenshot.return_value = "/tmp/screenshot.png"

        tool = ScreenshotTool()
        result = await tool.execute()
        assert "/tmp/screenshot.png" in result

    @pytest.mark.asyncio
    async def test_click_success(self, mock_engine):
        mock_engine.click.return_value = True

        tool = ClickTool()
        result = await tool.execute(selector="#btn")
        assert "successfully" in result

    @pytest.mark.asyncio
    async def test_click_failure(self, mock_engine):
        mock_engine.click.return_value = False

        tool = ClickTool()
        result = await tool.execute(selector="#btn")
        assert "Error" in result or "Failed" in result

    @pytest.mark.asyncio
    async def test_fill_success(self, mock_engine):
        mock_engine.fill.return_value = True

        tool = FillFormTool()
        result = await tool.execute(selector="#email", value="test@example.com")
        assert "Filled" in result

    @pytest.mark.asyncio
    async def test_eval_js(self, mock_engine):
        mock_engine.evaluate_js.return_value = 42

        tool = EvalJSTool()
        result = await tool.execute(expression="1 + 1")
        assert "42" in result


# ── Screenshot Cleanup Tests ─────────────────────────────────────────────────


class TestScreenshotCleanup:
    """Test screenshot retention cleanup."""

    def test_cleanup_old_files(self, tmp_path):
        """Files older than max_age_days should be deleted."""
        # Create an 'old' screenshot
        old_file = tmp_path / "screenshot_old.png"
        old_file.write_bytes(b"PNG")
        # Set mtime to 10 days ago
        import os
        os.utime(old_file, (time.time() - 10 * 86400, time.time() - 10 * 86400))

        # Create a 'new' screenshot
        new_file = tmp_path / "screenshot_new.png"
        new_file.write_bytes(b"PNG")

        deleted = cleanup_screenshots(tmp_path, max_age_days=7)
        assert deleted == 1
        assert not old_file.exists()
        assert new_file.exists()

    def test_cleanup_empty_dir(self, tmp_path):
        deleted = cleanup_screenshots(tmp_path)
        assert deleted == 0

    def test_cleanup_nonexistent_dir(self):
        deleted = cleanup_screenshots(Path("/nonexistent/path"))
        assert deleted == 0

    def test_cleanup_skips_non_png(self, tmp_path):
        """Only .png files should be cleaned up."""
        txt_file = tmp_path / "log.txt"
        txt_file.write_text("hello")
        import os
        os.utime(txt_file, (time.time() - 20 * 86400, time.time() - 20 * 86400))

        deleted = cleanup_screenshots(tmp_path, max_age_days=7)
        assert deleted == 0
        assert txt_file.exists()


# ── Param Validation Tests ────────────────────────────────────────────────────


class TestBrowserToolValidation:
    """Test parameter validation for browser tools."""

    def test_browse_requires_url(self):
        tool = BrowseTool()
        errors = tool.validate_params({})
        assert len(errors) > 0
        assert any("url" in e for e in errors)

    def test_browse_valid_params(self):
        tool = BrowseTool()
        errors = tool.validate_params({"url": "https://example.com"})
        assert len(errors) == 0

    def test_click_requires_selector(self):
        tool = ClickTool()
        errors = tool.validate_params({})
        assert len(errors) > 0

    def test_fill_requires_both(self):
        tool = FillFormTool()
        errors = tool.validate_params({})
        assert len(errors) >= 2  # selector + value

    def test_eval_requires_expression(self):
        tool = EvalJSTool()
        errors = tool.validate_params({})
        assert len(errors) > 0
