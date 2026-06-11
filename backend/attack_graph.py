"""
SentinelForge — Attack Graph.

Maintains a directed graph of the attack progression using NetworkX.
Every tool invocation becomes a node; edges connect sequential steps.
Exposed as node-link JSON for Cytoscape rendering in the frontend.
"""

import logging
import time
from typing import Any

import networkx as nx

logger = logging.getLogger(__name__)


class AttackGraph:
    """
    Tracks the pentest kill-chain as a directed graph.

    Each node represents a step (action + result). Edges show
    the order of execution.

    Usage:
        graph = AttackGraph()
        graph.add_step("1", "nmap_scan", {"target": "..."}, "Open ports: 22, 80")
        graph.add_step("2", "bruteforce_ssh", {}, "Credentials found")
        cytoscape_json = graph.to_dict()
    """

    def __init__(self):
        self._graph = nx.DiGraph()
        self._previous_id: str | None = None

    def add_step(
        self,
        step_id: str,
        action: str,
        action_input: Any,
        observation: str,
        status: str = "success",
    ) -> None:
        """
        Add a node to the attack graph and connect it to the previous step.

        Args:
            step_id: unique identifier for this step (e.g. 'step_3').
            action: tool name (nmap_scan, bruteforce_ssh, FINISH, etc.).
            action_input: the parameters passed to the tool.
            observation: truncated result / summary returned by the tool.
            status: 'success', 'error', or 'pending'.
        """
        label = action
        if action == "FINISH":
            label = "🏁 FINISH"
        elif status == "error":
            label = f"❌ {action}"
        else:
            label = f"🔧 {action}"

        # Truncate observation for display
        short_obs = observation[:120] + "..." if len(observation) > 120 else observation

        self._graph.add_node(
            step_id,
            label=label,
            action=action,
            input=str(action_input)[:200],
            observation=short_obs,
            status=status,
            timestamp=time.time(),
        )

        if self._previous_id is not None:
            self._graph.add_edge(self._previous_id, step_id)

        self._previous_id = step_id
        logger.debug("Graph step added: %s → %s", step_id, action)

    def to_dict(self) -> dict:
        """
        Return the graph as a node-link dictionary suitable for
        serialisation to JSON and consumption by Cytoscape.js.
        """
        return nx.node_link_data(self._graph)
