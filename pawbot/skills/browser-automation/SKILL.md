---
name: browser-automation
description: "Automate web browsers using Playwright via the browser MCP server. Navigate pages, click elements, fill forms, take screenshots, extract data, and handle anti-detection. Use when the user asks to scrape websites, automate web tasks, fill web forms, or interact with web pages programmatically."
metadata: {"pawbot":{"emoji":"🌐","requires":{}}}
---

# Browser Automation

Use the `browser` MCP server tools for Playwright-based browser automation with anti-detection.

## Core Tools

| Tool | Purpose |
|------|---------|
| `browser_navigate` | Open a URL |
| `browser_click` | Click an element (CSS selector or text) |
| `browser_type` | Type into an input field |
| `browser_screenshot` | Capture page screenshot |
| `browser_extract` | Extract text/data from page |
| `browser_evaluate` | Run JavaScript in page context |
| `browser_wait` | Wait for element/condition |
| `browser_scroll` | Scroll the page |

## Workflow: Scrape a Page

```
mcp_browser_browser_navigate(url="https://example.com")
mcp_browser_browser_wait(selector="h1", timeout=10)
mcp_browser_browser_screenshot()
mcp_browser_browser_extract(selector="main")
```

## Workflow: Fill a Form

```
mcp_browser_browser_navigate(url="https://example.com/login")
mcp_browser_browser_type(selector="#username", text="admin")
mcp_browser_browser_type(selector="#password", text="secret")
mcp_browser_browser_click(selector="button[type=submit]")
mcp_browser_browser_wait(selector=".dashboard", timeout=15)
```

## Anti-Detection

The browser server uses stealth mode by default:
- Randomized User-Agent strings
- WebGL fingerprint masking
- Realistic viewport sizes
- Human-like mouse movement and typing delays

## Tips

- Use CSS selectors for precision: `#id`, `.class`, `button[type=submit]`
- Use text selectors for simplicity: `text="Sign In"`
- Always `browser_wait` after navigation or clicks before extracting
- Take screenshots to verify page state when debugging
- Use `browser_evaluate` for complex DOM operations
