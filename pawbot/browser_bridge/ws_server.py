"""WebSocket server for side panel ↔ PawBot communication (Phase 18.4).

Provides a WebSocket endpoint that the Chrome extension's side panel
connects to for real-time chat with the PawBot agent.

Protocol:
  Panel → Server: {type: "user_message", content: "...", tabId: "..."}
  Server → Panel: {type: "agent_response", content: "..."}
  Server → Panel: {type: "tool_use", tool: "chrome_read_page"}
  Server → Panel: {type: "agent_active", active: true}
  Server → Panel: {type: "error", content: "..."}
  Server → Panel: {type: "status", content: "..."}
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Awaitable, Set

from loguru import logger

try:
    import websockets
except ImportError:
    websockets = None


class PanelWebSocketServer:
    """Serves WebSocket connections from the Chrome extension side panel.

    Attributes:
        host: Bind address (default: 127.0.0.1)
        port: Bind port (default: 8765)
        clients: Active WebSocket connections
    """

    def __init__(
        self,
        agent_callback: Callable[[str, str | None], Awaitable[str]] | None = None,
        host: str = "127.0.0.1",
        port: int = 8765,
    ):
        """Initialize the WebSocket server.

        Args:
            agent_callback: async function(content, tab_id) → response string
            host: Bind address
            port: Bind port
        """
        self.agent_callback = agent_callback
        self.host = host
        self.port = port
        self.clients: Set[Any] = set()
        self._server = None
        self._running = False

    async def handler(self, websocket: Any, path: str = "") -> None:
        """Handle a single WebSocket connection from the side panel."""
        self.clients.add(websocket)
        client_id = id(websocket)
        logger.debug("Panel client connected: {}", client_id)

        try:
            # Send welcome message
            await websocket.send(json.dumps({
                "type": "status",
                "content": "Connected to PawBot agent",
            }))

            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                    await self._handle_message(websocket, msg)
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({
                        "type": "error",
                        "content": "Invalid JSON",
                    }))
        except Exception as e:
            logger.debug("Panel WS error (client {}): {}", client_id, e)
        finally:
            self.clients.discard(websocket)
            logger.debug("Panel client disconnected: {}", client_id)

    async def _handle_message(self, websocket: Any, msg: dict[str, Any]) -> None:
        """Process a message from the side panel."""
        msg_type = msg.get("type")

        if msg_type == "user_message":
            content = msg.get("content", "")
            tab_id = msg.get("tabId")

            if not content:
                await websocket.send(json.dumps({
                    "type": "error",
                    "content": "Empty message",
                }))
                return

            # Notify panel that agent is working
            await websocket.send(json.dumps({
                "type": "agent_active",
                "active": True,
            }))

            try:
                if self.agent_callback:
                    response = await self.agent_callback(content, tab_id)
                else:
                    response = f"Echo: {content}"

                await websocket.send(json.dumps({
                    "type": "agent_response",
                    "content": response,
                }))
            except Exception as e:
                logger.error("Agent callback error: {}", e)
                await websocket.send(json.dumps({
                    "type": "error",
                    "content": str(e),
                }))
            finally:
                await websocket.send(json.dumps({
                    "type": "agent_active",
                    "active": False,
                }))

        elif msg_type == "init":
            logger.debug("Panel initialized (tabId: {})", msg.get("tabId"))

        elif msg_type == "ping":
            await websocket.send(json.dumps({"type": "pong"}))

    async def broadcast(self, msg: dict[str, Any]) -> None:
        """Send a message to all connected panel clients."""
        if not self.clients:
            return

        data = json.dumps(msg)
        disconnected = set()

        for ws in self.clients:
            try:
                await ws.send(data)
            except Exception:
                disconnected.add(ws)

        self.clients -= disconnected

    async def notify_tool_use(self, tool_name: str) -> None:
        """Notify panels that a tool is being used."""
        await self.broadcast({
            "type": "tool_use",
            "tool": tool_name,
        })

    async def start(self) -> None:
        """Start the WebSocket server."""
        if not websockets:
            logger.warning("websockets package not installed — side panel disabled")
            return

        self._running = True
        self._server = await websockets.serve(
            self.handler,
            self.host,
            self.port,
        )
        logger.info("Panel WebSocket server on ws://{}:{}", self.host, self.port)
        await self._server.wait_closed()

    async def stop(self) -> None:
        """Stop the WebSocket server."""
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            logger.info("Panel WebSocket server stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def client_count(self) -> int:
        return len(self.clients)
