# Phase 17 — Chrome Extension: Visual Indicators & Tab Group Workspaces

> **Goal:** Add visual feedback when PawBot is controlling the browser (glow border, stop button) and implement tab group workspaces for organized agent sessions.  
> **Duration:** 7-10 days  
> **Risk Level:** Low-Medium  
> **Depends On:** Phase 15 (extension core), Phase 16 (accessibility tree)  
> **Reference:** Claude's `agent-visual-indicator.js`, tab group management

---

## What Claude Does (Reverse-Engineered)

1. **Glow Border** — Pulsing orange `box-shadow` on a fixed overlay (`z-index: 2147483646`) when agent is active
2. **Stop Button** — Fixed bottom-center button that sends `STOP_AGENT` message
3. **Static Indicator** — "Claude is active in this tab group" bar with chat + dismiss buttons
4. **Heartbeat** — Indicator checks every 5 seconds if agent is still running; auto-hides if not
5. **Hide During Tool Use** — Indicators hide while a tool executes to avoid interfering with screenshots
6. **Tab Groups** — Agent tabs grouped and color-coded; orphaned groups tracked

---

## 17.1 — Agent Visual Indicator Content Script

**Create:** `pawbot-extension/content-scripts/agent-indicator.js`

```javascript
/**
 * PawBot Agent Visual Indicator
 * Injected into all pages at document_idle.
 * Shows/hides glow border and stop button based on agent state.
 */
(function () {
  let glowEl = null;
  let stopContainer = null;
  let staticIndicator = null;
  let isActive = false;
  let heartbeatInterval = null;

  // ── Create Glow Border ──────────────────────────────────────────
  function createGlow() {
    if (glowEl) { glowEl.style.display = ""; return; }
    
    // Inject animation styles
    if (!document.getElementById("pawbot-agent-styles")) {
      const style = document.createElement("style");
      style.id = "pawbot-agent-styles";
      style.textContent = `
        @keyframes pawbot-pulse {
          0%, 100% { box-shadow: inset 0 0 10px rgba(99, 102, 241, 0.5),
            inset 0 0 20px rgba(99, 102, 241, 0.3); }
          50% { box-shadow: inset 0 0 15px rgba(99, 102, 241, 0.7),
            inset 0 0 25px rgba(99, 102, 241, 0.5); }
        }`;
      document.head.appendChild(style);
    }

    glowEl = document.createElement("div");
    glowEl.id = "pawbot-agent-glow";
    glowEl.style.cssText = `position:fixed; top:0; left:0; right:0; bottom:0;
      pointer-events:none; z-index:2147483646; opacity:0;
      transition:opacity 0.3s; animation:pawbot-pulse 2s infinite;`;
    document.body.appendChild(glowEl);
    requestAnimationFrame(() => { if (glowEl) glowEl.style.opacity = "1"; });
  }

  // ── Create Stop Button ──────────────────────────────────────────
  function createStopButton() {
    if (stopContainer) { stopContainer.style.display = ""; return; }

    stopContainer = document.createElement("div");
    stopContainer.id = "pawbot-stop-container";
    stopContainer.style.cssText = `position:fixed; bottom:16px; left:50%;
      transform:translateX(-50%); z-index:2147483647; pointer-events:none;`;

    const btn = document.createElement("button");
    btn.id = "pawbot-stop-button";
    btn.innerHTML = `🐾 <span>Stop PawBot</span>`;
    btn.style.cssText = `padding:12px 20px; background:#1e1b4b; color:#e0e7ff;
      border:1px solid rgba(99,102,241,0.4); border-radius:12px;
      font-family:-apple-system,BlinkMacSystemFont,sans-serif; font-size:14px;
      font-weight:600; cursor:pointer; pointer-events:auto; display:inline-flex;
      align-items:center; gap:8px; box-shadow:0 20px 60px rgba(99,102,241,0.3);
      transition:all 0.3s; opacity:0; transform:translateY(100px);`;

    btn.addEventListener("mouseenter", () => {
      btn.style.background = "#312e81";
      btn.style.borderColor = "rgba(99,102,241,0.7)";
    });
    btn.addEventListener("mouseleave", () => {
      btn.style.background = "#1e1b4b";
      btn.style.borderColor = "rgba(99,102,241,0.4)";
    });
    btn.addEventListener("click", async () => {
      await chrome.runtime.sendMessage({ type: "STOP_AGENT", fromTabId: "CURRENT_TAB" });
    });

    stopContainer.appendChild(btn);
    document.body.appendChild(stopContainer);
    requestAnimationFrame(() => {
      btn.style.transform = "translateY(0)";
      btn.style.opacity = "1";
    });
  }

  // ── Show / Hide ─────────────────────────────────────────────────
  function showIndicators() {
    isActive = true;
    createGlow();
    createStopButton();
  }

  function hideIndicators() {
    isActive = false;
    if (glowEl) { glowEl.style.opacity = "0"; }
    if (stopContainer) {
      const btn = stopContainer.querySelector("#pawbot-stop-button");
      if (btn) { btn.style.opacity = "0"; btn.style.transform = "translateY(100px)"; }
    }
    setTimeout(() => {
      if (!isActive) {
        if (glowEl?.parentNode) { glowEl.parentNode.removeChild(glowEl); glowEl = null; }
        if (stopContainer?.parentNode) {
          stopContainer.parentNode.removeChild(stopContainer);
          stopContainer = null;
        }
      }
    }, 300);
  }

  // ── Static Indicator with Heartbeat ─────────────────────────────
  function showStaticIndicator() {
    if (staticIndicator) { staticIndicator.style.display = ""; return; }

    staticIndicator = document.createElement("div");
    staticIndicator.id = "pawbot-static-indicator";
    staticIndicator.innerHTML = `
      🐾 <span style="color:#e0e7ff; font-size:14px;">PawBot is active in this tab group</span>
      <button id="pawbot-static-dismiss" style="margin-left:12px; background:transparent;
        border:1px solid rgba(99,102,241,0.3); color:#a5b4fc; padding:4px 12px;
        border-radius:6px; cursor:pointer; font-size:12px; pointer-events:auto;">Dismiss</button>`;
    staticIndicator.style.cssText = `position:fixed; bottom:16px; left:50%;
      transform:translateX(-50%); background:#1e1b4b; border:1px solid rgba(99,102,241,0.3);
      border-radius:14px; padding:8px 16px; z-index:2147483647; display:inline-flex;
      align-items:center; gap:8px; box-shadow:0 20px 40px rgba(0,0,0,0.2);
      font-family:-apple-system,BlinkMacSystemFont,sans-serif; pointer-events:none;`;

    staticIndicator.querySelector("#pawbot-static-dismiss").addEventListener("click", () => {
      chrome.runtime.sendMessage({ type: "DISMISS_STATIC_INDICATOR_FOR_GROUP" });
    });

    document.body.appendChild(staticIndicator);

    // Heartbeat — check if agent is still running every 5s
    heartbeatInterval = setInterval(async () => {
      try {
        const res = await chrome.runtime.sendMessage({ type: "STATIC_INDICATOR_HEARTBEAT" });
        if (!res?.success) hideStaticIndicator();
      } catch { hideStaticIndicator(); }
    }, 5000);
  }

  function hideStaticIndicator() {
    if (heartbeatInterval) { clearInterval(heartbeatInterval); heartbeatInterval = null; }
    if (staticIndicator?.parentNode) {
      staticIndicator.parentNode.removeChild(staticIndicator);
      staticIndicator = null;
    }
  }

  // ── Message Handler ─────────────────────────────────────────────
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.type === "SHOW_AGENT_INDICATORS") { showIndicators(); sendResponse({ success: true }); }
    else if (msg.type === "HIDE_AGENT_INDICATORS") { hideIndicators(); sendResponse({ success: true }); }
    else if (msg.type === "HIDE_FOR_TOOL_USE") {
      if (glowEl) glowEl.style.display = "none";
      if (stopContainer) stopContainer.style.display = "none";
      sendResponse({ success: true });
    }
    else if (msg.type === "SHOW_AFTER_TOOL_USE") {
      if (glowEl) glowEl.style.display = "";
      if (stopContainer) stopContainer.style.display = "";
      sendResponse({ success: true });
    }
    else if (msg.type === "SHOW_STATIC_INDICATOR") { showStaticIndicator(); sendResponse({ success: true }); }
    else if (msg.type === "HIDE_STATIC_INDICATOR") { hideStaticIndicator(); sendResponse({ success: true }); }
  });

  window.addEventListener("beforeunload", () => { hideIndicators(); hideStaticIndicator(); });
})();
```

---

## 17.2 — Tab Group Manager

**Add to service worker:** `pawbot-extension/lib/tab-groups.js`

```javascript
/**
 * Tab Group Manager — organize agent tabs into Chrome tab groups.
 */
