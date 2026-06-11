"""
Tests for robust_parse_agent_response — covers 10 scenarios.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.parser import robust_parse_agent_response


def test_perfect_format():
    """Ideal format: Thought → Action → Action Input with valid JSON."""
    text = (
        'Thought: Scanning for open ports on the target.\n'
        'Action: nmap_scan\n'
        'Action Input: {"target": "192.168.1.100", "ports": "1-1000"}'
    )
    result = robust_parse_agent_response(text)
    assert result is not None
    assert result["thought"] == "Scanning for open ports on the target."
    assert result["action"] == "nmap_scan"
    assert result["action_input"] == {"target": "192.168.1.100", "ports": "1-1000"}


def test_finish_action():
    """Agent signals completion with Action: FINISH (no Action Input)."""
    text = (
        "Thought: I have gathered enough information. The attack is complete.\n"
        "Action: FINISH"
    )
    result = robust_parse_agent_response(text)
    assert result is not None
    assert result["action"] == "FINISH"
    assert result["action_input"] is None
    assert "attack is complete" in result["thought"].lower()


def test_markdown_wrapping():
    """LLM wraps the entire response in a markdown code fence."""
    text = (
        "```\n"
        'Thought: Trying ssh brute force.\n'
        'Action: bruteforce_ssh\n'
        'Action Input: {"username": "admin", "password": "12345"}\n'
        "```"
    )
    result = robust_parse_agent_response(text)
    assert result is not None
    assert result["action"] == "bruteforce_ssh"
    assert result["action_input"] == {"username": "admin", "password": "12345"}


def test_broken_json_with_single_quotes():
    """LLM uses single quotes instead of double quotes in JSON."""
    text = (
        'Thought: Exploit found.\n'
        "Action: msf_module\n"
        "Action Input: {'module': 'exploit/unix/ftp/vsftpd_234_backdoor', 'target': '10.0.0.5'}"
    )
    result = robust_parse_agent_response(text)
    assert result is not None
    assert result["action"] == "msf_module"
    # Should recover the JSON
    assert isinstance(result["action_input"], dict)
    assert result["action_input"]["module"] == "exploit/unix/ftp/vsftpd_234_backdoor"


def test_single_quotes_with_apostrophe_in_value():
    """Single-quoted JSON where a value contains an apostrophe (e.g. can't)."""
    text = (
        'Thought: testing.\n'
        "Action: nmap_scan\n"
        "Action Input: {'description': 'can not scan this', 'port': 80}"
    )
    result = robust_parse_agent_response(text)
    assert result is not None
    assert result["action"] == "nmap_scan"
    # Should recover at minimum without crashing
    assert result["action_input"] is not None


def test_missing_action_input():
    """Action is present but no Action Input provided at all."""
    text = (
        "Thought: The previous command failed, let me try something else.\n"
        "Action: nmap_scan"
    )
    result = robust_parse_agent_response(text)
    assert result is not None
    assert result["action"] == "nmap_scan"
    assert result["action_input"] is None


def test_extraneous_text_before_and_after():
    """LLM adds conversational fluff around the structured response."""
    text = (
        "Sure, let me scan that target for you!\n\n"
        'Thought: Starting reconnaissance.\n'
        "Action: nmap_scan\n"
        'Action Input: {"target": "172.16.0.1"}\n\n'
        "I will now proceed with the scan."
    )
    result = robust_parse_agent_response(text)
    assert result is not None
    assert result["action"] == "nmap_scan"
    assert result["action_input"] == {"target": "172.16.0.1"}


def test_no_action_field():
    """Response contains no Action: line at all."""
    text = "I cannot perform this task because of safety concerns."
    result = robust_parse_agent_response(text)
    assert result is None


def test_empty_input():
    """Empty string or None should return None."""
    assert robust_parse_agent_response("") is None
    assert robust_parse_agent_response(None) is None


def test_broken_json_with_missing_commas():
    """JSON is missing commas between key-value pairs (multi-line)."""
    text = (
        'Thought: Attacking.\n'
        'Action: nmap_scan\n'
        'Action Input: {"target": "10.0.0.1"\n"ports": "22,80,443"\n"timing": "aggressive"}'
    )
    result = robust_parse_agent_response(text)
    assert result is not None
    assert result["action"] == "nmap_scan"
    # Should recover — at minimum not crash
    assert result["action_input"] is not None


def test_nested_braces_in_thought():
    """Thought contains JSON-like output but Action Input is separate valid JSON."""
    text = (
        'Thought: Scan shows {"port": 21, "service": "ftp"} — interesting.\n'
        'Action: msf_module\n'
        'Action Input: {"module": "exploit/ftp", "target": "10.0.0.1"}'
    )
    result = robust_parse_agent_response(text)
    assert result is not None
    assert result["action"] == "msf_module"
    assert isinstance(result["action_input"], dict)
    assert result["action_input"]["module"] == "exploit/ftp"


if __name__ == "__main__":
    tests = [
        test_perfect_format,
        test_finish_action,
        test_markdown_wrapping,
        test_broken_json_with_single_quotes,
        test_single_quotes_with_apostrophe_in_value,
        test_missing_action_input,
        test_extraneous_text_before_and_after,
        test_no_action_field,
        test_empty_input,
        test_broken_json_with_missing_commas,
        test_nested_braces_in_thought,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  ✓ {test.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ✗ {test.__name__} — {e}")
            failed += 1
        except Exception as e:
            print(f"  ✗ {test.__name__} — ERROR: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed out of {len(tests)}")
    sys.exit(0 if failed == 0 else 1)
