"""
End-to-end test: run the SentinelAgent with a mock LLM client.

Verifies the agent orchestrates a full kill-chain:
nmap → msf_module → FINISH using scripted LLM responses.
All tools are simulated (real=False), and the agent correctly
identifies simulated results.
"""

import sys
import os
import asyncio
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# Scripted LLM responses that simulate a realistic kill-chain
SCRIPTED_RESPONSES = [
    # Step 1: reconnaissance
    'Thought: Starting reconnaissance with nmap to discover open services.\n'
    'Action: nmap_scan\n'
    'Action Input: {"target": "10.0.0.5", "ports": "1-1000"}',

    # Step 2: after nmap, pick vsftpd exploit
    'Thought: nmap found port 21 with vsftpd 2.3.4 — this version has a known backdoor.\n'
    'Action: msf_module\n'
    'Action Input: {"module": "exploit/unix/ftp/vsftpd_234_backdoor", "target": "10.0.0.5"}',

    # Step 3: after exploit (which is simulated), agent notes SIMULATED and finishes
    'Thought: The msf_module result is marked [SIMULATED] — no actual exploitation was performed.\n'
    'The tool was not available on this system. Reporting findings honestly.\n'
    'Action: FINISH',
]


class MockStreamGenerator:
    """Simulates an LLM streaming response by yielding tokens from a script."""
    def __init__(self, text: str):
        self._text = text
        self._pos = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        # Yield 3-5 characters at a time like real streaming
        if self._pos >= len(self._text):
            raise StopAsyncIteration
        chunk = self._text[self._pos:self._pos + 4]
        self._pos += 4
        return chunk


async def _collect_events(agent):
    """Run the agent and collect all broadcast events for inspection."""
    events: list[dict] = []

    async def capture(event: dict):
        events.append(event)

    agent.broadcast = capture
    result = await agent.run()
    return result, events


def test_e2e_full_kill_chain():
    """Agent completes a full kill-chain with scripted LLM responses."""
    from backend.agent_core import SentinelAgent

    agent = SentinelAgent("10.0.0.5")

    # Mock the LLM stream to return scripted responses
    call_count = [0]

    def mock_stream(messages, temperature=0.2, max_tokens=1024):
        idx = min(call_count[0], len(SCRIPTED_RESPONSES) - 1)
        text = SCRIPTED_RESPONSES[idx]
        call_count[0] += 1
        return MockStreamGenerator(text)

    with patch.object(agent.llm, "generate_stream", side_effect=mock_stream):
        result, events = asyncio.run(_collect_events(agent))

    # Agent should complete
    assert result["success"] is True

    # Since tools are simulated on this system, any_simulated should be True
    assert result["any_simulated"] is True
    assert len(result["simulated_steps"]) > 0

    # Should have executed several steps
    assert result["total_steps"] >= 2

    # Report should include the integrity warning
    assert "Integrity Warning" in result["report"]

    # Graph should have nodes
    assert len(result["graph"]["nodes"]) >= 1

    # Events should include step_start, action_parsed, graph_update, finished
    event_types = {e.get("type") for e in events}
    assert "step_start" in event_types
    assert "action_parsed" in event_types
    assert "graph_update" in event_types
    assert "finished" in event_types or "report" in event_types


def test_e2e_agent_report_contains_target():
    """Final report mentions the target IP."""
    from backend.agent_core import SentinelAgent

    agent = SentinelAgent("192.168.1.50")

    def mock_stream(messages, temperature=0.2, max_tokens=1024):
        return MockStreamGenerator(
            'Thought: Audit complete.\nAction: FINISH'
        )

    with patch.object(agent.llm, "generate_stream", side_effect=mock_stream):
        result, _ = asyncio.run(_collect_events(agent))

    assert "192.168.1.50" in result["report"]


def test_e2e_cancel_mid_run():
    """Cancelling the agent mid-run stops it at next checkpoint."""
    from backend.agent_core import SentinelAgent

    cancel = asyncio.Event()
    agent = SentinelAgent("10.0.0.5", cancel_event=cancel)

    def mock_stream(messages, temperature=0.2, max_tokens=1024):
        # Cancel after first token
        cancel.set()
        return MockStreamGenerator(
            'Thought: Starting scan...\nAction: nmap_scan\nAction Input: {"target": "10.0.0.5"}'
        )

    with patch.object(agent.llm, "generate_stream", side_effect=mock_stream):
        result, _ = asyncio.run(_collect_events(agent))

    # Should have stopped before max_steps
    assert result["total_steps"] < agent.max_steps
    assert result["success"] is True


def test_e2e_graph_structure():
    """Graph produced by agent has valid node-link structure for Cytoscape."""
    from backend.agent_core import SentinelAgent

    agent = SentinelAgent("10.0.0.5")

    call_count = [0]

    def mock_stream(messages, temperature=0.2, max_tokens=1024):
        idx = min(call_count[0], len(SCRIPTED_RESPONSES) - 1)
        text = SCRIPTED_RESPONSES[idx]
        call_count[0] += 1
        return MockStreamGenerator(text)

    with patch.object(agent.llm, "generate_stream", side_effect=mock_stream):
        result, _ = asyncio.run(_collect_events(agent))

    graph = result["graph"]
    assert "nodes" in graph
    assert "edges" in graph
    assert "links" not in graph  # NetworkX 3 uses "edges"

    # Each node should have required fields
    for node in graph["nodes"]:
        assert "id" in node
        assert "label" in node
        assert "action" in node

    # Edges should have source/target
    for edge in graph["edges"]:
        assert "source" in edge
        assert "target" in edge


def test_e2e_parse_retry_works():
    """Agent retries when LLM returns unparseable response, then succeeds."""
    from backend.agent_core import SentinelAgent

    agent = SentinelAgent("10.0.0.5")
    agent.max_steps = 5  # shorter for test

    call_count = [0]
    responses = [
        # First response: garbage, no Action field
        "I'm sorry, I cannot help with that request.",
        # Second response: properly formatted
        'Thought: Recon started.\nAction: nmap_scan\nAction Input: {"target": "10.0.0.5"}',
        # Third: FINISH
        'Thought: Done.\nAction: FINISH',
    ]

    def mock_stream(messages, temperature=0.2, max_tokens=1024):
        idx = min(call_count[0], len(responses) - 1)
        text = responses[idx]
        call_count[0] += 1
        return MockStreamGenerator(text)

    with patch.object(agent.llm, "generate_stream", side_effect=mock_stream):
        result, _ = asyncio.run(_collect_events(agent))

    # Should have recovered and completed
    assert result["success"] is True
    assert result["total_steps"] >= 1


if __name__ == "__main__":
    tests = [
        test_e2e_full_kill_chain,
        test_e2e_agent_report_contains_target,
        test_e2e_cancel_mid_run,
        test_e2e_graph_structure,
        test_e2e_parse_retry_works,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  ✓ {test.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ✗ {test.__name__} — ASSERT: {e}")
            failed += 1
        except Exception as e:
            print(f"  ✗ {test.__name__} — ERROR: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed out of {len(tests)}")
    sys.exit(0 if failed == 0 else 1)
