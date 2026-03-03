"""Tests for in-memory state management (PreviewTokenStore, QueryTokenStore)."""

from unittest.mock import patch

import pytest

from servicenow_mcp.state import PreviewTokenStore, QueryTokenStore


class TestPreviewTokenStore:
    """Tests for PreviewTokenStore."""

    def test_create_returns_token_string(self):
        """create() returns a UUID-style string token."""
        store = PreviewTokenStore(ttl_seconds=300)
        token = store.create({"table": "incident", "sys_id": "abc", "changes": {"state": "2"}})
        assert isinstance(token, str)
        assert len(token) > 0

    def test_get_returns_stored_payload(self):
        """get() returns the payload stored for a valid token."""
        store = PreviewTokenStore(ttl_seconds=300)
        payload = {"table": "incident", "sys_id": "abc", "changes": {"state": "2"}}
        token = store.create(payload)
        result = store.get(token)
        assert result is not None
        assert result["table"] == "incident"
        assert result["changes"] == {"state": "2"}

    def test_get_returns_none_for_unknown_token(self):
        """get() returns None for a token that was never created."""
        store = PreviewTokenStore(ttl_seconds=300)
        result = store.get("nonexistent-token")
        assert result is None

    def test_get_returns_none_for_expired_token(self):
        """get() returns None for a token that has expired (mocked clock)."""
        fake_time = 1000.0
        with patch("servicenow_mcp.state.time.monotonic", side_effect=lambda: fake_time):
            store = PreviewTokenStore(ttl_seconds=60)
            token = store.create({"table": "incident"})

        # Advance time past TTL
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 61):
            result = store.get(token)

        assert result is None

    def test_consume_returns_payload_and_removes(self):
        """consume() returns the payload and removes the token from the store."""
        store = PreviewTokenStore(ttl_seconds=300)
        payload = {"table": "incident", "sys_id": "abc"}
        token = store.create(payload)

        result = store.consume(token)
        assert result is not None
        assert result["table"] == "incident"

        # Token should be gone now
        assert store.get(token) is None

    def test_consume_returns_none_for_expired_token(self):
        """consume() returns None for an expired token (mocked clock)."""
        fake_time = 1000.0
        with patch("servicenow_mcp.state.time.monotonic", side_effect=lambda: fake_time):
            store = PreviewTokenStore(ttl_seconds=60)
            token = store.create({"table": "incident"})

        # Advance time past TTL
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 61):
            result = store.consume(token)

        assert result is None

    def test_get_returns_payload_before_ttl_expires(self):
        """get() returns the payload when the TTL has not yet expired (mocked clock)."""
        fake_time = 1000.0
        with patch("servicenow_mcp.state.time.monotonic", side_effect=lambda: fake_time):
            store = PreviewTokenStore(ttl_seconds=60)
            token = store.create({"table": "incident", "key": "value"})

        # Advance time but stay within TTL
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 59):
            result = store.get(token)

        assert result is not None
        assert result["table"] == "incident"

    def test_consume_at_exact_boundary(self):
        """consume() at exact TTL boundary is NOT expired (uses > not >=)."""
        fake_time = 1000.0
        with patch("servicenow_mcp.state.time.monotonic", side_effect=lambda: fake_time):
            store = PreviewTokenStore(ttl_seconds=60)
            token = store.create({"table": "incident"})

        # Advance time to exactly TTL — _is_expired uses >, so 60 == 60 is NOT expired
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 60):
            result_at_boundary = store.consume(token)

        assert result_at_boundary is not None

    def test_store_max_size(self):
        """create() raises RuntimeError when max_size is reached and no entries are expired."""
        store = PreviewTokenStore(ttl_seconds=300, max_size=3)
        store.create({"table": "incident"})
        store.create({"table": "problem"})
        store.create({"table": "change_request"})

        with pytest.raises(RuntimeError, match="store is full"):
            store.create({"table": "kb_knowledge"})

    def test_sweep_expired_on_create(self):
        """create() sweeps expired entries first, allowing new tokens when space is freed."""
        fake_time = 1000.0
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time):
            store = PreviewTokenStore(ttl_seconds=60, max_size=2)
            store.create({"table": "incident"})
            store.create({"table": "problem"})

        # Advance time past TTL — expired entries should be swept on next create()
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 61):
            token = store.create({"table": "change_request"})
            result = store.get(token)

        assert result is not None
        assert result["table"] == "change_request"

    def test_sweep_expired_frees_space(self):
        """_sweep_expired() removes expired entries, reducing store size."""
        fake_time = 1000.0
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time):
            store = PreviewTokenStore(ttl_seconds=60, max_size=10)
            store.create({"table": "incident"})
            store.create({"table": "problem"})
            store.create({"table": "change_request"})

        assert len(store._store) == 3

        # Advance time past TTL and sweep
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 61):
            store._sweep_expired()

        assert len(store._store) == 0


