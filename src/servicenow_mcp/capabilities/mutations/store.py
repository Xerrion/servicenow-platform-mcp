"""Bounded memory-only staging with atomic token consumption."""

import asyncio
import time
import uuid
from dataclasses import dataclass

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.mutations.models import StagedMutation, retained_bytes


@dataclass(frozen=True)
class _Entry:
    mutation: StagedMutation
    created_at: float
    size: int


class PreviewTokenStore:
    def __init__(
        self,
        ttl_seconds: int = 300,
        max_size: int = 1000,
        max_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        if ttl_seconds <= 0 or max_size <= 0 or max_bytes <= 0:
            raise ValueError("Preview store limits must be positive.")
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self.max_bytes = max_bytes
        self._entries: dict[str, _Entry] = {}
        self._bytes = 0
        self._lock = asyncio.Lock()

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def retained_payload_bytes(self) -> int:
        return self._bytes

    def _remove(self, token: str) -> _Entry | None:
        entry = self._entries.pop(token, None)
        if entry is not None:
            self._bytes -= entry.size
        return entry

    def _sweep(self) -> None:
        now = time.monotonic()
        for token, entry in list(self._entries.items()):
            if now - entry.created_at > self.ttl_seconds:
                self._remove(token)

    async def create(self, mutation: StagedMutation) -> str:
        owned = mutation.model_copy(deep=True)
        size = retained_bytes(owned)
        async with self._lock:
            self._sweep()
            if (
                len(self._entries) >= self.max_size
                or self._bytes + size > self.max_bytes
            ):
                raise CapabilityError(
                    "Preview capacity reached. Apply existing previews or wait for expiry.",
                    "PREVIEW_CAPACITY",
                )
            token = str(uuid.uuid4())
            self._entries[token] = _Entry(owned, time.monotonic(), size)
            self._bytes += size
            return token

    async def consume(self, token: str) -> StagedMutation:
        async with self._lock:
            self._sweep()
            entry = self._remove(token)
            if entry is None:
                raise CapabilityError(
                    "Invalid or expired preview token. Create a new preview.",
                    "PREVIEW_EXPIRED",
                )
            return entry.mutation

    async def clear(self) -> None:
        async with self._lock:
            self._entries.clear()
            self._bytes = 0
