"""
SentinelForge — GPU Monitor Component.

Displays AMD GPU metrics in the Streamlit sidebar.
On systems without ROCm, shows a clear status message instead of fake data.
On ROCm Linux, calls rocm-smi for real metrics.
"""

from __future__ import annotations

import json
import logging
import platform
import subprocess
import sys
from typing import Any

logger = logging.getLogger(__name__)


def _call_rocm_smi() -> dict[str, Any] | None:
    """
    Call rocm-smi --json and return parsed data, or None if unavailable.
    ROCm is Linux-only; returns None on other platforms.
    """
    if sys.platform != "linux":
        return None

    try:
        result = subprocess.run(
            ["rocm-smi", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except FileNotFoundError:
        logger.debug("rocm-smi not found")
    except json.JSONDecodeError:
        logger.debug("rocm-smi returned non-JSON output")
    except Exception:
        logger.debug("rocm-smi call failed", exc_info=True)
    return None


def render_gpu() -> None:
    """
    Render GPU metrics in the Streamlit sidebar.

    - On Linux with ROCm: shows real GPU utilisation, memory, temperature, power.
    - On other platforms or without ROCm: shows a clear status message.
    """
    import streamlit as st

    st.sidebar.markdown("## 🖥️ GPU Monitor")

    data = _call_rocm_smi()

    if data is None:
        if sys.platform == "linux":
            st.sidebar.info(
                "ROCm не обнаружен.\n\n"
                "Установите ROCm и драйвер AMD GPU для отображения метрик.\n"
                "`sudo bash infra/install_rocm.sh`"
            )
        else:
            st.sidebar.info(
                f"GPU-монитор недоступен на {platform.system()}.\n\n"
                "Метрики AMD GPU отображаются только на Linux с установленным ROCm.\n"
                "При развёртывании на AMD Developer Cloud монитор заработает автоматически."
            )
        return

    for card_name, card_info in data.items():
        if isinstance(card_info, dict):
            gpu_use = card_info.get("GPU use (%)", "N/A")
            mem_use = card_info.get("GPU memory use (%)", "N/A")
            temp = card_info.get("Temperature (°C)", "N/A")
            power = card_info.get("Power (W)", "N/A")

            st.sidebar.markdown(f"**{card_name}**")
            st.sidebar.metric("Загрузка GPU", f"{gpu_use}%")
            st.sidebar.metric("Видеопамять", f"{mem_use}%")
            st.sidebar.metric("Температура", f"{temp}°C")
            st.sidebar.metric("Потребление", f"{power}W")
            st.sidebar.divider()
