"""
SentinelForge — Shared utilities: logging setup, env config loader.
"""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv


def get_project_root() -> Path:
    """Return the absolute path to the project root directory."""
    return Path(__file__).resolve().parent.parent


def load_env():
    """Load .env file from the project root if it exists; no-op if not found."""
    env_path = get_project_root() / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        env_path_example = get_project_root() / ".env.example"
        if env_path_example.exists():
            load_dotenv(env_path_example)


def get_config() -> dict:
    """
    Read and return all relevant configuration from environment variables.
    Ensures .env is loaded first.
    """
    load_env()
    return {
        "llm_base_url": os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
        "llm_api_key": os.getenv("LLM_API_KEY", ""),
        "llm_model": os.getenv("LLM_MODEL", "deepseek-chat"),
        "agent_max_steps": int(os.getenv("AGENT_MAX_STEPS", "12")),
        "agent_tool_timeout": int(os.getenv("AGENT_TOOL_TIMEOUT", "30")),
        "host": os.getenv("HOST", "0.0.0.0"),
        "port": int(os.getenv("PORT", "8000")),
    }


def setup_logging(level: int = logging.INFO):
    """Configure root logger with a sensible default format."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
