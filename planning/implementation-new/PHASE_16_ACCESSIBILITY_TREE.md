# Phase 16 — Chrome Extension: Accessibility Tree & Page Intelligence

> **Goal:** Implement the accessibility tree content script that lets PawBot "see" any web page as structured text, plus advanced element interaction.  
> **Duration:** 10-14 days  
> **Risk Level:** Medium  
> **Depends On:** Phase 15 (extension core)  
> **Reference:** Claude's `accessibility-tree.js-D8KNCIWO.js`

---

## What Claude Does (Reverse-Engineered)

Claude's accessibility tree script is the **most critical** piece — it converts any web page into structured text the AI can understand and reference. Key discoveries:

1. **WeakRef Element Map** — `window.__claudeElementMap[ref_id] = new WeakRef(element)`. Uses WeakRef so garbage collection still works. Each element gets a unique `ref_XXX` ID.
2. **Role Detection** — Maps HTML tags to ARIA roles (`a` → `link`, `button` → `button`, `input[type=checkbox]` → `checkbox`, etc.)
3. **Label Extraction** — Priority: `aria-label` → `placeholder` → `title` → `alt` → `label[for]` → `value` → direct text content
4. **Visibility Filtering** — Skips `display:none`, `visibility:hidden`, `opacity:0`, zero-size, off-viewport elements
5. **Interactive Filter** — `filter="interactive"` only returns clickable/typeable elements
6. **Depth Limiting** — `depth` param controls how deep to traverse (default 15)
7. **Size Limiting** — `maxChars` param truncates output to prevent context overflow
8. **Ref ID Focus** — `refId` param zooms into a single element subtree

---

## 16.1 — Accessibility Tree Content Script

**Create:** `pawbot-extension/content-scripts/accessibility-tree.js`

