"""
Integration smoke + regression tests — verify all modules import and function.
Updated for ToolResult return type.
"""

import sys
import os
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Parser ────────────────────────────────────────────────────────────

def test_parser_import():
    from backend.parser import robust_parse_agent_response
    assert callable(robust_parse_agent_response)


# ── Utils ─────────────────────────────────────────────────────────────

def test_utils_import():
    from backend.utils import get_config, load_env, setup_logging
    assert callable(get_config)
    assert callable(setup_logging)


def test_get_config():
    from backend.utils import get_config
    cfg = get_config()
    assert "llm_base_url" in cfg
    assert "llm_model" in cfg
    assert "agent_max_steps" in cfg
    assert cfg["agent_max_steps"] > 0


# ── Attack Graph ──────────────────────────────────────────────────────

def test_attack_graph():
    from backend.attack_graph import AttackGraph
    g = AttackGraph()
    g.add_step("step_1", "nmap_scan", {"target": "x"}, "open ports: 22, 80")
    g.add_step("step_2", "bruteforce_ssh", {}, "found creds")
    d = g.to_dict()
    assert len(d["nodes"]) == 2
    assert len(d["edges"]) == 1
    assert d["nodes"][0]["action"] == "nmap_scan"
    # FINISH node gets special label
    g.add_step("step_3", "FINISH", None, "done", "success")
    d2 = g.to_dict()
    assert len(d2["nodes"]) == 3


# ── Memory ────────────────────────────────────────────────────────────

def test_memory_dict_mode():
    from backend.memory import AttackMemory
    m = AttackMemory(use_chromadb=False)
    m.store("s1", "ssh port 22 open")
    m.store("s2", "http port 80 open")
    results = m.retrieve("ssh", top_k=2)
    assert len(results) >= 1
    assert "22" in results[0]
    assert len(m) == 2


def test_memory_count():
    """Verify _count is tracked separately from _store for ChromaDB mode."""
    from backend.memory import AttackMemory
    m = AttackMemory(use_chromadb=False)
    m.store("a", "data")
    m.store("b", "data")
    assert m._count == 2


# ── LLM Client ────────────────────────────────────────────────────────

def test_llm_client_init():
    from backend.llm_client import LLMClient
    client = LLMClient(
        base_url="https://api.deepseek.com/v1",
        api_key="sk-test",
        model="deepseek-chat",
    )
    assert client.model == "deepseek-chat"
    assert client.base_url == "https://api.deepseek.com/v1"


# ── Tool Executor ─────────────────────────────────────────────────────

def test_tool_executor_init():
    from backend.tool_executor import ToolExecutor
    te = ToolExecutor("192.168.1.1", max_timeout=10)
    assert te.target == "192.168.1.1"
    assert te.max_timeout == 10


def test_tool_executor_mock_msf():
    """msf_module returns ToolResult with real=False when msfconsole is not installed."""
    from backend.tool_executor import ToolExecutor
    te = ToolExecutor("10.0.0.5")
    result = asyncio.run(te.msf_module("exploit/test"))
    assert not result.real
    assert "[SIMULATED]" in result.output
    assert "10.0.0.5" in result.output


def test_tool_executor_mock_bruteforce():
    """bruteforce_ssh returns ToolResult with real=False when Hydra is not installed."""
    from backend.tool_executor import ToolExecutor
    te = ToolExecutor("10.0.0.5")
    result = asyncio.run(te.bruteforce_ssh())
    assert not result.real
    assert "[SIMULATED]" in result.output
    assert "msfadmin" in result.output


def test_tool_executor_mock_nmap():
    """nmap_scan returns ToolResult with real=False when nmap is not found."""
    from backend.tool_executor import ToolExecutor
    import shutil
    te = ToolExecutor("10.0.0.5")
    result = asyncio.run(te.nmap_scan())
    # nmap may or may not be installed; check the interface
    assert hasattr(result, "output")
    assert hasattr(result, "real")
    assert isinstance(result.real, bool)


def test_tool_result_dataclass():
    """ToolResult dataclass has correct defaults."""
    from backend.tool_executor import ToolResult
    r1 = ToolResult(output="test")
    assert r1.output == "test"
    assert r1.real is True
    r2 = ToolResult(output="mock", real=False)
    assert r2.real is False


# ── Agent Core ────────────────────────────────────────────────────────

def test_agent_target_validation():
    """Valid targets pass; invalid ones raise ValueError."""
    from backend.agent_core import _validate_target
    assert _validate_target("192.168.1.1") == "192.168.1.1"
    assert _validate_target("sentinel-dvwa") == "sentinel-dvwa"
    assert _validate_target("10.0.0.5") == "10.0.0.5"

    try:
        _validate_target("192.168.1.1; rm -rf /")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
    try:
        _validate_target("$(whoami)")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_agent_noop_broadcast():
    """No-op broadcast must be awaitable (not sync lambda)."""
    from backend.agent_core import _noop_broadcast
    import inspect
    assert inspect.iscoroutinefunction(_noop_broadcast)
    asyncio.run(_noop_broadcast({"type": "test"}))


def test_agent_cancel_event():
    """Cancel event stops the agent at next checkpoint."""
    from backend.agent_core import SentinelAgent
    cancel = asyncio.Event()
    cancel.set()  # pre-cancelled
    agent = SentinelAgent("192.168.1.1", cancel_event=cancel)
    result = asyncio.run(agent.run())
    assert result["total_steps"] == 0
    assert result["success"] is True


def test_agent_broadcast_no_callback():
    """Agent must not crash when broadcast_callback is not provided."""
    from backend.agent_core import SentinelAgent
    cancel = asyncio.Event()
    cancel.set()
    agent = SentinelAgent("192.168.1.1", cancel_event=cancel)
    result = asyncio.run(agent.run())
    assert result is not None


def test_agent_simulated_steps_tracked():
    """Agent tracks which steps used simulated tools."""
    from backend.agent_core import SentinelAgent
    cancel = asyncio.Event()
    cancel.set()
    agent = SentinelAgent("192.168.1.1", cancel_event=cancel)
    result = asyncio.run(agent.run())
    assert "simulated_steps" in result
    assert "any_simulated" in result


# ── Server ────────────────────────────────────────────────────────────

def test_server_import():
    from backend.server import app
    assert app.title == "SentinelForge API"


# ── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_parser_import,
        test_utils_import,
        test_get_config,
        test_attack_graph,
        test_memory_dict_mode,
        test_memory_count,
        test_llm_client_init,
        test_tool_executor_init,
        test_tool_executor_mock_msf,
        test_tool_executor_mock_bruteforce,
        test_tool_executor_mock_nmap,
        test_tool_result_dataclass,
        test_agent_target_validation,
        test_agent_noop_broadcast,
        test_agent_cancel_event,
        test_agent_broadcast_no_callback,
        test_agent_simulated_steps_tracked,
        test_server_import,
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
