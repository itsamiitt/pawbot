"""Tests for Phase 16 — Accessibility Tree & Page Intelligence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pawbot.tools.browser_chrome import (
    CHROME_TOOLS,
    get_chrome_tool,
    get_chrome_tool_names,
    map_chrome_tool_to_extension,
)


# ── Tool Definitions Tests ────────────────────────────────────────────────────


class TestChromeToolDefinitions:
    """Test the chrome_* tool definitions (Phase 16.3)."""

    def test_tool_count(self):
        assert len(CHROME_TOOLS) == 13

    def test_all_tools_have_name(self):
        for tool in CHROME_TOOLS:
            assert "name" in tool
            assert tool["name"].startswith("chrome_")

    def test_all_tools_have_description(self):
        for tool in CHROME_TOOLS:
            assert "description" in tool
            assert len(tool["description"]) > 10

    def test_all_tools_have_parameters(self):
        for tool in CHROME_TOOLS:
            assert "parameters" in tool

    def test_get_tool_names(self):
        names = get_chrome_tool_names()
        assert "chrome_read_page" in names
        assert "chrome_click" in names
        assert "chrome_click_advanced" in names
        assert "chrome_type" in names
        assert "chrome_type_advanced" in names
        assert "chrome_navigate" in names
        assert "chrome_screenshot" in names
        assert "chrome_get_tabs" in names
        assert "chrome_switch_tab" in names
        assert "chrome_new_tab" in names
        assert "chrome_close_tab" in names
        assert "chrome_scroll" in names
        assert "chrome_extract" in names

    def test_get_tool_by_name(self):
        tool = get_chrome_tool("chrome_read_page")
        assert tool is not None
        assert tool["name"] == "chrome_read_page"
        assert "filter" in tool["parameters"]

    def test_get_tool_nonexistent(self):
        assert get_chrome_tool("chrome_nonexistent") is None

    def test_read_page_params(self):
        tool = get_chrome_tool("chrome_read_page")
        params = tool["parameters"]
        assert "filter" in params
        assert params["filter"]["enum"] == ["all", "interactive"]
        assert "depth" in params
        assert "ref_id" in params

    def test_click_params(self):
        tool = get_chrome_tool("chrome_click")
        params = tool["parameters"]
        assert "ref_id" in params
        assert "coordinate" in params

    def test_type_params(self):
        tool = get_chrome_tool("chrome_type")
        params = tool["parameters"]
        assert params["ref_id"]["required"] is True
        assert params["text"]["required"] is True

    def test_navigate_params(self):
        tool = get_chrome_tool("chrome_navigate")
        assert tool["parameters"]["url"]["required"] is True

    def test_screenshot_no_required_params(self):
        tool = get_chrome_tool("chrome_screenshot")
        assert tool["parameters"] == {}

    def test_extract_params(self):
        tool = get_chrome_tool("chrome_extract")
        params = tool["parameters"]
        assert "what" in params
        assert params["what"]["enum"] == ["text", "links", "tables", "forms", "metadata"]

    def test_scroll_params(self):
        tool = get_chrome_tool("chrome_scroll")
        params = tool["parameters"]
        assert "direction" in params
        assert "top" in params["direction"]["enum"]
        assert "bottom" in params["direction"]["enum"]

    def test_map_tool_to_extension(self):
        assert map_chrome_tool_to_extension("chrome_read_page") == "read_page"
        assert map_chrome_tool_to_extension("chrome_click") == "click"
        assert map_chrome_tool_to_extension("chrome_click_advanced") == "click_advanced"
        assert map_chrome_tool_to_extension("chrome_extract") == "extract"

    def test_map_tool_non_chrome(self):
        assert map_chrome_tool_to_extension("read_file") is None
        assert map_chrome_tool_to_extension("") is None


# ── Extension Files Tests (Phase 16 Updates) ──────────────────────────────────


class TestAccessibilityTreeScript:
    """Test the enhanced accessibility tree content script."""

    EXTENSION_DIR = Path(__file__).resolve().parent.parent.parent / "pawbot-extension"

    def test_script_exists(self):
        path = self.EXTENSION_DIR / "content-scripts" / "accessibility-tree.js"
        assert path.exists()

    def test_has_role_detection(self):
        content = self._read_script()
        # Role map should include standard ARIA roles
        assert "ROLE_MAP" in content or "roleMap" in content
        assert '"link"' in content
        assert '"button"' in content
        assert '"textbox"' in content
        assert '"checkbox"' in content
        assert '"combobox"' in content

    def test_has_label_extraction(self):
        content = self._read_script()
        assert "getLabel" in content
        assert "aria-label" in content
        assert "placeholder" in content
        assert "label[for" in content or "label\\[for" in content

    def test_has_visibility_check(self):
        content = self._read_script()
        assert "isVisible" in content
        assert "display" in content
        assert "visibility" in content
        assert "opacity" in content

    def test_has_interactive_filter(self):
        content = self._read_script()
        assert "isInteractive" in content
        assert "interactive" in content
        assert "tabindex" in content
        assert "contenteditable" in content

    def test_has_viewport_check(self):
        content = self._read_script()
        assert "isInViewport" in content or "innerHeight" in content
        assert "getBoundingClientRect" in content

    def test_has_weakref_element_map(self):
        content = self._read_script()
        assert "WeakRef" in content
        assert "__pawbotElementMap" in content
        assert "ref_" in content

    def test_has_depth_limiting(self):
        content = self._read_script()
        assert "maxDepth" in content

    def test_has_char_limiting(self):
        content = self._read_script()
        assert "maxChars" in content or "charLimit" in content

    def test_has_ref_id_focus(self):
        content = self._read_script()
        assert "refId" in content

    def test_outputs_indented_text(self):
        content = self._read_script()
        # Should build lines with indentation
        assert "lines" in content
        assert '" ".repeat' in content or "indent" in content

    def test_has_select_options(self):
        content = self._read_script()
        assert "option" in content
        assert "selected" in content

    def test_skips_hidden_elements(self):
        content = self._read_script()
        assert "SKIP_TAGS" in content or "script" in content
        assert "style" in content
        assert "noscript" in content

    def test_has_backward_compat_alias(self):
        content = self._read_script()
        assert "__pawbotAccessibilityTree" in content
        assert "__generateAccessibilityTree" in content

    def test_input_type_roles(self):
        content = self._read_script()
        assert "checkbox" in content
        assert "radio" in content
        assert "slider" in content or "range" in content

    def _read_script(self):
        path = self.EXTENSION_DIR / "content-scripts" / "accessibility-tree.js"
        return path.read_text(encoding="utf-8")


class TestServiceWorkerAdvancedTools:
    """Test the service worker has Phase 16 advanced tools."""

    EXTENSION_DIR = Path(__file__).resolve().parent.parent.parent / "pawbot-extension"

    def test_has_click_advanced(self):
        content = self._read_sw()
        assert "toolClickAdvanced" in content
        assert "click_advanced" in content
        assert "debugger.attach" in content

    def test_has_type_advanced(self):
        content = self._read_sw()
        assert "toolTypeAdvanced" in content
        assert "type_advanced" in content
        assert "Input.dispatchKeyEvent" in content

    def test_has_extract_tool(self):
        content = self._read_sw()
        assert "toolExtract" in content
        assert "extract" in content

    def test_extract_supports_all_modes(self):
        content = self._read_sw()
        for mode in ["text", "links", "tables", "forms", "metadata"]:
            assert f'"{mode}"' in content, f"Missing extract mode: {mode}"

    def test_cdp_detach_in_finally(self):
        """CDP tools should detach debugger in finally block."""
        content = self._read_sw()
        # toolClickAdvanced and toolTypeAdvanced should use try/finally
        assert "finally" in content
        assert "debugger.detach" in content

    def test_human_like_typing_delay(self):
        content = self._read_sw()
        assert "Math.random()" in content
        assert "setTimeout" in content

    def test_extract_metadata_includes_og(self):
        content = self._read_sw()
        assert "og:image" in content

    def _read_sw(self):
        path = self.EXTENSION_DIR / "service-worker.js"
        return path.read_text(encoding="utf-8")