```javascript
/**
 * PawBot Accessibility Tree Generator
 * Injected into ALL pages at document_start, ALL frames.
 *
 * Creates: window.__pawbotElementMap (WeakRef map)
 * Creates: window.__generateAccessibilityTree(filter, depth, maxChars, refId)
 *
 * Output format (indented text):
 *   button "Submit" [ref_1] type="submit"
 *    textbox "Email" [ref_2] placeholder="Enter email"
 *    link "Sign up" [ref_3] href="/signup"
 */

(function () {
  // Initialize element tracking
  window.__pawbotElementMap = window.__pawbotElementMap || {};
  window.__pawbotRefCounter = window.__pawbotRefCounter || 0;

  // ── Role Detection ──────────────────────────────────────────────
  function getRole(el) {
    const explicit = el.getAttribute("role");
    if (explicit) return explicit;

    const tag = el.tagName.toLowerCase();
    const type = el.getAttribute("type");

    const roleMap = {
      a: "link", button: "button", select: "combobox",
      textarea: "textbox", nav: "navigation", main: "main",
      header: "banner", footer: "contentinfo", section: "region",
      article: "article", aside: "complementary", form: "form",
      table: "table", ul: "list", ol: "list", li: "listitem",
      label: "label", img: "image",
      h1: "heading", h2: "heading", h3: "heading",
      h4: "heading", h5: "heading", h6: "heading",
    };

    if (tag === "input") {
      if (type === "submit" || type === "button") return "button";
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (type === "file") return "button";
      return "textbox";
    }
    return roleMap[tag] || "generic";
  }

  // ── Label Extraction ────────────────────────────────────────────
  function getLabel(el) {
    const tag = el.tagName.toLowerCase();

    // Select: show selected option text
    if (tag === "select") {
      const sel = el;
      const opt = sel.querySelector("option[selected]") || sel.options[sel.selectedIndex];
      if (opt?.textContent) return opt.textContent.trim();
    }

    // Priority chain
    const ariaLabel = el.getAttribute("aria-label");
    if (ariaLabel?.trim()) return ariaLabel.trim();

    const placeholder = el.getAttribute("placeholder");
    if (placeholder?.trim()) return placeholder.trim();

    const title = el.getAttribute("title");
    if (title?.trim()) return title.trim();

    const alt = el.getAttribute("alt");
    if (alt?.trim()) return alt.trim();

    // Associated <label for="id">
    if (el.id) {
      const lbl = document.querySelector(`label[for="${el.id}"]`);
      if (lbl?.textContent?.trim()) return lbl.textContent.trim();
    }

    // Input value (short)
    if (tag === "input") {
      const type = el.getAttribute("type") || "";
      const value = el.getAttribute("value");
      if (type === "submit" && value?.trim()) return value.trim();
      if (el.value && el.value.length < 50 && el.value.trim())
        return el.value.trim();
    }

    // Button / link direct text
    if (["button", "a", "summary"].includes(tag)) {
      let text = "";
      for (let i = 0; i < el.childNodes.length; i++) {
        if (el.childNodes[i].nodeType === Node.TEXT_NODE)
          text += el.childNodes[i].textContent;
      }
      if (text.trim()) return text.trim();
    }

    // Heading text
    if (tag.match(/^h[1-6]$/)) {
      const text = el.textContent;
      if (text?.trim()) return text.trim().substring(0, 100);
    }

    // Generic text content (3+ chars)
    let directText = "";
    for (let i = 0; i < el.childNodes.length; i++) {
      if (el.childNodes[i].nodeType === Node.TEXT_NODE)
        directText += el.childNodes[i].textContent;
    }
    if (directText?.trim()?.length >= 3) {
      const t = directText.trim();
      return t.length > 100 ? t.substring(0, 100) + "..." : t;
    }
    return "";
  }

  // ── Visibility & Relevance ─────────────────────────────────────
  function isVisible(el) {
    const s = window.getComputedStyle(el);
    return s.display !== "none" && s.visibility !== "hidden"
      && s.opacity !== "0" && el.offsetWidth > 0 && el.offsetHeight > 0;
  }

  function isInteractive(el) {
    const tag = el.tagName.toLowerCase();
    return ["a","button","input","select","textarea","details","summary"]
      .includes(tag) || el.getAttribute("onclick") != null
      || el.getAttribute("tabindex") != null
      || el.getAttribute("role") === "button"
      || el.getAttribute("role") === "link"
      || el.getAttribute("contenteditable") === "true";
  }

  function isRelevant(el, opts) {
    const tag = el.tagName.toLowerCase();
    if (["script","style","meta","link","title","noscript"].includes(tag))
      return false;
    if (opts.filter !== "all" && el.getAttribute("aria-hidden") === "true")
      return false;
    if (opts.filter !== "all" && !isVisible(el)) return false;

    // Viewport check (skip off-screen unless focused element)
    if (opts.filter !== "all" && !opts.refId) {
      const rect = el.getBoundingClientRect();
      if (!(rect.top < window.innerHeight && rect.bottom > 0
        && rect.left < window.innerWidth && rect.right > 0))
        return false;
    }

    if (opts.filter === "interactive") return isInteractive(el);
    if (isInteractive(el)) return true;
    if (getLabel(el).length > 0) return true;

    const role = getRole(el);
    return role != null && role !== "generic" && role !== "image";
  }

  // ── Tree Builder ────────────────────────────────────────────────
  window.__generateAccessibilityTree = function (filter, depth, maxChars, refId) {
    try {
      const lines = [];
      const maxDepth = depth != null ? depth : 15;
      const opts = { filter: filter || "all", refId: refId };

      function walk(el, level) {
        if (level > maxDepth || !el || !el.tagName) return;

        const relevant = isRelevant(el, opts) || (refId != null && level === 0);
        if (relevant) {
          const role = getRole(el);
          const label = getLabel(el);

          // Get or create ref_id
          let ref = null;
          for (const key in window.__pawbotElementMap) {
            if (window.__pawbotElementMap[key].deref() === el) {
              ref = key; break;
            }
          }
          if (!ref) {
            ref = "ref_" + (++window.__pawbotRefCounter);
            window.__pawbotElementMap[ref] = new WeakRef(el);
          }

          // Build line
          let line = " ".repeat(level) + role;
          if (label) {
            line += ` "${label.replace(/\s+/g, " ").substring(0, 100).replace(/"/g, '\\"')}"`;
          }
          line += ` [${ref}]`;

          // Attributes
          if (el.getAttribute("href")) line += ` href="${el.getAttribute("href")}"`;
          if (el.getAttribute("type")) line += ` type="${el.getAttribute("type")}"`;
          if (el.getAttribute("placeholder"))
            line += ` placeholder="${el.getAttribute("placeholder")}"`;

          lines.push(line);

          // Select options
          if (el.tagName.toLowerCase() === "select") {
            for (let i = 0; i < el.options.length; i++) {
              const opt = el.options[i];
              let optLine = " ".repeat(level + 1) + "option";
              const text = opt.textContent?.trim() || "";
              if (text) optLine += ` "${text.replace(/\s+/g," ").substring(0,100)}"`;
              if (opt.selected) optLine += " (selected)";
              lines.push(optLine);
            }
          }
        }

        // Recurse children
        if (el.children && level < maxDepth) {
          for (let i = 0; i < el.children.length; i++) {
            walk(el.children[i], relevant ? level + 1 : level);
          }
        }
      }

      // Start traversal
      if (refId) {
        const ref = window.__pawbotElementMap[refId];
        if (!ref) return {
          error: `Element '${refId}' not found. Use read_page without ref_id to refresh.`,
          pageContent: "", viewport: { width: window.innerWidth, height: window.innerHeight }
        };
        const el = ref.deref();
        if (!el) return {
          error: `Element '${refId}' no longer exists. Page may have changed.`,
          pageContent: "", viewport: { width: window.innerWidth, height: window.innerHeight }
        };
        walk(el, 0);
      } else if (document.body) {
        walk(document.body, 0);
      }

      // Cleanup stale refs
      for (const key in window.__pawbotElementMap) {
        if (!window.__pawbotElementMap[key].deref())
          delete window.__pawbotElementMap[key];
      }

      const content = lines.join("\n");
      if (maxChars != null && content.length > maxChars) {
        return {
          error: `Output exceeds ${maxChars} char limit (${content.length} chars). Use depth or ref_id to narrow.`,
          pageContent: "", viewport: { width: window.innerWidth, height: window.innerHeight }
        };
      }

      return {
        pageContent: content,
        viewport: { width: window.innerWidth, height: window.innerHeight }
      };
    } catch (e) {
      throw new Error("Accessibility tree error: " + (e.message || "Unknown"));
    }
  };
})();
```

---

## 16.2 — Advanced Element Interaction Tools

Add these to the service worker tool router:

```javascript
// ── Enhanced Click with CDP (for complex interactions) ────────────
async function toolClickAdvanced(args) {
  const tabId = await getTargetTabId(args);
  const debugTarget = { tabId };

  await chrome.debugger.attach(debugTarget, "1.3");
  try {
    if (args.coordinate) {
      const [x, y] = args.coordinate;
      await chrome.debugger.sendCommand(debugTarget, "Input.dispatchMouseEvent", {
        type: "mousePressed", x, y, button: "left", clickCount: 1
      });
      await chrome.debugger.sendCommand(debugTarget, "Input.dispatchMouseEvent", {
        type: "mouseReleased", x, y, button: "left", clickCount: 1
      });
    }
    return { content: JSON.stringify({ success: true }) };
  } finally {
    await chrome.debugger.detach(debugTarget);
  }
}