class TestQueryTokenStore:
    """Tests for QueryTokenStore."""

    def test_create_returns_token_string(self):
        """create() returns a UUID-style string token."""
        store = QueryTokenStore(ttl_seconds=300)
        token = store.create({"query": "active=true"})
        assert isinstance(token, str)
        assert len(token) > 0

    def test_get_returns_stored_payload(self):
        """get() returns the payload stored for a valid token."""
        store = QueryTokenStore(ttl_seconds=300)
        payload = {"query": "active=true"}
        token = store.create(payload)
        result = store.get(token)
        assert result is not None
        assert result["query"] == "active=true"

    def test_get_returns_none_for_unknown_token(self):
        """get() returns None for a token that was never created."""
        store = QueryTokenStore(ttl_seconds=300)
        result = store.get("nonexistent-token")
        assert result is None

    def test_get_returns_none_for_expired_token(self):
        """get() returns None for a token that has expired (mocked clock)."""
        fake_time = 1000.0
        with patch("servicenow_mcp.state.time.monotonic", side_effect=lambda: fake_time):
            store = QueryTokenStore(ttl_seconds=60)
            token = store.create({"query": "active=true"})

        # Advance time past TTL
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 61):
            result = store.get(token)

        assert result is None

    def test_get_is_reusable_within_ttl(self):
        """get() succeeds multiple times for the same token (non-destructive)."""
        store = QueryTokenStore(ttl_seconds=300)
        token = store.create({"query": "active=true"})

        result1 = store.get(token)
        result2 = store.get(token)
        result3 = store.get(token)

        assert result1 is not None
        assert result2 is not None
        assert result3 is not None
        assert result1["query"] == "active=true"
        assert result2["query"] == "active=true"
        assert result3["query"] == "active=true"

    def test_get_returns_payload_before_ttl_expires(self):
        """get() returns the payload when the TTL has not yet expired (mocked clock)."""
        fake_time = 1000.0
        with patch("servicenow_mcp.state.time.monotonic", side_effect=lambda: fake_time):
            store = QueryTokenStore(ttl_seconds=60)
            token = store.create({"query": "active=true"})

        # Advance time but stay within TTL
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 59):
            result = store.get(token)

        assert result is not None
        assert result["query"] == "active=true"

    def test_get_at_exact_boundary(self):
        """get() at exact TTL boundary is NOT expired (uses > not >=)."""
        fake_time = 1000.0
        with patch("servicenow_mcp.state.time.monotonic", side_effect=lambda: fake_time):
            store = QueryTokenStore(ttl_seconds=60)
            token = store.create({"query": "active=true"})

        # Advance time to exactly TTL -- _is_expired uses >, so 60 == 60 is NOT expired
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 60):
            result = store.get(token)

        assert result is not None

    def test_store_max_size(self):
        """create() raises RuntimeError when max_size is reached and no entries are expired."""
        store = QueryTokenStore(ttl_seconds=300, max_size=3)
        store.create({"query": "active=true"})
        store.create({"query": "state=1"})
        store.create({"query": "priority=1"})

        with pytest.raises(RuntimeError, match="store is full"):
            store.create({"query": "impact=1"})

    def test_sweep_expired_on_create(self):
        """create() sweeps expired entries first, allowing new tokens when space is freed."""
        fake_time = 1000.0
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time):
            store = QueryTokenStore(ttl_seconds=60, max_size=2)
            store.create({"query": "active=true"})
            store.create({"query": "state=1"})

        # Advance time past TTL -- expired entries should be swept on next create()
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 61):
            token = store.create({"query": "priority=1"})
            result = store.get(token)

        assert result is not None
        assert result["query"] == "priority=1"

    def test_sweep_expired_frees_space(self):
        """_sweep_expired() removes expired entries, reducing store size."""
        fake_time = 1000.0
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time):
            store = QueryTokenStore(ttl_seconds=60, max_size=10)
            store.create({"query": "active=true"})
            store.create({"query": "state=1"})
            store.create({"query": "priority=1"})

        assert len(store._store) == 3

        # Advance time past TTL and sweep
        with patch("servicenow_mcp.state.time.monotonic", return_value=fake_time + 61):
            store._sweep_expired()

        assert len(store._store) == 0
