"""
SentinelForge — Tool Executor.

Executes external pentesting tools (nmap, Metasploit, Hydra) on a target.
Each tool runs as a subprocess with configurable timeouts.
Kills child processes on timeout to prevent zombie leaks.

Returns ToolResult(output=str, real=bool) so the agent can distinguish
real tool execution from mock/simulated results.
"""

import asyncio
import logging
import os
import subprocess
import sys
import tempfile
import shutil
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """Result of a tool execution.

    Attributes:
        output: Human-readable stdout/stderr or mock description.
        real: True if the tool actually ran; False if simulated/mocked.
    """
    output: str
    real: bool = True


# ---------------------------------------------------------------------------
# Helper: detect Docker availability for containerised tools
# ---------------------------------------------------------------------------

def _docker_exec(container: str, *args: str) -> list[str]:
    """Build a 'docker exec' command list for running a tool inside a container."""
    return ["docker", "exec", container] + list(args)


def _docker_available() -> bool:
    """Check whether Docker is installed AND the daemon is reachable."""
    if not shutil.which("docker"):
        return False
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=5,
            **({} if sys.platform != "win32" else {"creationflags": 0x08000000}),
        )
        return result.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# ToolExecutor
# ---------------------------------------------------------------------------

class ToolExecutor:
    """
    Runs pentest tools against a specified target.

    Tools available:
        - nmap_scan(target, ports) → ToolResult
        - msf_module(module, target, extra) → ToolResult
        - bruteforce_ssh(target, username, wordlist) → ToolResult
    """

    def __init__(self, target: str, max_timeout: int = 30):
        self.target = target
        self.max_timeout = max_timeout
        logger.info("ToolExecutor initialized for target=%s timeout=%ds", target, max_timeout)

    async def _run_cmd(self, cmd: list[str], timeout: int | None = None) -> str:
        """
        Run a shell command asynchronously with a timeout.

        Kills the child process on timeout to prevent zombie leaks.
        Returns combined stdout+stderr as a string.
        """
        timeout = timeout or self.max_timeout
        logger.debug("Running command: %s (timeout=%ds)", " ".join(cmd), timeout)

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode("utf-8", errors="replace").strip()
            logger.debug("Command output (%d chars): %s...", len(output), output[:200])
            return output
        except asyncio.TimeoutError:
            logger.warning("Command timed out after %ds: %s", timeout, " ".join(cmd))
            if proc is not None:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
            return f"[TIMEOUT] Command exceeded {timeout}s limit: {' '.join(cmd)}"
        except FileNotFoundError:
            logger.error("Tool not found: %s", cmd[0])
            return f"[ERROR] Tool not installed: {cmd[0]}"
        except Exception as exc:
            logger.error("Command failed: %s — %s", " ".join(cmd), exc)
            if proc is not None:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
            return f"[ERROR] {exc}"

    # ------------------------------------------------------------------
    # nmap
    # ------------------------------------------------------------------
    async def nmap_scan(
        self, target: str | None = None, ports: str | None = None
    ) -> ToolResult:
        """Run an nmap scan with version detection and safe scripts."""
        tgt = target or self.target
        cmd = ["nmap", "-sV", "-sC", "-T4", "--max-retries", "2"]
        if ports:
            cmd += ["-p", ports]
        cmd.append(tgt)

        if not shutil.which("nmap"):
            logger.warning("nmap not found — returning simulated result")
            return ToolResult(
                output=(
                    f"[SIMULATED] nmap scan on {tgt}\n"
                    f"PORT     STATE  SERVICE     VERSION\n"
                    f"21/tcp   open   ftp         vsftpd 2.3.4\n"
                    f"22/tcp   open   ssh         OpenSSH 4.7p1 Debian\n"
                    f"80/tcp   open   http        Apache httpd 2.2.8\n"
                    f"1524/tcp open   bindshell   Metasploitable root shell\n"
                ),
                real=False,
            )

        logger.info("Starting nmap scan on %s (ports=%s)", tgt, ports or "default")
        output = await self._run_cmd(cmd, timeout=self.max_timeout * 4)
        return ToolResult(output=output, real=True)

    # ------------------------------------------------------------------
    # Metasploit
    # ------------------------------------------------------------------
    async def msf_module(
        self,
        module: str,
        target: str | None = None,
        extra: str = "",
    ) -> ToolResult:
        """Execute a Metasploit module via msfconsole or Docker."""
        tgt = target or self.target

        rc_content = f"""use {module}
set RHOSTS {tgt}
{extra}
run
exit
"""

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".rc", delete=False, encoding="utf-8"
        ) as f:
            f.write(rc_content)
            rc_path = f.name

        try:
            # 1) Try local msfconsole
            if shutil.which("msfconsole"):
                logger.info("Executing msf module %s against %s (local msfconsole)", module, tgt)
                cmd = ["msfconsole", "-q", "-r", rc_path]
                output = await self._run_cmd(cmd, timeout=self.max_timeout * 6)
                return ToolResult(output=output, real=True)

            # 2) Try Docker-based Metasploit (msf_container)
            msf_container = os.getenv("MSF_CONTAINER", "sentinel-msf")
            if _docker_available():
                # Copy rc file into container and execute
                logger.info("Trying Docker msfconsole in container %s", msf_container)
                try:
                    copy_cmd = ["docker", "cp", rc_path, f"{msf_container}:/tmp/sentinel.rc"]
                    proc = await asyncio.create_subprocess_exec(
                        *copy_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await asyncio.wait_for(proc.communicate(), timeout=10)

                    exec_cmd = _docker_exec(msf_container, "msfconsole", "-q", "-r", "/tmp/sentinel.rc")
                    output = await self._run_cmd(exec_cmd, timeout=self.max_timeout * 6)
                    return ToolResult(output=output, real=True)
                except Exception as exc:
                    logger.warning("Docker msfconsole failed: %s", exc)

            # 3) Fallback: simulated result
            logger.warning("msfconsole not available — returning simulated result")
            return ToolResult(
                output=(
                    f"[SIMULATED] Metasploit module {module} against {tgt}\n"
                    f"RHOSTS => {tgt}\n"
                    f"[*] Started reverse TCP handler on 0.0.0.0:4444\n"
                    f"[+] {tgt} — exploit payload delivered\n"
                    f"[*] Command shell session opened\n"
                    f"[!] WARNING: This is a SIMULATED result — no actual exploitation occurred.\n"
                    f"[SIMULATED] To run real exploits, install msfconsole or set MSF_CONTAINER."
                ),
                real=False,
            )
        finally:
            try:
                os.unlink(rc_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # bruteforce SSH
    # ------------------------------------------------------------------
    async def bruteforce_ssh(
        self,
        target: str | None = None,
        username: str = "root",
        wordlist: str = "",
    ) -> ToolResult:
        """
        Attempt SSH brute-force using Hydra.

        Args:
            target: override target IP.
            username: SSH username (default: root).
            wordlist: path to password list (falls back to common passwords).

        Falls back to mock when Hydra is not installed and no Docker.
        """
        tgt = target or self.target

        # Try local hydra
        if shutil.which("hydra"):
            candidates = [
                wordlist,
                "/usr/share/wordlists/rockyou.txt",
                "/usr/share/wordlists/rockyou.txt.gz",
                "/usr/share/dict/wordlist",
            ]
            chosen = next((p for p in candidates if p and os.path.isfile(p)), None)

            if chosen is None:
                logger.warning("No wordlist found — using built-in common passwords")
                pw_list = ["admin", "password", "123456", "root", "toor", "msfadmin"]
                chosen = None
            else:
                pw_list = None
                logger.info("Using wordlist: %s", chosen)

            logger.info("Running Hydra SSH brute-force on %s (user=%s)", tgt, username)
            cmd = ["hydra", "-l", username]
            if chosen:
                cmd += ["-P", chosen]
            else:
                cmd += ["-p", ",".join(pw_list)]
            cmd += [f"ssh://{tgt}", "-t", "4", "-f"]

            output = await self._run_cmd(cmd, timeout=self.max_timeout * 8)
            return ToolResult(output=output, real=True)

        # Try Docker-based hydra
        hydra_container = os.getenv("HYDRA_CONTAINER", "sentinel-hydra")
        if _docker_available():
            logger.info("Trying Docker hydra in container %s", hydra_container)
            try:
                cmd = _docker_exec(hydra_container, "hydra", "-l", username,
                                   "-P", "/wordlists/rockyou.txt",
                                   f"ssh://{tgt}", "-t", "4", "-f")
                output = await self._run_cmd(cmd, timeout=self.max_timeout * 8)
                return ToolResult(output=output, real=True)
            except Exception as exc:
                logger.warning("Docker hydra failed: %s", exc)

        # Fallback: simulated
        logger.info("Hydra not available — returning simulated result")
        return ToolResult(
            output=(
                f"[SIMULATED] SSH brute-force on {tgt}\n"
                f"[22][ssh] host: {tgt}   login: msfadmin   password: msfadmin\n"
                f"[22][ssh] host: {tgt}   login: root       password: password\n"
                f"[!] WARNING: This is a SIMULATED result — no actual brute-force occurred.\n"
                f"[SIMULATED] 2 simulated credentials. Install hydra for real execution."
            ),
            real=False,
        )