// ── Enhanced Type with CDP (keystroke-level input) ────────────────
async function toolTypeAdvanced(args) {
  const tabId = await getTargetTabId(args);
  const debugTarget = { tabId };

  await chrome.debugger.attach(debugTarget, "1.3");
  try {
    for (const char of args.text) {
      await chrome.debugger.sendCommand(debugTarget,
        "Input.dispatchKeyEvent", {
          type: "keyDown", text: char,
          key: char, code: `Key${char.toUpperCase()}`,
        });
      await chrome.debugger.sendCommand(debugTarget,
        "Input.dispatchKeyEvent", {
          type: "keyUp", key: char,
        });
      // Human-like delay
      await new Promise(r => setTimeout(r, 20 + Math.random() * 40));
    }
    return { content: JSON.stringify({ success: true, chars: args.text.length }) };
  } finally {
    await chrome.debugger.detach(debugTarget);
  }
}

// ── Extract Structured Data ───────────────────────────────────────
async function toolExtract(args) {
  const tabId = await getTargetTabId(args);
  const what = args.what || "text";

  const extractors = {
    text: `document.body.innerText.substring(0, 5000)`,
    links: `JSON.stringify(Array.from(document.querySelectorAll('a'))
      .map(a => ({text: a.innerText.trim(), href: a.href}))
      .filter(l => l.href && l.text).slice(0, 100))`,
    tables: `JSON.stringify(Array.from(document.querySelectorAll('table'))
      .map(t => Array.from(t.querySelectorAll('tr'))
        .map(r => Array.from(r.querySelectorAll('td,th'))
          .map(c => c.innerText.trim()))))`,
    forms: `JSON.stringify(Array.from(document.querySelectorAll('input,select,textarea'))
      .map(el => ({name: el.name||el.id, type: el.type, value: el.value,
        required: el.required})))`,
    metadata: `JSON.stringify({
      title: document.title,
      url: location.href,
      description: document.querySelector('meta[name=description]')?.content,
      canonical: document.querySelector('link[rel=canonical]')?.href
    })`,
  };

  const script = extractors[what] || extractors.text;
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func: new Function(`return ${script}`),
  });
  return { content: results[0]?.result || "" };
}
```

---

## 16.3 — PawBot Agent Tool Definitions

**Create:** `pawbot/tools/browser_chrome.py`

```python
"""Browser tools that use the real Chrome browser via the extension."""

