"""Tests for in-memory state management (PreviewTokenStore, QueryTokenStore)."""

import asyncio
from unittest.mock import patch

import pytest

from servicenow_mcp.state import PreviewTokenStore, QueryTokenStore


class TestPreviewTokenStore:
    """Tests for PreviewTokenStore."""

    @pytest.mark.asyncio()
    async def test_create_returns_token_string(self) -> None:
        """create() returns a UUID-style string token."""
        store = PreviewTokenStore(ttl_seconds=300)
        token = await store.create({"table": "incident", "sys_id": "abc", "changes": {"state": "2"}})
        assert isinstance(token, str)
        assert len(token) > 0

    @pytest.mark.asyncio()
    async def test_get_returns_stored_payload(self) -> None:
        """get() returns the payload stored for a valid token."""
        store = PreviewTokenStore(ttl_seconds=300)
        payload = {
            "table": "incident",
            "sys_id": "abc",
            "changes": {"state": "2"},
        }
        token = await store.create(payload)
        result = await store.get(token)
        assert result is not None
        assert result["table"] == "incident"
        assert result["changes"] == {"state": "2"}

    @pytest.mark.asyncio()
    async def test_get_returns_none_for_unknown_token(self) -> None:
        """get() returns None for a token that was never created."""
        store = PreviewTokenStore(ttl_seconds=300)
        result = await store.get("nonexistent-token")
        assert result is None

    @pytest.mark.asyncio()
    async def test_get_returns_none_for_expired_token(self) -> None:
        """get() returns None for a token that has expired (mocked clock)."""
        fake_time = 1000.0
        with patch(
            "servicenow_mcp.state.time.monotonic",
            side_effect=lambda: fake_time,
        ):
            store = PreviewTokenStore(ttl_seconds=60)
            token = await store.create({"table": "incident"})

        # Advance time past TTL
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 61):
            result = await store.get(token)

        assert result is None

    @pytest.mark.asyncio()
    async def test_consume_returns_payload_and_removes(self) -> None:
        """consume() returns the payload and removes the token from the store."""
        store = PreviewTokenStore(ttl_seconds=300)
        payload = {"table": "incident", "sys_id": "abc"}
        token = await store.create(payload)

        result = await store.consume(token)
        assert result is not None
        assert result["table"] == "incident"

        # Token should be gone now
        assert await store.get(token) is None

    @pytest.mark.asyncio()
    async def test_consume_returns_none_for_expired_token(self) -> None:
        """consume() returns None for an expired token (mocked clock)."""
        fake_time = 1000.0
        with patch(
            "servicenow_mcp.state.time.monotonic",
            side_effect=lambda: fake_time,
        ):
            store = PreviewTokenStore(ttl_seconds=60)
            token = await store.create({"table": "incident"})

        # Advance time past TTL
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 61):
            result = await store.consume(token)

        assert result is None

    @pytest.mark.asyncio()
    async def test_get_returns_payload_before_ttl_expires(self) -> None:
        """get() returns the payload when the TTL has not yet expired (mocked clock)."""
        fake_time = 1000.0
        with patch(
            "servicenow_mcp.state.time.monotonic",
            side_effect=lambda: fake_time,
        ):
            store = PreviewTokenStore(ttl_seconds=60)
            token = await store.create({"table": "incident", "key": "value"})

        # Advance time but stay within TTL
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 59):
            result = await store.get(token)

        assert result is not None
        assert result["table"] == "incident"

    @pytest.mark.asyncio()
    async def test_consume_at_exact_boundary(self) -> None:
        """consume() at exact TTL boundary is NOT expired (uses > not >=)."""
        fake_time = 1000.0
        with patch(
            "servicenow_mcp.state.time.monotonic",
            side_effect=lambda: fake_time,
        ):
            store = PreviewTokenStore(ttl_seconds=60)
            token = await store.create({"table": "incident"})

        # Advance time to exactly TTL — _is_expired uses >, so 60 == 60 is NOT expired
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 60):
            result_at_boundary = await store.consume(token)

        assert result_at_boundary is not None

    @pytest.mark.asyncio()
    async def test_store_max_size(self) -> None:
        """create() raises RuntimeError when max_size is reached and no entries are expired."""
        store = PreviewTokenStore(ttl_seconds=300, max_size=3)
        await store.create({"table": "incident"})
        await store.create({"table": "problem"})
        await store.create({"table": "change_request"})

        with pytest.raises(RuntimeError, match="store is full"):
            await store.create({"table": "kb_knowledge"})

    @pytest.mark.asyncio()
    async def test_sweep_expired_on_create(self) -> None:
        """create() sweeps expired entries first, allowing new tokens when space is freed."""
        fake_time = 1000.0
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time):
            store = PreviewTokenStore(ttl_seconds=60, max_size=2)
            await store.create({"table": "incident"})
            await store.create({"table": "problem"})

        # Advance time past TTL — expired entries should be swept on next create()
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 61):
            token = await store.create({"table": "change_request"})
            result = await store.get(token)

        assert result is not None
        assert result["table"] == "change_request"

    @pytest.mark.asyncio()
    async def test_sweep_expired_frees_space(self) -> None:
        """_sweep_expired() removes expired entries, reducing store size."""
        fake_time = 1000.0
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time):
            store = PreviewTokenStore(ttl_seconds=60, max_size=10)
            await store.create({"table": "incident"})
            await store.create({"table": "problem"})
            await store.create({"table": "change_request"})

        assert len(store) == 3

        # Advance time past TTL and sweep
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 61):
            await store._sweep_expired()

        assert len(store) == 0


