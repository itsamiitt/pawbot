# Phase 4 — Channel Maturity

> **Goal:** Tier channels by production-readiness, make Tier 1 bulletproof.  
> **Duration:** 10-14 days  
> **Risk Level:** Medium (channel behavior changes, but per-channel isolation)  
> **Depends On:** Phase 0 (typed exceptions), Phase 1 (retry patterns)

---

## 4.1 — Channel Tiering Strategy

### Current State
12 channel adapters exist but none are production-hardened. All share the same code quality bar, which means none get enough attention.

### Tier Assignment

| Tier | Channels | Quality Target | Effort |
|------|----------|----------------|--------|
| **Tier 1 (Production)** | Telegram, WhatsApp, CLI | 99.9% uptime, auto-reconnect, rich media | High |
| **Tier 2 (Supported)** | Discord, Slack | Basic reliability, auto-reconnect | Medium |
| **Tier 3 (Community)** | Email, Matrix, Feishu, DingTalk, QQ, Mochat | "Works but experimental" | Low |

### Implementation: Mark channel tiers in config/schema.py

```python
# Add to each channel config class:
class TelegramConfig(BaseModel):
    """Telegram channel configuration."""
    enabled: bool = False
    token: str = ""
    allowed_users: list[str] = Field(default_factory=list)
    webhook_url: str = ""           # NEW: webhook mode support
    webhook_port: int = 8443        # NEW: webhook port
    use_webhook: bool = False       # NEW: webhook vs polling
    auto_reconnect: bool = True     # NEW: auto-reconnect on failure
    max_reconnect_delay: int = 300  # NEW: max backoff seconds
    tier: str = "production"        # Informational only
```

---

## 4.2 — Telegram: Production Hardening

### Problem
Current Telegram adapter uses polling only, has no webhook support, and doesn't handle disconnections gracefully.

### Fix: Auto-reconnect with exponential backoff

```python
# In pawbot/channels/telegram.py:

class TelegramChannel(BaseChannel):
    """Production-grade Telegram channel with auto-reconnect."""

    def __init__(self, config, bus, memory_router=None):
        super().__init__(config, bus, memory_router)
        self._reconnect_delay = 1  # Start at 1 second
        self._max_reconnect_delay = config.max_reconnect_delay if hasattr(config, 'max_reconnect_delay') else 300
        self._reconnect_count = 0

    async def start(self) -> None:
        """Start with auto-reconnect loop."""
        while self._running:
            try:
                self._reconnect_delay = 1  # Reset on successful connection
                await self._run_polling()
            except Exception as e:
                self._reconnect_count += 1
                logger.warning(
                    "Telegram disconnected (attempt {}): {} — reconnecting in {}s",
                    self._reconnect_count, e, self._reconnect_delay,
                )
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2,
                    self._max_reconnect_delay,
                )

    async def _run_polling(self) -> None:
        """Run the Telegram polling loop."""
        from telegram import Update
        from telegram.ext import (
            Application,
            MessageHandler,
            filters,
        )

        app = Application.builder().token(self.config.token).build()
        
        async def handle_message(update: Update, context) -> None:
            if update.message is None:
                return
            
            sender_id = str(update.message.from_user.id)
            chat_id = str(update.message.chat_id)
            
            # Permission check
            if not self.is_allowed(sender_id):
                return

            content = update.message.text or ""
            
            # Handle voice messages
            if update.message.voice:
                content = await self._transcribe_voice(update.message.voice)
            
            # Handle photos
            media = []
            if update.message.photo:
                photo = update.message.photo[-1]  # Highest resolution
                file = await context.bot.get_file(photo.file_id)
                media.append(file.file_path)

            await self._handle_message(
                sender_id=sender_id,
                chat_id=chat_id,
                content=content,
                media=media or None,
                metadata={
                    "message_id": str(update.message.message_id),
                    "chat_type": update.message.chat.type,
                },
            )

        app.add_handler(MessageHandler(filters.ALL, handle_message))
        
        async with app:
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            
            # Wait until stopped
            while self._running:
                await asyncio.sleep(1)
            
            await app.updater.stop()
            await app.stop()
```