export class TabGroupManager {
  constructor() {
    this.groups = new Map(); // groupId → { mainTabId, tabs: Set, created }
  }

  async createGroup(tabId, title = "🐾 PawBot") {
    const groupId = await chrome.tabs.group({ tabIds: [tabId] });
    await chrome.tabGroups.update(groupId, {
      title, color: "purple", collapsed: false,
    });
    this.groups.set(groupId, {
      mainTabId: tabId,
      tabs: new Set([tabId]),
      created: Date.now(),
    });
    return groupId;
  }

  async addTabToGroup(tabId, groupId) {
    await chrome.tabs.group({ tabIds: [tabId], groupId });
    const group = this.groups.get(groupId);
    if (group) group.tabs.add(tabId);
  }

  async handleTabClosed(tabId) {
    for (const [groupId, group] of this.groups) {
      if (group.tabs.has(tabId)) {
        group.tabs.delete(tabId);
        if (group.tabs.size === 0) this.groups.delete(groupId);
        break;
      }
    }
  }

  findGroupByTab(tabId) {
    for (const [groupId, group] of this.groups) {
      if (group.tabs.has(tabId)) return { groupId, ...group };
    }
    return null;
  }

  getMainTabId(tabId) {
    const group = this.findGroupByTab(tabId);
    return group?.mainTabId || null;
  }
}
```

---

## Verification Checklist — Phase 17

- [ ] Orange/purple glow border appears on active tab when agent is working
- [ ] "Stop PawBot" button appears bottom-center during agent activity
- [ ] Clicking "Stop PawBot" sends stop message to service worker
- [ ] Indicators fade out smoothly (300ms transition)
- [ ] Indicators hide during tool execution (no interference with screenshots)
- [ ] Static indicator shows "PawBot is active in this tab group"
- [ ] Heartbeat auto-hides static indicator when agent disconnects
- [ ] Tab groups created with purple color and "🐾 PawBot" title
- [ ] New agent-opened tabs added to the same group
- [ ] Tab group cleans up when all tabs are closed
