"""
SentinelForge — Streamlit Frontend.

Real-time pentest interface with live thought streaming, attack graph
visualisation, and GPU monitoring.

Run:
    streamlit run frontend/app.py
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any

import streamlit as st
import requests

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from components.graph_view import render_graph
from components.gpu_monitor import render_gpu

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="SentinelForge — Автономный Пентест",
    page_icon="🛡️",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------
DEFAULTS: dict[str, Any] = {
    "ws_queue": None,        # queue.Queue — создаётся при запуске агента
    "messages": [],
    "thoughts": [],
    "graph": None,
    "running": False,
    "report": "",
    "current_step": 0,
    "max_steps": 12,
    "graph_revision": 0,
    "last_gpu_render": 0.0,
    "ws_thread": None,
}
for key, val in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = val

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BACKEND_URL = "http://127.0.0.1:8000"


# ---------------------------------------------------------------------------
# WebSocket listener (background thread → queue)
# ---------------------------------------------------------------------------

def _ws_listener(event_queue: queue.Queue):
    """
    Background thread: connect to the backend WebSocket and push
    parsed events into a thread-safe queue.  The main Streamlit thread
    reads from the queue to update session_state safely.
    """
    try:
        import websocket
    except ImportError:
        event_queue.put({"type": "error", "message": "websocket-client not installed"})
        return

    ws_url = BACKEND_URL.replace("http://", "ws://") + "/ws"

    try:
        ws = websocket.create_connection(ws_url, timeout=10)
        event_queue.put({"type": "status", "message": "WebSocket connected"})
    except Exception as exc:
        event_queue.put({"type": "error", "message": f"WebSocket connection failed: {exc}"})
        return

    ws.settimeout(2.0)

    try:
        while True:
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break

            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            event_queue.put(event)

            # Stop signal from backend
            if event.get("type") == "finished":
                break

    except Exception:
        pass
    finally:
        try:
            ws.close()
        except Exception:
            pass
        event_queue.put({"type": "_ws_closed"})


def _drain_queue():
    """Drain all pending events from the queue and apply to session_state."""
    q = st.session_state.ws_queue
    if q is None:
        return

    while True:
        try:
            event = q.get_nowait()
        except queue.Empty:
            break

        evt_type = event.get("type", "")

        if evt_type == "thought_token":
            token = event.get("token", "")
            if st.session_state.thoughts and st.session_state.thoughts[-1].get("complete", True):
                st.session_state.thoughts.append({"text": token, "complete": False})
            elif st.session_state.thoughts:
                st.session_state.thoughts[-1]["text"] += token
                st.session_state.thoughts[-1]["complete"] = False

        elif evt_type == "action_parsed":
            if st.session_state.thoughts:
                st.session_state.thoughts[-1]["complete"] = True
            st.session_state.thoughts.append({
                "text": f"\n🔧 **Action:** `{event.get('action')}`",
                "complete": True,
            })
            st.session_state.current_step = event.get("step", 0)

        elif evt_type == "graph_update":
            st.session_state.graph = event.get("graph")
            st.session_state.graph_revision += 1

        elif evt_type == "report":
            st.session_state.report = event.get("report", "")

        elif evt_type == "finished":
            st.session_state.running = False
            st.session_state.report = event.get("report", "")
            st.session_state.graph = event.get("graph")
            st.session_state.graph_revision += 1

        elif evt_type == "error":
            st.session_state.thoughts.append({
                "text": f"\n❌ **Ошибка:** {event.get('message')}",
                "complete": True,
            })

        elif evt_type == "status":
            msg = event.get("message", "")
            if "cancel" in msg.lower() or "stop" in msg.lower():
                pass  # don't auto-stop on status messages

        elif evt_type == "_ws_closed":
            st.session_state.running = False


def start_ws_thread():
    """Launch the WebSocket listener in a daemon thread with a fresh queue."""
    if st.session_state.ws_queue is None:
        st.session_state.ws_queue = queue.Queue()
    else:
        # Drain old queue before starting fresh
        _drain_queue()

    # Only start if no existing thread
    existing = st.session_state.get("ws_thread")
    if existing and existing.is_alive():
        return

    t = threading.Thread(
        target=_ws_listener,
        args=(st.session_state.ws_queue,),
        name="ws-listener",
        daemon=True,
    )
    t.start()
    st.session_state.ws_thread = t


def stop_agent_on_backend():
    """Send POST /stop to cancel the running agent."""
    try:
        resp = requests.post(f"{BACKEND_URL}/stop", timeout=3)
        return resp.json()
    except Exception:
        return {"status": "error", "message": "Failed to reach backend"}


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
col_title, col_logo = st.columns([5, 1])
with col_title:
    st.title("🛡️ SentinelForge")
    st.caption("Автономный пентест-агент · AMD ROCm · Полностью локально")

# ---------------------------------------------------------------------------
# Sidebar — GPU Monitor (throttled to once per 3 seconds)
# ---------------------------------------------------------------------------
now = time.time()
if now - st.session_state.last_gpu_render > 3.0:
    render_gpu()
    st.session_state.last_gpu_render = now

st.sidebar.markdown("---")
st.sidebar.markdown("## ⚙️ Настройки")

api_target = st.sidebar.text_input(
    "Backend URL",
    value=BACKEND_URL,
    help="Адрес FastAPI-сервера",
)

# ---------------------------------------------------------------------------
# Main layout: left (thoughts) + right (graph)
# ---------------------------------------------------------------------------
left, right = st.columns([1, 2])

with left:
    st.subheader("🎯 Цель")
    target_ip = st.text_input("Target IP", value="192.168.1.100", key="target_input")

    col1, col2 = st.columns(2)
    with col1:
        launch_btn = st.button(
            "🚀 Launch Attack",
            type="primary",
            disabled=st.session_state.running,
            use_container_width=True,
        )
    with col2:
        stop_btn = st.button(
            "🛑 Stop",
            disabled=not st.session_state.running,
            use_container_width=True,
        )
        if stop_btn:
            stop_agent_on_backend()
            st.session_state.running = False
            st.toast("Агент остановлен", icon="🛑")
            st.rerun()

    # Progress bar
    if st.session_state.running:
        prog = st.session_state.current_step / max(st.session_state.max_steps, 1)
        st.progress(min(prog, 1.0), text=f"Шаг {st.session_state.current_step}/{st.session_state.max_steps}")

    # Thought stream
    st.subheader("🧠 Ход мыслей агента")
    thought_container = st.container(height=500)

    with thought_container:
        if not st.session_state.thoughts:
            st.info("Мысли агента появятся здесь после запуска атаки...")
        else:
            for thought in st.session_state.thoughts:
                if not thought["complete"]:
                    st.markdown(thought["text"] + "▌")
                else:
                    st.markdown(thought["text"])

with right:
    st.subheader("🔗 Граф атаки")
    render_graph(st.session_state.graph)

    # Final report
    if st.session_state.report:
        with st.expander("📄 Финальный отчёт", expanded=True):
            st.markdown(st.session_state.report)

# ---------------------------------------------------------------------------
# Launch logic
# ---------------------------------------------------------------------------
if launch_btn and not st.session_state.running:
    target = target_ip.strip()
    if not target:
        st.error("Введите IP-адрес цели.")
    else:
        st.session_state.running = True
        st.session_state.thoughts = []
        st.session_state.graph = None
        st.session_state.report = ""
        st.session_state.current_step = 0
        st.session_state.graph_revision = 0

        start_ws_thread()
        time.sleep(1.0)  # give WebSocket time to connect

        try:
            resp = requests.post(f"{api_target}/run", json={"target": target}, timeout=10)
            if resp.status_code == 200:
                body = resp.json()
                if body.get("status") == "busy":
                    st.warning("Агент уже запущен. Остановите текущую сессию.")
                    st.session_state.running = False
                else:
                    st.toast(f"✅ Агент запущен — цель: {target}", icon="🚀")
            else:
                body = resp.json()
                st.error(f"Ошибка запуска: {body.get('message', resp.text)}")
                st.session_state.running = False
        except requests.ConnectionError:
            st.error(f"Не удалось подключиться к бэкенду ({api_target}). Запустите сервер.")
            st.session_state.running = False
        except Exception as exc:
            st.error(f"Ошибка: {exc}")
            st.session_state.running = False

        st.rerun()

# ---------------------------------------------------------------------------
# Drain WS queue and auto-refresh while running (poll-based, no busy-loop)
# ---------------------------------------------------------------------------
if st.session_state.running:
    _drain_queue()
    time.sleep(0.8)
    st.rerun()
