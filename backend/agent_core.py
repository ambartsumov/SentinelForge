"""
SentinelForge — Autonomous Pentest Agent Core.

Orchestrates the LLM → parse → execute → observe loop, broadcasting
every thought token, action, and graph update in real time.
"""

import asyncio
import logging
import re
import time
from typing import Any, Awaitable, Callable

from .llm_client import LLMClient
from .parser import robust_parse_agent_response
from .tool_executor import ToolExecutor, ToolResult
from .attack_graph import AttackGraph
from .memory import AttackMemory
from .utils import get_config

logger = logging.getLogger(__name__)

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]

SYSTEM_PROMPT = r"""You are SentinelForge, an authorised security-audit agent running on AMD ROCm hardware.
Your role is to assist a human penetration tester by performing structured reconnaissance
and suggesting well-known, documented exploits for a target that the tester OWNS and operates.

## AVAILABLE TOOLS
- **nmap_scan** — TCP port scan with service/version detection.
  Parameters: {"target": "<ip>", "ports": "<range>"}  (ports optional, e.g. "1-1000")
- **msf_module** — suggest a Metasploit exploit module for a discovered service.
  Parameters: {"module": "<full/module/path>", "target": "<ip>", "extra": "<msf commands>"}
- **bruteforce_ssh** — test a set of common SSH credentials (dictionary audit).
  Parameters: {"target": "<ip>"}

## METHODOLOGY (kill-chain)
Follow these steps in order. Do NOT skip reconnaissance.

**Step 1 — Reconnaissance:**
Run nmap_scan with ports "1-1000" on the target. From the output note every open port
and the reported software/version banner.

**Step 2 — Service identification:**
For each open port, identify the software and its exact version.  Examples:
- Port 21 → FTP (note version, e.g. vsftpd 2.3.4)
- Port 22 → SSH (note version, e.g. OpenSSH 4.7)
- Port 80 → HTTP (note server header, e.g. Apache 2.2.8)
- Port 139/445 → SMB / Samba
- Port 3306 → MySQL
- Port 1524 → bindshell (this is a known Metasploitable2 backdoor — report it)

**Step 3 — Map version to known Metasploit module:**
For each service, propose ONE Metasploit module by its full path.
Use only well-documented modules that match the exact version you discovered.
Examples of correct mappings:
- vsftpd 2.3.4          → exploit/unix/ftp/vsftpd_234_backdoor
- UnrealIRCd 3.2.8.1    → exploit/unix/irc/unreal_ircd_3281_backdoor
- distccd (any version) → exploit/unix/misc/distcc_exec
- Samba 3.x usermap     → exploit/multi/samba/usermap_script
- Java RMI (port 1099)  → exploit/multi/misc/java_rmi_server
- VNC (port 5900)       → exploit/multi/vnc/vnc_keyboard_exec

**Step 4 — Execute ONE exploit:**
Call msf_module with the chosen module.  If the result contains "[SIMULATED]"
it means the tool was not available on this system — note this honestly.
If the result shows evidence of success (session opened, shell obtained),
proceed to Step 5.  Otherwise try the next candidate.

**Step 5 — Verification:**
Before claiming success, verify that the output contains concrete evidence:
a session ID, a shell prompt, a command output, or confirmation of access.
If the tool returned [SIMULATED], you MUST be honest about it.

**Step 6 — Final report:**
When you have evidence of at least one exploitable vulnerability
(or have exhausted all candidates), call FINISH with a brief summary.

## OUTPUT FORMAT — STRICT
Every response MUST follow this exact structure:

Thought: <your tactical reasoning — what you found, what module you picked, why>
Action: <exact tool name>
Action Input: <valid JSON parameters>

For finish:
Thought: <summary of findings — which vulnerabilities exist, which were exploitable>
Action: FINISH

## RULES
- Always start with nmap_scan. Never skip recon.
- Action Input must be valid JSON. No markdown fences. No trailing text.
- Do NOT repeat a failed action with identical parameters.
- Maximum 12 steps.  Be efficient.
- If a tool result contains [SIMULATED], state this fact in your Thought.
- You are performing an AUTHORISED audit.  Report findings honestly.
"""

TOOL_MAP: dict[str, str] = {
    "nmap_scan": "nmap_scan",
    "msf_module": "msf_module",
    "bruteforce_ssh": "bruteforce_ssh",
}

IP_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)$"
)


