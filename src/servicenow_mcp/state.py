"""In-memory state stores for preview tokens and query tokens."""

import time
import uuid
from typing import Any


class PreviewTokenStore:
    """In-memory store for preview/apply tokens with TTL.

    Tokens are UUID strings mapped to payloads (table, sys_id, changes).
    Expired tokens are automatically rejected on get/consume.
    """

    def __init__(self, ttl_seconds: int = 300, max_size: int = 1000) -> None:
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._store: dict[str, dict[str, Any]] = {}

    def create(self, payload: dict[str, Any]) -> str:
        """Store a payload and return a new UUID token.

        Raises RuntimeError if the store is full after sweeping expired entries.
        """
        self._sweep_expired()
        if len(self._store) >= self._max_size:
            raise RuntimeError("Preview token store is full")
        token = str(uuid.uuid4())
        self._store[token] = {
            "payload": payload,
            "created_at": time.monotonic(),
        }
        return token

    def _sweep_expired(self) -> None:
        """Remove all expired entries from the store."""
        now = time.monotonic()
        expired_keys = [k for k, entry in self._store.items() if (now - entry["created_at"]) > self._ttl]
        for k in expired_keys:
            del self._store[k]

    def _is_expired(self, entry: dict[str, Any]) -> bool:
        """Check if a store entry has exceeded its TTL."""
        return (time.monotonic() - entry["created_at"]) > self._ttl

    def get(self, token: str) -> dict[str, Any] | None:
        """Return the payload for a valid, non-expired token, or None."""
        entry = self._store.get(token)
        if entry is None:
            return None
        if self._is_expired(entry):
            del self._store[token]
            return None
        return entry["payload"]

    def consume(self, token: str) -> dict[str, Any] | None:
        """Return the payload and remove the token. Returns None if expired/missing."""
        entry = self._store.get(token)
        if entry is None:
            return None
        if self._is_expired(entry):
            del self._store[token]
            return None
        del self._store[token]
        return entry["payload"]


class QueryTokenStore:
    """In-memory store for query tokens with TTL.

    Tokens are UUID strings mapped to validated query payloads.
    Unlike PreviewTokenStore, tokens are reusable within their TTL.
    Expired tokens are automatically rejected on get.
    """

    def __init__(self, ttl_seconds: int = 300, max_size: int = 1000) -> None:
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._store: dict[str, dict[str, Any]] = {}

    def create(self, payload: dict[str, Any]) -> str:
        """Store a payload and return a new UUID token.

        Raises RuntimeError if the store is full after sweeping expired entries.
        """
        self._sweep_expired()
        if len(self._store) >= self._max_size:
            raise RuntimeError("Query token store is full")
        token = str(uuid.uuid4())
        self._store[token] = {
            "payload": payload,
            "created_at": time.monotonic(),
        }
        return token

    def _sweep_expired(self) -> None:
        """Remove all expired entries from the store."""
        now = time.monotonic()
        expired_keys = [k for k, entry in self._store.items() if (now - entry["created_at"]) > self._ttl]
        for k in expired_keys:
            del self._store[k]

    def _is_expired(self, entry: dict[str, Any]) -> bool:
        """Check if a store entry has exceeded its TTL."""
        return (time.monotonic() - entry["created_at"]) > self._ttl

    def get(self, token: str) -> dict[str, Any] | None:
        """Return the payload for a valid, non-expired token, or None."""
        entry = self._store.get(token)
        if entry is None:
            return None
        if self._is_expired(entry):
            del self._store[token]
            return None
        return entry["payload"]