from __future__ import annotations
from typing import Any


CHROME_TOOLS = [
    {
        "name": "chrome_read_page",
        "description": "Read the current page as an accessibility tree. Returns structured text with [ref_id] for each element. Use filter='interactive' to see only clickable/typeable elements.",
        "parameters": {
            "filter": {"type": "string", "enum": ["all", "interactive"], "default": "all"},
            "depth": {"type": "integer", "default": 10},
            "ref_id": {"type": "string", "description": "Focus on a specific element subtree"},
        },
    },
    {
        "name": "chrome_click",
        "description": "Click an element in the user's browser. Use ref_id from read_page, or coordinate [x, y].",
        "parameters": {
            "ref_id": {"type": "string"},
            "coordinate": {"type": "array", "items": {"type": "integer"}},
        },
    },
    {
        "name": "chrome_type",
        "description": "Type text into a form field. Use ref_id from read_page.",
        "parameters": {
            "ref_id": {"type": "string", "required": True},
            "text": {"type": "string", "required": True},
        },
    },
    {
        "name": "chrome_navigate",
        "description": "Navigate the active tab to a URL.",
        "parameters": {
            "url": {"type": "string", "required": True},
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
            "tabId": {"type": "integer", "required": True},
        },
    },
    {
        "name": "chrome_extract",
        "description": "Extract structured data: text, links, tables, forms, metadata.",
        "parameters": {
            "what": {"type": "string", "enum": ["text","links","tables","forms","metadata"]},
        },
    },
]
```

---

## Verification Checklist — Phase 16

- [ ] Accessibility tree script injects into all pages and all iframes
- [ ] `read_page` returns structured tree with roles, labels, ref_ids
- [ ] `read_page(filter="interactive")` only shows clickable/typeable elements
- [ ] `read_page(ref_id="ref_5")` zooms into a single element
- [ ] WeakRef map auto-cleans when elements are removed from DOM
- [ ] `click(ref_id)` clicks real elements on the user's page
- [ ] `type(ref_id, text)` fills real form fields with event dispatch
- [ ] CDP-based click/type works for complex widgets (canvas, custom inputs)
- [ ] `extract(what="links")` returns all page links
- [ ] PawBot agent can use `chrome_*` tools in conversation