---

## 4.3 — WhatsApp: Bridge Reconnection Fix

### Problem
WhatsApp bridge requires manual restart after disconnection.

### Fix

```python
# In pawbot/channels/whatsapp.py:

class WhatsAppChannel(BaseChannel):
    """WhatsApp channel with bridge health monitoring."""

    async def start(self) -> None:
        """Start with health monitoring loop."""
        while self._running:
            try:
                # Check bridge health before connecting
                if not await self._check_bridge_health():
                    logger.warning("WhatsApp bridge not responding, waiting...")
                    await asyncio.sleep(10)
                    continue
                
                await self._connect_websocket()
            except Exception as e:
                logger.warning("WhatsApp connection lost: {} — reconnecting in 5s", e)
                await asyncio.sleep(5)

    async def _check_bridge_health(self) -> bool:
        """Check if the WhatsApp bridge is running and responsive."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.config.bridge_url}/health")
                return r.status_code == 200
        except Exception:
            return False

    async def _connect_websocket(self) -> None:
        """Connect to bridge WebSocket with heartbeat."""
        import websockets
        
        ws_url = self.config.bridge_url.replace("http", "ws") + "/ws"
        
        async with websockets.connect(ws_url, ping_interval=30, ping_timeout=10) as ws:
            logger.info("WhatsApp bridge connected")
            
            async for raw in ws:
                if not self._running:
                    break
                try:
                    data = json.loads(raw)
                    await self._process_bridge_message(data)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from WhatsApp bridge")
                except Exception:
                    logger.exception("Error processing WhatsApp message")
```

---

## 4.4 — CLI Improvements

### Problem
CLI mode has no JSON output for scripting, no pipe support, no streaming flag.

### Solution

```python
# Update agent_commands.py (from Phase 0):

@agent_app.command(name="ask")
def agent_ask(
    message: str = typer.Argument(..., help="Message to send"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    stream: bool = typer.Option(False, "--stream", help="Stream response tokens"),
    session_id: str = typer.Option("cli:direct", "--session", "-s"),
):
    """Send a single message (non-interactive). Supports piping."""
    import sys
    
    # Support pipe input: echo "hello" | pawbot ask -
    if message == "-":
        if sys.stdin.isatty():
            console.print("[red]No pipe input detected. Provide a message or pipe input.[/red]")
            raise typer.Exit(1)
        message = sys.stdin.read().strip()

    # ... process message ...
    
    if json_output:
        import json
        print(json.dumps({
            "response": response,
            "session_id": session_id,
            "model": config.agents.defaults.model,
        }))
    else:
        _print_agent_response(response, render_markdown=True)
```

---

## 4.5 — Channel Health Reporting

### New endpoint: `/api/channels/health`

```python
# Add to dashboard/server.py:

@app.get("/api/channels/health")
def channels_health():
    """Per-channel health status."""
    cfg = _load_raw_config()
    channels = cfg.get("channels", {})
    
    health = {}
    for name, ch_cfg in channels.items():
        if not isinstance(ch_cfg, dict):
            continue
        health[name] = {
            "enabled": ch_cfg.get("enabled", False),
            "tier": _get_channel_tier(name),
            "connected": False,  # TODO: wire up to actual channel state
            "last_message": None,
            "messages_24h": 0,
        }
    
    return {"channels": health}


def _get_channel_tier(name: str) -> str:
    """Get tier classification for a channel."""
    tiers = {
        "telegram": "production",
        "whatsapp": "production",
        "cli": "production",
        "discord": "supported",
        "slack": "supported",
    }
    return tiers.get(name, "community")
```

---

## Verification Checklist — Phase 4 Complete

- [ ] Channel tiers documented in each channel config class
- [ ] Telegram auto-reconnects with exponential backoff
- [ ] Telegram supports both polling and webhook modes
- [ ] WhatsApp bridge health check prevents stale connections
- [ ] CLI supports `--json` output and pipe input
- [ ] `/api/channels/health` endpoint returns per-channel status
- [ ] Tier 3 channels have "experimental" warning in docs
- [ ] All tests pass: `pytest tests/ -v --tb=short`
