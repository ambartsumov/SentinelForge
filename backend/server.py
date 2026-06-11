"""
SentinelForge — FastAPI Server.

Provides REST + WebSocket interface for the pentest agent.
POST /run    — launches an async agent task.
POST /stop   — cancels the running agent.
GET  /status — check if an agent is running.
WS   /ws     — streams real-time events to frontend clients (supports multiple).
"""

import asyncio
import json
import logging
import signal
import sys
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .agent_core import SentinelAgent
from .utils import get_config, setup_logging

setup_logging()
logger = logging.getLogger(__name__)

config = get_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle for the FastAPI app."""
    _install_signal_handlers()
    yield
    await _shutdown()


app = FastAPI(title="SentinelForge API", version="0.3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# WebSocket connection manager (multiple clients)
# ---------------------------------------------------------------------------

class ConnectionManager:
    """Manages multiple WebSocket connections with broadcast to all."""

    def __init__(self):
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)
        logger.info("WebSocket client connected (total: %d)", len(self._connections))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(ws)
        logger.info("WebSocket client disconnected (total: %d)", len(self._connections))

    async def broadcast(self, event: dict[str, Any]) -> None:
        """Send a JSON event to ALL connected clients. Silently drops dead connections."""
        async with self._lock:
            connections = list(self._connections)

        dead: list[WebSocket] = []
        for ws in connections:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)

    @property
    def connected_count(self) -> int:
        return len(self._connections)


manager = ConnectionManager()

# ---------------------------------------------------------------------------
# Agent lifecycle (single agent at a time)
# ---------------------------------------------------------------------------

_agent_task: asyncio.Task | None = None
_agent_instance: SentinelAgent | None = None
_cancel_event: asyncio.Event | None = None
_lock = asyncio.Lock()


async def _run_agent(target: str):
    """Background coroutine that creates and runs the agent, broadcasting all events."""
    global _agent_instance, _cancel_event
    try:
        _cancel_event = asyncio.Event()
        _agent_instance = SentinelAgent(
            target=target,
            broadcast_callback=manager.broadcast,
            cancel_event=_cancel_event,
        )
        result = await _agent_instance.run()
        logger.info("Agent finished: %s", result.get("report", "")[:200])
    except Exception as exc:
        logger.exception("Agent crashed: %s", exc)
        await manager.broadcast({
            "type": "error",
            "message": f"Agent crashed: {exc}",
        })
    finally:
        _agent_instance = None
        _cancel_event = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    target: str


@app.get("/health")
async def health():
    return {"status": "ok", "service": "SentinelForge", "version": "0.3.0"}


@app.post("/run")
async def run_agent(req: RunRequest):
    """Launch a new pentest agent against the given target."""
    global _agent_task

    async with _lock:
        if _agent_task and not _agent_task.done():
            return {"status": "busy", "message": "An agent is already running. Stop it first or wait."}

        logger.info("Launching agent for target=%s", req.target)
        _agent_task = asyncio.create_task(_run_agent(req.target))

    return {"status": "started", "target": req.target}


@app.post("/stop")
async def stop_agent():
    """Cancel the currently running agent."""
    global _agent_instance

    if _agent_instance is None:
        return {"status": "idle", "message": "No agent is running."}

    logger.info("Stopping agent...")
    _agent_instance.cancel()

    await manager.broadcast({
        "type": "status",
        "message": "Agent stop requested — shutting down...",
    })

    return {"status": "stopping", "message": "Agent cancellation requested."}


@app.get("/status")
async def agent_status():
    """Check if an agent is currently running."""
    if _agent_task and not _agent_task.done():
        return {"running": True}
    return {"running": False}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket endpoint for real-time event streaming."""
    await manager.connect(ws)
    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("command") == "stop":
                    if _agent_instance:
                        _agent_instance.cancel()
                        await manager.broadcast({
                            "type": "status",
                            "message": "Stop command received from client.",
                        })
                    else:
                        await ws.send_json({
                            "type": "status",
                            "message": "No agent is running.",
                        })
                logger.debug("WS message from client: %s", msg)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected normally")
    except Exception as exc:
        logger.warning("WebSocket error: %s", exc)
    finally:
        await manager.disconnect(ws)


# ---------------------------------------------------------------------------
# Graceful shutdown — cross-platform (Windows + Unix)
# ---------------------------------------------------------------------------

async def _shutdown():
    """Gracefully cancel the agent and stop the server."""
    logger.info("Initiating graceful shutdown...")
    if _agent_instance:
        _agent_instance.cancel()
    # Give agent a moment to clean up
    await asyncio.sleep(0.5)


def _install_signal_handlers():
    """Install OS-appropriate signal handlers."""
    if sys.platform == "win32":
        logger.info("Running on Windows — using atexit for cleanup")
    else:
        try:
            loop = asyncio.get_event_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown()))
                except NotImplementedError:
                    pass
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.server:app",
        host=config["host"],
        port=config["port"],
        reload=False,
    )