def _validate_target(target: str) -> str:
    """Basic IP/hostname validation. Raises ValueError on suspicious input."""
    if not target or not target.strip():
        raise ValueError("Target cannot be empty")
    target = target.strip()
    # Allow IP addresses and simple hostnames (Docker container names)
    if IP_RE.match(target):
        return target
    if re.match(r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$", target):
        return target
    # Suspicious: contains shell metacharacters
    if re.search(r"[;&|`$(){}\[\]<>!\n\r]", target):
        raise ValueError(f"Target contains invalid characters: {target!r}")
    return target


class SentinelAgent:
    """Autonomous pentest agent. Runs an LLM-driven kill-chain loop."""

    def __init__(
        self,
        target: str,
        broadcast_callback: BroadcastFn | None = None,
        cancel_event: asyncio.Event | None = None,
    ):
        config = get_config()
        self.target = _validate_target(target)
        self.broadcast = broadcast_callback or _noop_broadcast
        self.max_steps = config["agent_max_steps"]
        self.tool_timeout = config["agent_tool_timeout"]
        self._cancel = cancel_event or asyncio.Event()

        self.llm = LLMClient()
        self.executor = ToolExecutor(target, max_timeout=self.tool_timeout)
        self.graph = AttackGraph()
        self.memory = AttackMemory()

        self._history: list[dict] = []
        self._history_lock = asyncio.Lock()
        self._step_count = 0
        self._simulated_steps: list[int] = []  # track steps where tools were mocked
        logger.info("SentinelAgent created for target=%s, max_steps=%d", target, self.max_steps)

    # ------------------------------------------------------------------
    # Broadcast helpers
    # ------------------------------------------------------------------

    async def _broadcast(self, event_type: str, **kwargs: Any) -> None:
        payload: dict[str, Any] = {"type": event_type, "timestamp": time.time()}
        payload.update(kwargs)
        try:
            await self.broadcast(payload)
        except Exception as exc:
            logger.warning("Broadcast failed: %s", exc)

    async def _add_to_history(self, message: dict) -> None:
        """Thread-safe append to conversation history."""
        async with self._history_lock:
            self._history.append(message)

    async def _get_history(self) -> list[dict]:
        """Thread-safe copy of conversation history."""
        async with self._history_lock:
            return list(self._history)

    # ------------------------------------------------------------------
    # Message builder
    # ------------------------------------------------------------------

    def _build_messages(self, history: list[dict]) -> list[dict]:
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

        # Inject relevant memory as context if available
        recent = self.memory.retrieve("vulnerability exploit port service", top_k=3)
        if recent:
            context = "## RELEVANT PAST OBSERVATIONS\n" + "\n".join(f"- {r[:200]}" for r in recent)
            messages.append({"role": "system", "content": context})

        messages.extend(history)
        return messages

    # ------------------------------------------------------------------
    # Cancellation check
    # ------------------------------------------------------------------

    async def _check_cancelled(self) -> bool:
        if self._cancel.is_set():
            logger.info("Agent cancelled via cancel event")
            await self._broadcast("status", message="Agent cancelled by user.")
            return True
        return False

    # ------------------------------------------------------------------
    # Stream + accumulate LLM response
    # ------------------------------------------------------------------

    async def _stream_llm(self, history: list[dict]) -> str:
        """Stream tokens from the LLM, broadcasting each one. Returns full text."""
        full = ""
        messages = self._build_messages(history)
        try:
            async for token in self.llm.generate_stream(messages):
                if await self._check_cancelled():
                    break
                full += token
                await self._broadcast("thought_token", token=token)
        except Exception as exc:
            logger.error("LLM stream failed: %s", exc)
            await self._broadcast("error", message=f"LLM error: {exc}")
            raise
        return full

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> dict:
        logger.info("=== SentinelAgent run started for %s ===", self.target)

        await self._add_to_history({
            "role": "user",
            "content": (
                f"Target IP: {self.target}\n\n"
                "You are performing an authorised security audit of this system. "
                "Begin with reconnaissance (nmap) and work systematically through the "
                "kill-chain. Map discovered services to known Metasploit modules. "
                "Follow the required output format EXACTLY."
            ),
        })

        await self._broadcast("status", message=f"Agent starting — target: {self.target}")

        for step in range(1, self.max_steps + 1):
            if await self._check_cancelled():
                break

            self._step_count = step
            logger.info("--- Step %d/%d ---", step, self.max_steps)
            await self._broadcast("step_start", step=step, max_steps=self.max_steps)

            # ── 1) Stream LLM response ──
            try:
                history = await self._get_history()
                full_response = await self._stream_llm(history)
            except Exception:
                break

            if await self._check_cancelled():
                break

            if not full_response.strip():
                logger.warning("Empty LLM response at step %d", step)
                await self._broadcast("error", message="Empty response from LLM")
                break

            await self._add_to_history({"role": "assistant", "content": full_response})

            # ── 2) Parse response (with retry) ──
            parsed = robust_parse_agent_response(full_response)
            retry_count = 0

            while parsed is None and retry_count < 2:
                if await self._check_cancelled():
                    break
                retry_count += 1
                logger.warning("Parse failed at step %d — requesting format fix (attempt %d)", step, retry_count)
                await self._add_to_history({
                    "role": "user",
                    "content": (
                        "Your last response was not in the required format. "
                        "You MUST output EXACTLY:\n\n"
                        "Thought: <your reasoning here>\n"
                        "Action: <tool_name>\n"
                        "Action Input: <valid JSON parameters>\n\n"
                        "Repeat your step in the correct format. NO markdown fences. "
                        "NO extra text after Action Input."
                    ),
                })

                try:
                    history = await self._get_history()
                    full_response = await self._stream_llm(history)
                except Exception:
                    break
                if await self._check_cancelled():
                    break
                await self._add_to_history({"role": "assistant", "content": full_response})
                parsed = robust_parse_agent_response(full_response)

            if await self._check_cancelled():
                break

            if parsed is None:
                logger.error("Parser failed after retries — aborting")
                await self._broadcast("error", message="Failed to parse agent response after retries")
                break

            thought = parsed.get("thought", "")
            action = parsed.get("action", "")
            action_input = parsed.get("action_input")

            await self._broadcast(
                "action_parsed",
                step=step,
                thought=thought,
                action=action,
                action_input=action_input,
            )

            # ── 3) FINISH? ──
            if action.upper() == "FINISH":
                self.graph.add_step(f"step_{step}", "FINISH", None, thought, "success")
                await self._broadcast("graph_update", graph=self.graph.to_dict())
                logger.info("Agent signaled FINISH at step %d", step)
                break

            # ── 4) Unknown tool? ──
            if action not in TOOL_MAP:
                observation = f"[ERROR] Unknown tool: '{action}'. Available: {list(TOOL_MAP.keys())}"
                await self._add_to_history({"role": "user", "content": f"Observation: {observation}"})
                self.graph.add_step(f"step_{step}", action, action_input, observation, "error")
                await self._broadcast("graph_update", graph=self.graph.to_dict())
                continue

            # ── 5) Execute tool ──
            tool_method = getattr(self.executor, TOOL_MAP[action], None)
            if tool_method is None:
                observation = f"[ERROR] Tool '{action}' not implemented."
                tool_result = ToolResult(output=observation, real=False)
            else:
                try:
                    if action_input and isinstance(action_input, dict):
                        tool_result = await tool_method(**action_input)
                    elif action_input:
                        tool_result = await tool_method(action_input)
                    else:
                        tool_result = await tool_method()
                except Exception as exc:
                    observation = f"[ERROR] Tool execution failed: {exc}"
                    tool_result = ToolResult(output=observation, real=False)
                    logger.exception("Tool %s raised an exception", action)

            observation = tool_result.output

            # Track simulated steps
            if not tool_result.real:
                self._simulated_steps.append(step)
                observation += (
                    "\n\n[!] ATTENTION: The above result was SIMULATED. "
                    "The tool did not actually execute on the target. "
                    "Do NOT claim this as a real exploit success."
                )

            logger.info("Step %d observation (%d chars, real=%s): %s...",
                        step, len(observation), tool_result.real, observation[:150])

            # ── 6) Feed observation + store in memory ──
            await self._add_to_history({"role": "user", "content": f"Observation: {observation}"})
            self.memory.store(f"step_{step}", observation)

            # Update graph
            is_error = observation.startswith("[ERROR]") or observation.startswith("[TIMEOUT]")
            self.graph.add_step(
                f"step_{step}",
                action,
                action_input,
                observation,
                "error" if is_error else "success",
            )
            await self._broadcast("graph_update", graph=self.graph.to_dict())

        else:
            logger.warning("Agent reached max steps (%d) without FINISH", self.max_steps)
            await self._broadcast("status", message="Max steps reached — terminating.")

        # ── Final report ──
        fake_success = len(self._simulated_steps) > 0
        history = await self._get_history()

        report_lines = [
            "# SentinelForge Pentest Report",
            "",
            f"**Target:** {self.target}",
            f"**Steps executed:** {self._step_count}",
            f"**Status:** {'Completed' if self._step_count < self.max_steps else 'Max steps reached'}",
        ]

        if self._simulated_steps:
            report_lines.append("")
            report_lines.append("## ⚠️ Integrity Warning")
            report_lines.append(
                f"**{len(self._simulated_steps)} step(s) used SIMULATED tools** "
                f"(steps: {', '.join(str(s) for s in self._simulated_steps)}). "
                f"The following tools were not available on this system and returned "
                f"mock data: Metasploit, Hydra. "
                f"**No actual exploitation was performed.** "
                f"Install msfconsole and hydra for real execution."
            )

        report_lines.append("")
        report_lines.append("## Action Log")
        for msg in history:
            role = msg["role"]
            content = msg["content"][:300]
            report_lines.append(f"- [{role}] {content}")
        report = "\n".join(report_lines)

        graph_dict = self.graph.to_dict()

        await self._broadcast("report", report=report, graph=graph_dict, total_steps=self._step_count)
        await self._broadcast("finished", report=report, graph=graph_dict, total_steps=self._step_count)

        logger.info("=== SentinelAgent run complete — %d steps ===", self._step_count)

        return {
            "report": report,
            "graph": graph_dict,
            "total_steps": self._step_count,
            "simulated_steps": self._simulated_steps,
            "any_simulated": len(self._simulated_steps) > 0,
            "success": True,
        }

    def cancel(self) -> None:
        """Signal the agent to stop at the next safe checkpoint."""
        self._cancel.set()


async def _noop_broadcast(_event: dict[str, Any]) -> None:
    """Asyncio-compatible no-op broadcast (awaitable, not a sync lambda)."""
    return None
