"""
SentinelForge — Attack Memory.

Stores and retrieves embeddings of past attack steps using ChromaDB.
In MVP mode falls back to a simple in-memory dict when ChromaDB is unavailable.
The interface is stable so it can be upgraded to full ChromaDB later.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class AttackMemory:
    """
    Semantic memory for the agent. Stores (key → text) pairs and
    supports similarity-based retrieval.

    In full mode uses ChromaDB + SentenceTransformer for embeddings.
    In MVP mode uses a simple in-memory dictionary with substring matching.

    Usage:
        mem = AttackMemory()
        mem.store("step_1", "Found open ports 22 and 80 on target.")
        results = mem.retrieve("ssh port", top_k=2)
    """

    def __init__(self, use_chromadb: bool = False):
        """
        Args:
            use_chromadb: if True, attempt to use ChromaDB; falls back to
                           simple dict otherwise.
        """
        self._use_chromadb = use_chromadb
        self._store: dict[str, str] = {}
        self._chroma_collection = None
        self._count: int = 0  # separate counter for ChromaDB mode

        if use_chromadb:
            try:
                import chromadb
                from chromadb.config import Settings

                client = chromadb.Client(Settings(anonymized_telemetry=False))
                self._chroma_collection = client.get_or_create_collection("attack_memory")
                logger.info("AttackMemory: ChromaDB backend active")
            except ImportError:
                logger.warning("chromadb not installed — using simple dict backend")
                self._use_chromadb = False
            except Exception as exc:
                logger.warning("ChromaDB init failed (%s) — using simple dict backend", exc)
                self._use_chromadb = False

    def store(self, key: str, text: str) -> None:
        """
        Persist a memory entry.

        Args:
            key: unique identifier (e.g. 'step_3').
            text: free-form description or observation.
        """
        self._count += 1
        if self._use_chromadb and self._chroma_collection is not None:
            try:
                self._chroma_collection.add(
                    documents=[text],
                    ids=[key],
                )
            except Exception as exc:
                logger.warning("ChromaDB store failed (%s), falling back to dict", exc)
                self._store[key] = text
        else:
            self._store[key] = text
        logger.debug("Memory stored: %s", key)

    def retrieve(self, query: str, top_k: int = 3) -> list[str]:
        """
        Retrieve the most relevant past observations for a query.

        Args:
            query: search string.
            top_k: max number of results.

        Returns:
            list of matching text strings (most relevant first).
        """
        if self._use_chromadb and self._chroma_collection is not None:
            try:
                n = max(1, min(top_k, self._count))
                results = self._chroma_collection.query(
                    query_texts=[query],
                    n_results=n,
                )
                docs = results.get("documents", [[]])[0]
                return [d for d in docs if d]
            except Exception as exc:
                logger.warning("ChromaDB query failed (%s), using dict fallback", exc)

        # Simple fallback: substring match
        query_lower = query.lower()
        matches = []
        for key, text in self._store.items():
            if query_lower in text.lower():
                matches.append(text)
            if len(matches) >= top_k:
                break
        return matches

    def __len__(self) -> int:
        return len(self._store)
