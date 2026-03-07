"""Tests for Phase 17 — Visual Indicators & Tab Group Workspaces."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


EXTENSION_DIR = Path(__file__).resolve().parent.parent.parent / "pawbot-extension"


# ── Agent Indicator Tests ─────────────────────────────────────────────────────


class TestAgentIndicatorScript:
    """Test the enhanced agent visual indicator content script."""

    def _read_script(self):
        path = EXTENSION_DIR / "content-scripts" / "agent-indicator.js"
        return path.read_text(encoding="utf-8")

    def test_script_exists(self):
        path = EXTENSION_DIR / "content-scripts" / "agent-indicator.js"
        assert path.exists()

    def test_has_glow_border(self):
        content = self._read_script()
        assert "pawbot-agent-glow" in content
        assert "pawbot-pulse" in content
        assert "box-shadow" in content

    def test_glow_uses_correct_z_index(self):
        content = self._read_script()
        assert "2147483646" in content  # glow z-index

    def test_has_stop_button(self):
        content = self._read_script()
        assert "pawbot-stop-button" in content
        assert "Stop PawBot" in content

    def test_stop_button_sends_message(self):
        content = self._read_script()
        assert "STOP_AGENT" in content
        assert "sendMessage" in content

    def test_stop_button_highest_z_index(self):
        content = self._read_script()
        assert "2147483647" in content  # stop button z-index

    def test_show_hide_indicators(self):
        content = self._read_script()
        assert "showIndicators" in content
        assert "hideIndicators" in content

    def test_hide_has_transition(self):
        """Indicators should fade out with transition."""
        content = self._read_script()
        assert "opacity" in content
        assert "300" in content  # 300ms timeout for cleanup

    def test_cleanup_removes_from_dom(self):
        content = self._read_script()
        assert "removeChild" in content

    def test_has_static_indicator(self):
        content = self._read_script()
        assert "pawbot-static-indicator" in content
        assert "PawBot is active in this tab group" in content

    def test_static_indicator_has_dismiss(self):
        content = self._read_script()
        assert "pawbot-static-dismiss" in content
        assert "Dismiss" in content
        assert "DISMISS_STATIC_INDICATOR_FOR_GROUP" in content

    def test_heartbeat_every_5_seconds(self):
        content = self._read_script()
        assert "setInterval" in content
        assert "5000" in content
        assert "STATIC_INDICATOR_HEARTBEAT" in content

    def test_heartbeat_auto_hides(self):
        content = self._read_script()
        assert "hideStaticIndicator" in content
        assert "clearInterval" in content

    def test_hide_for_tool_use(self):
        """Indicators should hide during tool execution to avoid screenshot interference."""
        content = self._read_script()
        assert "HIDE_FOR_TOOL_USE" in content
        assert "SHOW_AFTER_TOOL_USE" in content

    def test_tool_hide_preserves_state(self):
        """Tool hide should use display:none, not remove from DOM."""
        content = self._read_script()
        # When hiding for tool use, it should set display to "none"
        assert 'display = "none"' in content or "display: none" in content

    def test_respond_to_show_hide_messages(self):
        content = self._read_script()
        assert "SHOW_AGENT_INDICATORS" in content
        assert "HIDE_AGENT_INDICATORS" in content

    def test_backward_compat_messages(self):
        content = self._read_script()
        assert "pawbot_agent_active" in content
        assert "pawbot_agent_idle" in content

    def test_cleanup_on_unload(self):
        content = self._read_script()
        assert "beforeunload" in content

    def test_has_hover_effects(self):
        content = self._read_script()
        assert "mouseenter" in content
        assert "mouseleave" in content

    def test_animation_keyframes(self):
        content = self._read_script()
        assert "@keyframes" in content
        assert "pawbot-pulse" in content

    def test_exposed_global_functions(self):
        content = self._read_script()
        assert "__pawbotShowIndicator" in content
        assert "__pawbotHideIndicator" in content
        assert "__pawbotShowStaticIndicator" in content
        assert "__pawbotHideStaticIndicator" in content


# ── Tab Group Manager Tests ───────────────────────────────────────────────────


class TestTabGroupManagerScript:
    """Test the tab group manager module."""

    def _read_script(self):
        path = EXTENSION_DIR / "lib" / "tab-groups.js"
        return path.read_text(encoding="utf-8")

    def test_script_exists(self):
        path = EXTENSION_DIR / "lib" / "tab-groups.js"
        assert path.exists()

    def test_exports_class(self):
        content = self._read_script()
        assert "export class TabGroupManager" in content

    def test_has_create_group(self):
        content = self._read_script()
        assert "createGroup" in content
        assert "chrome.tabs.group" in content
        assert "chrome.tabGroups.update" in content

    def test_purple_color_default(self):
        content = self._read_script()
        assert '"purple"' in content

    def test_pawbot_title_default(self):
        content = self._read_script()
        assert "🐾 PawBot" in content

    def test_has_add_tab_to_group(self):
        content = self._read_script()
        assert "addTabToGroup" in content

    def test_has_handle_tab_closed(self):
        content = self._read_script()
        assert "handleTabClosed" in content

    def test_cleanup_empty_groups(self):
        """Empty groups should be deleted from tracking."""
        content = self._read_script()
        assert "groups.delete" in content
        assert "size === 0" in content

    def test_has_find_group_by_tab(self):
        content = self._read_script()
        assert "findGroupByTab" in content

    def test_has_get_main_tab_id(self):
        content = self._read_script()
        assert "getMainTabId" in content
        assert "mainTabId" in content

    def test_has_list_groups(self):
        content = self._read_script()
        assert "listGroups" in content

    def test_has_set_collapsed(self):
        content = self._read_script()
        assert "setCollapsed" in content
        assert "collapsed" in content

    def test_has_update_title(self):
        content = self._read_script()
        assert "updateTitle" in content

    def test_tracks_creation_time(self):
        content = self._read_script()
        assert "Date.now()" in content
        assert "created" in content

    def test_total_tabs_property(self):
        content = self._read_script()
        assert "totalTabs" in content


# ── Service Worker Phase 17 Updates ───────────────────────────────────────────


class TestServiceWorkerPhase17:
    """Test service worker has Phase 17 agent state and message handling."""

    def _read_sw(self):
        path = EXTENSION_DIR / "service-worker.js"
        return path.read_text(encoding="utf-8")

    def test_has_agent_state_tracking(self):
        content = self._read_sw()
        assert "agentActive" in content
        assert "agentTabId" in content

    def test_handles_stop_agent(self):
        content = self._read_sw()
        assert "STOP_AGENT" in content
        assert "stop_agent" in content  # Forwards to native host

    def test_handles_heartbeat(self):
        content = self._read_sw()
        assert "STATIC_INDICATOR_HEARTBEAT" in content
        assert "agentActive" in content

    def test_handles_dismiss_indicator(self):
        content = self._read_sw()
        assert "DISMISS_STATIC_INDICATOR_FOR_GROUP" in content

    def test_check_status_includes_agent_state(self):
        content = self._read_sw()
        assert "agentActive" in content

    def test_tab_close_resets_agent(self):
        content = self._read_sw()
        assert "onRemoved" in content
        assert "agentTabId" in content
