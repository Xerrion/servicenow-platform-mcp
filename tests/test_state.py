"""Bounded preview ownership, expiry, and single-use concurrency."""

import asyncio
from unittest.mock import patch

import pytest

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.mutations.models import (
    RecordCommand,
    StagedMutation,
    retained_bytes,
)
from servicenow_mcp.capabilities.mutations.store import PreviewTokenStore


def staged() -> StagedMutation:
    return StagedMutation(
        command=RecordCommand(
            action="create", table="incident", data={"nested": {"value": 1}}
        ),
        instance="https://test.service-now.com",
        authorization_generation=1,
    )


async def test_one_concurrent_consumer_wins() -> None:
    store = PreviewTokenStore()
    token = await store.create(staged())
    results = await asyncio.gather(
        *(store.consume(token) for _ in range(10)), return_exceptions=True
    )
    assert sum(isinstance(value, StagedMutation) for value in results) == 1
    assert sum(isinstance(value, CapabilityError) for value in results) == 9
    assert len(store) == 0


async def test_entry_capacity_and_expiry_reclamation() -> None:
    store = PreviewTokenStore(max_size=1)
    with patch(
        "servicenow_mcp.capabilities.mutations.store.time.monotonic", return_value=1000
    ):
        await store.create(staged())
        with pytest.raises(CapabilityError, match="capacity"):
            await store.create(staged())
    with patch(
        "servicenow_mcp.capabilities.mutations.store.time.monotonic", return_value=1301
    ):
        assert await store.create(staged())
    assert len(store) == 1
    await store.clear()
    assert len(store) == store.retained_payload_bytes == 0


async def test_exact_retained_byte_capacity() -> None:
    size = retained_bytes(staged())
    store = PreviewTokenStore(max_bytes=size)
    token = await store.create(staged())
    assert store.retained_payload_bytes == size
    with pytest.raises(CapabilityError, match="capacity"):
        await store.create(staged())
    await store.consume(token)
    assert await store.create(staged())
    too_small = PreviewTokenStore(max_bytes=size - 1)
    with pytest.raises(CapabilityError, match="capacity"):
        await too_small.create(staged())