class TestQueryTokenStore:
    """Tests for QueryTokenStore."""

    @pytest.mark.asyncio()
    async def test_create_returns_token_string(self) -> None:
        """create() returns a UUID-style string token."""
        store = QueryTokenStore(ttl_seconds=300)
        token = await store.create({"query": "active=true"})
        assert isinstance(token, str)
        assert len(token) > 0

    @pytest.mark.asyncio()
    async def test_get_returns_stored_payload(self) -> None:
        """get() returns the payload stored for a valid token."""
        store = QueryTokenStore(ttl_seconds=300)
        payload = {"query": "active=true"}
        token = await store.create(payload)
        result = await store.get(token)
        assert result is not None
        assert result["query"] == "active=true"

    @pytest.mark.asyncio()
    async def test_get_returns_none_for_unknown_token(self) -> None:
        """get() returns None for a token that was never created."""
        store = QueryTokenStore(ttl_seconds=300)
        result = await store.get("nonexistent-token")
        assert result is None

    @pytest.mark.asyncio()
    async def test_get_returns_none_for_expired_token(self) -> None:
        """get() returns None for a token that has expired (mocked clock)."""
        fake_time = 1000.0
        with patch(
            "servicenow_mcp.state.time.monotonic",
            side_effect=lambda: fake_time,
        ):
            store = QueryTokenStore(ttl_seconds=60)
            token = await store.create({"query": "active=true"})

        # Advance time past TTL
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 61):
            result = await store.get(token)

        assert result is None

    @pytest.mark.asyncio()
    async def test_get_is_reusable_within_ttl(self) -> None:
        """get() succeeds multiple times for the same token (non-destructive)."""
        store = QueryTokenStore(ttl_seconds=300)
        token = await store.create({"query": "active=true"})

        result1 = await store.get(token)
        result2 = await store.get(token)
        result3 = await store.get(token)

        assert result1 is not None
        assert result2 is not None
        assert result3 is not None
        assert result1["query"] == "active=true"
        assert result2["query"] == "active=true"
        assert result3["query"] == "active=true"

    @pytest.mark.asyncio()
    async def test_get_returns_payload_before_ttl_expires(self) -> None:
        """get() returns the payload when the TTL has not yet expired (mocked clock)."""
        fake_time = 1000.0
        with patch(
            "servicenow_mcp.state.time.monotonic",
            side_effect=lambda: fake_time,
        ):
            store = QueryTokenStore(ttl_seconds=60)
            token = await store.create({"query": "active=true"})

        # Advance time but stay within TTL
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 59):
            result = await store.get(token)

        assert result is not None
        assert result["query"] == "active=true"

    @pytest.mark.asyncio()
    async def test_get_at_exact_boundary(self) -> None:
        """get() at exact TTL boundary is NOT expired (uses > not >=)."""
        fake_time = 1000.0
        with patch(
            "servicenow_mcp.state.time.monotonic",
            side_effect=lambda: fake_time,
        ):
            store = QueryTokenStore(ttl_seconds=60)
            token = await store.create({"query": "active=true"})

        # Advance time to exactly TTL -- _is_expired uses >, so 60 == 60 is NOT expired
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 60):
            result = await store.get(token)

        assert result is not None

    @pytest.mark.asyncio()
    async def test_store_max_size(self) -> None:
        """create() raises RuntimeError when max_size is reached and no entries are expired."""
        store = QueryTokenStore(ttl_seconds=300, max_size=3)
        await store.create({"query": "active=true"})
        await store.create({"query": "state=1"})
        await store.create({"query": "priority=1"})

        with pytest.raises(RuntimeError, match="store is full"):
            await store.create({"query": "impact=1"})

    @pytest.mark.asyncio()
    async def test_sweep_expired_on_create(self) -> None:
        """create() sweeps expired entries first, allowing new tokens when space is freed."""
        fake_time = 1000.0
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time):
            store = QueryTokenStore(ttl_seconds=60, max_size=2)
            await store.create({"query": "active=true"})
            await store.create({"query": "state=1"})

        # Advance time past TTL -- expired entries should be swept on next create()
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 61):
            token = await store.create({"query": "priority=1"})
            result = await store.get(token)

        assert result is not None
        assert result["query"] == "priority=1"

    @pytest.mark.asyncio()
    async def test_sweep_expired_frees_space(self) -> None:
        """_sweep_expired() removes expired entries, reducing store size."""
        fake_time = 1000.0
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time):
            store = QueryTokenStore(ttl_seconds=60, max_size=10)
            await store.create({"query": "active=true"})
            await store.create({"query": "state=1"})
            await store.create({"query": "priority=1"})

        assert len(store) == 3

        # Advance time past TTL and sweep
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 61):
            await store._sweep_expired()

        assert len(store) == 0


class TestConcurrentConsume:
    """Exactly-one-winner regression for PreviewTokenStore.consume()."""

    @pytest.mark.asyncio()
    async def test_concurrent_consume_yields_one_winner(self) -> None:
        """10 concurrent consume() calls produce exactly one payload, no exceptions.

        With the asyncio.Lock-protected store, the lookup-and-pop sequence is
        atomic, so even though gathered coroutines interleave at every await,
        only one wins.
        """
        store = PreviewTokenStore(ttl_seconds=300)
        token = await store.create({"table": "incident", "sys_id": "abc"})

        async def consume_one() -> dict | None:
            await asyncio.sleep(0)
            return await store.consume(token)

        results = await asyncio.gather(*(consume_one() for _ in range(10)))

        winners = [r for r in results if r is not None]
        losers = [r for r in results if r is None]

        assert len(winners) == 1
        assert len(losers) == 9
        assert winners[0]["table"] == "incident"
        # Token must be gone now.
        assert await store.get(token) is None
