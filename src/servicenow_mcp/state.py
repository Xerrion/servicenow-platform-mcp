"""In-memory state stores for preview tokens and query tokens."""

import asyncio
import time
import uuid
from typing import Any


__all__ = ["PreviewTokenStore", "QueryTokenStore"]


class _BaseTokenStore:
    """Base class for UUID-keyed, TTL-expiring in-memory token stores.

    Provides create/get lifecycle with automatic expiry sweeping.
    Subclasses set ``_store_label`` to customize the full-store error message.

    All public mutating methods are ``async`` and serialize access through an
    ``asyncio.Lock`` so that read-then-write sequences (sweep + capacity check
    + insert, expiry check + pop) are atomic with respect to other coroutines.
    """

    _store_label: str = "Token"
    _ttl: int
    _max_size: int

    def __init__(self, ttl_seconds: int = 300, max_size: int = 1000) -> None:
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._store: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def __len__(self) -> int:
        """Return the number of entries currently in the store."""
        return len(self._store)

    async def create(self, payload: dict[str, Any]) -> str:
        """Store a payload and return a new UUID token.

        Raises RuntimeError if the store is full after sweeping expired entries.
        """
        async with self._lock:
            self._sweep_expired_locked()
            if len(self._store) >= self._max_size:
                raise RuntimeError(f"{self._store_label} store is full")
            token = str(uuid.uuid4())
            self._store[token] = {
                "payload": payload,
                "created_at": time.monotonic(),
            }
            return token

    async def _sweep_expired(self) -> None:
        """Remove all expired entries from the store (locked variant)."""
        async with self._lock:
            self._sweep_expired_locked()

    def _sweep_expired_locked(self) -> None:
        """Remove all expired entries; caller must already hold ``self._lock``."""
        now = time.monotonic()
        expired_keys = [k for k, entry in self._store.items() if (now - entry["created_at"]) > self._ttl]
        for k in expired_keys:
            self._store.pop(k, None)

    def _is_expired(self, entry: dict[str, Any]) -> bool:
        """Check if a store entry has exceeded its TTL."""
        return (time.monotonic() - entry["created_at"]) > self._ttl

    async def get(self, token: str) -> dict[str, Any] | None:
        """Return the payload for a valid, non-expired token, or None."""
        async with self._lock:
            entry = self._store.get(token)
            if entry is None:
                return None
            if self._is_expired(entry):
                self._store.pop(token, None)
                return None
            return entry["payload"]


class PreviewTokenStore(_BaseTokenStore):
    """In-memory store for preview/apply tokens with TTL.

    Tokens are UUID strings mapped to payloads (table, sys_id, changes).
    Expired tokens are automatically rejected on get/consume.
    """

    _store_label: str = "Preview token"

    async def consume(self, token: str) -> dict[str, Any] | None:
        """Return the payload and remove the token. Returns None if expired/missing.

        The lookup, expiry check, and removal happen under a single lock
        acquisition so that exactly one concurrent consumer wins.
        """
        async with self._lock:
            entry = self._store.get(token)
            if entry is None:
                return None
            if self._is_expired(entry):
                self._store.pop(token, None)
                return None
            # Atomic pop under the lock; no other coroutine can race us.
            self._store.pop(token, None)
            return entry["payload"]


class QueryTokenStore(_BaseTokenStore):
    """In-memory store for query tokens with TTL.

    Tokens are UUID strings mapped to validated query payloads.
    Unlike PreviewTokenStore, tokens are reusable within their TTL.
    Expired tokens are automatically rejected on get.
    """

    _store_label: str = "Query token"
