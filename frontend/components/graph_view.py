"""
SentinelForge — Graph View Component.

Converts node-link data from the backend into a Streamlit Cytoscape
visualisation showing the attack kill-chain in real time.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _node_link_to_cytoscape_elements(graph_dict: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Convert a NetworkX node-link dictionary into Cytoscape.js elements.

    Each node becomes a styled Cytoscape node; each link becomes an edge.
    """
    elements: list[dict[str, Any]] = []

    # Colour mapping by action type
    colour_map = {
        "nmap_scan": "#4A90D9",
        "bruteforce_ssh": "#E67E22",
        "msf_module": "#E74C3C",
        "FINISH": "#27AE60",
    }

    for node in graph_dict.get("nodes", []):
        node_id = node.get("id", "")
        node_data = node if isinstance(node, dict) else {}
        label = node_data.get("label", node_id)
        action = node_data.get("action", "")
        status = node_data.get("status", "")

        bg = colour_map.get(action, "#95A5A6")
        if status == "error":
            bg = "#C0392B"

        elements.append({
            "data": {
                "id": str(node_id),
                "label": label,
                "action": action,
                "observation": node_data.get("observation", "")[:80],
            },
            "style": {
                "background-color": bg,
                "color": "#ffffff",
                "text-valign": "center",
                "text-halign": "center",
                "font-size": "10px",
                "width": "label",
                "height": "label",
                "padding": "10px",
                "shape": "round-rectangle",
            },
        })

    for edge in graph_dict.get("edges", graph_dict.get("links", [])):
        elements.append({
            "data": {
                "id": f"{edge['source']}→{edge['target']}",
                "source": str(edge["source"]),
                "target": str(edge["target"]),
            },
        })

    return elements


def render_graph(graph_dict: dict[str, Any] | None) -> None:
    """
    Render the attack graph inside a Streamlit container using
    streamlit-cytoscape.

    Args:
        graph_dict: node-link dict from AttackGraph.to_dict(), or None.
    """
    import streamlit as st

    if graph_dict is None or not graph_dict.get("nodes"):
        st.info("Граф атаки будет отображаться здесь после запуска агента.")
        return

    try:
        from streamlit_cytoscape import cytoscape
    except ImportError:
        st.warning("streamlit-cytoscape не установлен. Выполните: pip install streamlit-cytoscape")
        st.json(graph_dict)
        return

    elements = _node_link_to_cytoscape_elements(graph_dict)

    cytoscape(
        elements,
        layout={"name": "breadthfirst", "directed": True, "spacingFactor": 1.2},
        stylesheet=[
            {
                "selector": "edge",
                "style": {
                    "width": 2,
                    "line-color": "#7F8C8D",
                    "target-arrow-color": "#7F8C8D",
                    "target-arrow-shape": "triangle",
                    "curve-style": "bezier",
                },
            },
            {
                "selector": "node:selected",
                "style": {"border-width": 3, "border-color": "#F1C40F"},
            },
        ],
        key="attack-graph",
        height="500px",
    )
