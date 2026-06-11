"""
SentinelForge — Robust Agent Response Parser.

Extracts Thought / Action / Action Input from LLM-generated text.
Handles common LLM formatting quirks: markdown fences, malformed JSON,
single quotes, missing fields, extraneous text, nested braces in thoughts.
"""

import json
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _try_json_parse(candidate: str) -> Optional[dict]:
    """Attempt multiple JSON repair strategies on a candidate string."""
    # 1) Try as-is
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # 2) Replace single quotes with double quotes (JSON requires double).
    #    Use full-match groups instead of lookbehind (fixed-width requirement).
    #    Match {'key': 'value'} patterns.
    fixed = re.sub(
        r"([{,]\s*)'(\w+)'(\s*:)",
        lambda m: m.group(1) + '"' + m.group(2) + '"' + m.group(3),
        candidate,
    )  # single-quoted keys
    fixed = re.sub(
        r"(:\s*)'([^']*)'(\s*[,}])",
        lambda m: m.group(1) + '"' + m.group(2) + '"' + m.group(3),
        fixed,
    )  # single-quoted values
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # 3) Remove trailing commas before } or ]
    fixed2 = re.sub(r",\s*([}\]])", r"\1", candidate)
    try:
        return json.loads(fixed2)
    except json.JSONDecodeError:
        pass

    # 4) Combine both fixes (missing commas + single quotes)
    fixed3 = re.sub(
        r"([{,]\s*)'(\w+)'(\s*:)",
        lambda m: m.group(1) + '"' + m.group(2) + '"' + m.group(3),
        fixed2,
    )
    fixed3 = re.sub(
        r"(:\s*)'([^']*)'(\s*[,}])",
        lambda m: m.group(1) + '"' + m.group(2) + '"' + m.group(3),
        fixed3,
    )
    try:
        return json.loads(fixed3)
    except json.JSONDecodeError:
        pass

    # 5) Missing commas between adjacent "key": val \n "key2" pairs
    fixed4 = re.sub(r'"\s*\n\s*"', '",\n"', candidate)
    try:
        return json.loads(fixed4)
    except json.JSONDecodeError:
        pass

    # 6) Remove unescaped control characters
    fixed5 = re.sub(r"[\x00-\x1f]", " ", candidate)
    try:
        return json.loads(fixed5)
    except json.JSONDecodeError:
        pass

    return None


def _extract_json_block(text: str) -> Optional[str]:
    """Try to extract a JSON object from text, even if partially malformed."""
    # Strip markdown code fences
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = re.sub(r"```", "", cleaned)

    # Find the outermost JSON object — match balanced braces
    # Use a simple stack-based approach for the first { }
    start = cleaned.find("{")
    if start == -1:
        return None

    depth = 0
    end = -1
    for i in range(start, len(cleaned)):
        if cleaned[i] == "{":
            depth += 1
        elif cleaned[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break

    if end == -1:
        # No balanced closing brace — take whatever we have
        end = len(cleaned)

    return cleaned[start:end + 1]


def robust_parse_agent_response(text: str) -> Optional[dict]:
    """
    Parse an agent's LLM response into structured components.

    Expected format:
        Thought: <reasoning>
        Action: <tool_name>
        Action Input: <json_or_string>

    For finish:
        Thought: <reasoning>
        Action: FINISH

    Returns:
        dict with keys 'thought', 'action', 'action_input' (may be None),
        or None if the response cannot be parsed.
    """
    if not text or not isinstance(text, str):
        return None

    result: dict = {"thought": "", "action": "", "action_input": None}

    # ---- Extract Thought ----
    thought_match = re.search(
        r"Thought:\s*(.+?)(?=\n\s*(?:Action|Observation|$))",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if thought_match:
        result["thought"] = thought_match.group(1).strip()
    else:
        # Fallback: everything before Action:
        parts = re.split(r"\n\s*Action\s*:", text, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) > 1:
            pre = re.sub(r"^Thought:\s*", "", parts[0], flags=re.IGNORECASE).strip()
            result["thought"] = pre
        else:
            result["thought"] = ""

    # ---- Extract Action ----
    action_match = re.search(
        r"Action\s*:\s*(\S+)",
        text,
        re.IGNORECASE,
    )
    if not action_match:
        logger.debug("No Action: field found in agent response")
        return None

    result["action"] = action_match.group(1).strip()

    # ---- Extract Action Input ----
    action_input_match = re.search(
        r"Action\s*Input\s*:\s*(.+)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if action_input_match:
        raw_input = action_input_match.group(1).strip()

        # Try parsing as JSON with multiple repair strategies
        json_block = _extract_json_block(raw_input)
        if json_block:
            parsed = _try_json_parse(json_block)
            if parsed is not None:
                result["action_input"] = parsed
            else:
                # Could not parse as JSON — return raw string
                result["action_input"] = raw_input
        else:
            # No JSON braces — return as plain string
            result["action_input"] = raw_input

    # ---- Cleanup thought (remove markdown artifacts) ----
    result["thought"] = re.sub(r"```.*?```", "", result["thought"], flags=re.DOTALL).strip()

    return result
