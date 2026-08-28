"""Bounded, freshness-aware caches for ServiceNow metadata."""

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from time import monotonic

from servicenow_mcp.telemetry import CacheEvent, CacheName, HttpTelemetry


_MAX_ENTRIES = 1000


@dataclass(frozen=True, slots=True)
class _CacheEntry[V]:
    value: V
    expires_at: float


class AsyncMetadataCache[K: Hashable, V]:
    """Cache metadata with TTL, bounded storage, and per-key single-flight loads."""

    def __init__(
        self,
        *,
        name: CacheName,
        ttl_seconds: int,
        telemetry: HttpTelemetry | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._name = name
        self._ttl_seconds = ttl_seconds
        self._telemetry = telemetry
        self._clock = clock
        self._entries: OrderedDict[K, _CacheEntry[V]] = OrderedDict()
        self._loads: dict[K, asyncio.Task[V]] = {}
        self._key_versions: dict[K, int] = {}
        self._global_version = 0

    async def get_or_load(self, key: K, loader: Callable[[], Awaitable[V]]) -> V:
        """Return a fresh value, sharing one load among concurrent callers for ``key``."""
        entry = self._entries.get(key)
        if entry is not None:
            if self._clock() < entry.expires_at:
                self._entries.move_to_end(key)
                self._record("hit")
                return entry.value
            self._entries.pop(key)
            self._record("expiration")

        self._record("miss")
        task = self._loads.get(key)
        if task is None:
            key_version = self._key_versions.get(key, 0)
            global_version = self._global_version
            task = asyncio.create_task(self._load(key, loader, key_version, global_version))
            self._loads[key] = task
            task.add_done_callback(self._load_finished_callback(key, key_version))
        return await asyncio.shield(task)

    def contains(self, key: K) -> bool:
        """Return whether ``key`` has an entry, including an expired entry not yet read."""
        return key in self._entries

    def seed(self, key: K, value: V) -> None:
        """Store a value with the configured TTL without running a loader."""
        self._entries[key] = _CacheEntry(value=value, expires_at=self._clock() + self._ttl_seconds)
        self._entries.move_to_end(key)
        if len(self._entries) > _MAX_ENTRIES:
            self._entries.popitem(last=False)

    def invalidate(self, key: K | None = None) -> None:
        """Invalidate one key, or all keys when ``key`` is omitted."""
        self._record("invalidation")
        if key is None:
            self._entries.clear()
            self._global_version += 1
            for load_key in tuple(self._loads):
                self._key_versions[load_key] = self._key_versions.get(load_key, 0) + 1
            self._loads.clear()
            return

        self._entries.pop(key, None)
        if key in self._loads:
            self._key_versions[key] = self._key_versions.get(key, 0) + 1
            self._loads.pop(key)

    def invalidate_where(self, predicate: Callable[[K], bool]) -> None:
        """Invalidate entries and in-flight loads whose keys match ``predicate``."""
        self._record("invalidation")
        matching_keys = {key for key in self._entries if predicate(key)}
        matching_keys.update(key for key in self._loads if predicate(key))
        for key in matching_keys:
            self._entries.pop(key, None)
            if key in self._loads:
                self._key_versions[key] = self._key_versions.get(key, 0) + 1
                self._loads.pop(key)

    async def _load(
        self,
        key: K,
        loader: Callable[[], Awaitable[V]],
        key_version: int,
        global_version: int,
    ) -> V:
        value = await loader()
        if self._global_version == global_version and self._key_versions.get(key, 0) == key_version:
            self._entries[key] = _CacheEntry(value=value, expires_at=self._clock() + self._ttl_seconds)
            self._entries.move_to_end(key)
            if len(self._entries) > _MAX_ENTRIES:
                self._entries.popitem(last=False)
            self._record("reload")
        return value

    def _finish_load(self, key: K, key_version: int, task: asyncio.Task[V]) -> None:
        if self._loads.get(key) is task:
            self._loads.pop(key, None)
            self._key_versions.pop(key, None)
        elif key not in self._loads and self._key_versions.get(key) == key_version:
            self._key_versions.pop(key, None)
        if not task.cancelled():
            task.exception()

    def _load_finished_callback(self, key: K, key_version: int) -> Callable[[asyncio.Task[V]], None]:
        def finish(task: asyncio.Task[V]) -> None:
            self._finish_load(key, key_version, task)

        return finish

    def _record(self, event: CacheEvent) -> None:
        if self._telemetry is not None:
            self._telemetry.record_cache_event(self._name, event)
