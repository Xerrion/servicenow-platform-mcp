"""Tests for freshness-aware metadata caches."""

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from servicenow_mcp.metadata_cache import AsyncMetadataCache
from servicenow_mcp.telemetry import CacheName, HttpTelemetry


def _counter(snapshot: HttpTelemetry, name: CacheName, field: str) -> int:
    return getattr(snapshot.snapshot().metadata_caches[name.value], field)


@pytest.mark.asyncio()
async def test_hit_miss_expiration_reload_and_invalidation_counters() -> None:
    """A cache reports each lifecycle event with one fixed cache name."""
    now = [10.0]
    telemetry = HttpTelemetry()
    cache = AsyncMetadataCache[str, str](
        name=CacheName.DICTIONARY_CHAINS,
        ttl_seconds=5,
        telemetry=telemetry,
        clock=lambda: now[0],
    )
    values = iter(["first", "second", "third"])

    async def load() -> str:
        return next(values)

    assert await cache.get_or_load("incident", load) == "first"
    assert await cache.get_or_load("incident", load) == "first"
    now[0] = 15.0
    assert await cache.get_or_load("incident", load) == "second"
    cache.invalidate("incident")
    assert await cache.get_or_load("incident", load) == "third"

    assert _counter(telemetry, CacheName.DICTIONARY_CHAINS, "hits") == 1
    assert _counter(telemetry, CacheName.DICTIONARY_CHAINS, "misses") == 3
    assert _counter(telemetry, CacheName.DICTIONARY_CHAINS, "expirations") == 1
    assert _counter(telemetry, CacheName.DICTIONARY_CHAINS, "reloads") == 3
    assert _counter(telemetry, CacheName.DICTIONARY_CHAINS, "invalidations") == 1


@pytest.mark.asyncio()
async def test_same_key_is_single_flight_and_independent_keys_load_together() -> None:
    """Same-key callers share a load while separate keys do not block each other."""
    cache = AsyncMetadataCache[str, str](name=CacheName.DICTIONARY_FIELDS, ttl_seconds=30)
    entered: set[str] = set()
    release = asyncio.Event()

    def make_loader(key: str) -> Callable[[], Awaitable[str]]:
        async def load() -> str:
            entered.add(key)
            await release.wait()
            return key

        return load

    first = asyncio.create_task(cache.get_or_load("incident", make_loader("incident")))
    duplicate = asyncio.create_task(cache.get_or_load("incident", make_loader("duplicate")))
    independent = asyncio.create_task(cache.get_or_load("problem", make_loader("problem")))
    for _ in range(3):
        await asyncio.sleep(0)
        if entered == {"incident", "problem"}:
            break

    assert entered == {"incident", "problem"}
    release.set()
    assert await asyncio.gather(first, duplicate, independent) == ["incident", "incident", "problem"]


@pytest.mark.asyncio()
async def test_failed_load_is_not_cached_and_waiters_receive_failure() -> None:
    """A shared failed load propagates and the next call can recover."""
    telemetry = HttpTelemetry()
    cache = AsyncMetadataCache[str, str](
        name=CacheName.AUDIT_FIELD_CONFIG,
        ttl_seconds=30,
        telemetry=telemetry,
    )
    attempts = 0

    async def load() -> str:
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(0)
        if attempts == 1:
            raise RuntimeError("load failed")
        return "recovered"

    first = asyncio.create_task(cache.get_or_load("key", load))
    second = asyncio.create_task(cache.get_or_load("key", load))
    results = await asyncio.gather(first, second, return_exceptions=True)

    assert all(isinstance(result, RuntimeError) for result in results)
    assert await cache.get_or_load("key", load) == "recovered"
    assert attempts == 2
    assert _counter(telemetry, CacheName.AUDIT_FIELD_CONFIG, "reloads") == 1


@pytest.mark.asyncio()
async def test_invalidation_during_load_prevents_stale_value_storage() -> None:
    """A load that crosses invalidation returns to its caller but does not repopulate the cache."""
    telemetry = HttpTelemetry()
    cache = AsyncMetadataCache[str, str](
        name=CacheName.AUDIT_TABLE_CONFIG,
        ttl_seconds=30,
        telemetry=telemetry,
    )
    release = asyncio.Event()
    loads = 0

    async def load() -> str:
        nonlocal loads
        loads += 1
        if loads == 1:
            await release.wait()
            return "stale"
        return "fresh"

    in_flight = asyncio.create_task(cache.get_or_load("incident", load))
    for _ in range(3):
        await asyncio.sleep(0)
    cache.invalidate("incident")
    release.set()

    assert await in_flight == "stale"
    assert await cache.get_or_load("incident", load) == "fresh"
    assert loads == 2
    assert _counter(telemetry, CacheName.AUDIT_TABLE_CONFIG, "reloads") == 1


@pytest.mark.asyncio()
async def test_replacement_load_stays_valid_when_invalidated_load_finishes_last() -> None:
    """An older invalidated load cannot clear the replacement load's version guard."""
    cache = AsyncMetadataCache[str, str](name=CacheName.AUDIT_TABLE_CONFIG, ttl_seconds=30)
    release_old = asyncio.Event()
    release_new = asyncio.Event()

    async def load_old() -> str:
        await release_old.wait()
        return "old"

    async def load_new() -> str:
        await release_new.wait()
        return "new"

    old = asyncio.create_task(cache.get_or_load("incident", load_old))
    for _ in range(3):
        await asyncio.sleep(0)
    cache.invalidate("incident")
    new = asyncio.create_task(cache.get_or_load("incident", load_new))
    for _ in range(3):
        await asyncio.sleep(0)

    release_old.set()
    assert await old == "old"
    release_new.set()
    assert await new == "new"
    assert await cache.get_or_load("incident", load_old) == "new"


@pytest.mark.asyncio()
async def test_cache_can_cross_event_loops_without_lock_affinity() -> None:
    """A cache created outside a loop can serve calls from separate event loops."""
    cache = AsyncMetadataCache[str, str](name=CacheName.CHOICES, ttl_seconds=30)

    async def load() -> str:
        return "value"

    assert await cache.get_or_load("all", load) == "value"

    def use_new_loop() -> str:
        return asyncio.run(cache.get_or_load("all", load))

    assert await asyncio.to_thread(use_new_loop) == "value"


@pytest.mark.asyncio()
async def test_entry_count_stays_bounded() -> None:
    """Dynamic table keys cannot grow a metadata cache without a fixed bound."""
    cache = AsyncMetadataCache[int, int](name=CacheName.DICTIONARY_FIELDS, ttl_seconds=30)

    for key in range(1001):
        await cache.get_or_load(key, lambda value=key: _return(value))

    assert len(cache._entries) == 1000


async def _return(value: int) -> int:
    return value


def test_cache_telemetry_has_only_fixed_low_cardinality_names() -> None:
    """Cache telemetry cannot accept dynamic table or record identifiers."""
    telemetry = HttpTelemetry()
    telemetry.record_cache_event(CacheName.CHOICES, "hit")

    context = telemetry.cache_sentry_context()
    assert set(context) == {name.value for name in CacheName}
    assert "incident" not in str(context)
    assert "abc123" not in str(context)
