"""
SentinelForge — LLM Client.

Provider-agnostic OpenAI-compatible client with streaming support.
Switching between DeepSeek API and local vLLM requires only a .env change.
"""

import logging
from typing import AsyncGenerator

from openai import AsyncOpenAI

from .utils import get_config

logger = logging.getLogger(__name__)


class LLMClient:
    """
    Async wrapper around an OpenAI-compatible API endpoint.

    Usage:
        client = LLMClient()
        response = await client.generate([{"role": "user", "content": "Hello"}])
        async for chunk in client.generate_stream(messages):
            print(chunk, end="")
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None):
        config = get_config()
        self.base_url = base_url or config["llm_base_url"]
        self.api_key = api_key or config["llm_api_key"]
        self.model = model or config["llm_model"]

        self._client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
        )
        logger.info("LLMClient initialized — model=%s endpoint=%s", self.model, self.base_url)

    async def generate(
        self,
        messages: list[dict],
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> str:
        """
        Non-streaming completion. Returns the full response text.
        """
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            logger.error("LLM generate failed: %s", exc)
            raise

    async def generate_stream(
        self,
        messages: list[dict],
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> AsyncGenerator[str, None]:
        """
        Streaming completion. Yields text chunks as they arrive.
        """
        try:
            stream = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    yield delta.content
        except Exception as exc:
            logger.error("LLM stream failed: %s", exc)
            raise
