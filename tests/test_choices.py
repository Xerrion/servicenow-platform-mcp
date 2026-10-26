"""Tests for ChoiceRegistry lazy-loaded choice list cache."""

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from httpx2 import Response

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.platform.metadata.choices import (
    ChoiceRegistry,
    _group_choice_records,
    _merge_with_defaults,
)
from tests._mock_transport import http_mock

if TYPE_CHECKING:
    from servicenow_mcp.config import Settings


BASE_URL = "https://test.service-now.com"


@pytest.fixture()
def auth_provider(settings: Settings) -> OAuthPKCEProvider:
    """Test auth provider fixture."""
    return OAuthPKCEProvider(settings)


def _make_registry_with_defaults(
    settings: Settings, auth_provider: OAuthPKCEProvider
) -> ChoiceRegistry:
    """Create a ChoiceRegistry pre-populated with OOTB defaults (no network)."""
    registry = ChoiceRegistry(settings, auth_provider)
    registry._metadata_cache.seed(
        "all", {k: dict(v) for k, v in ChoiceRegistry._DEFAULTS.items()}
    )
    return registry


class TestChoiceRegistryFetch:
    """Test HTTP-fetching behavior using the httpx2 mock transport."""

    @pytest.mark.asyncio()
    async def test_fetch_merges_instance_data_over_defaults(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
    ) -> None:
        """Instance data should override defaults while preserving non-overridden entries."""
        # Mock sys_choice response: override "open" value for incident.state
        http_mock.get(f"{BASE_URL}/api/now/table/sys_choice").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "name": "incident",
                            "element": "state",
                            "value": "99",
                            "label": "Open",
                        },
                    ],
                },
                headers={"X-Total-Count": "1"},
            ),
        )

        registry = ChoiceRegistry(settings, auth_provider)
        result = await registry.resolve("incident", "state", "open")

        # Instance overrode "open" from "1" to "99"
        assert result == "99"
        # Default "closed" should still be available
        closed = await registry.resolve("incident", "state", "closed")
        assert closed == "7"

    @pytest.mark.asyncio()
    async def test_concurrent_fetch_shares_load(
        self, settings: Settings, auth_provider: OAuthPKCEProvider
    ) -> None:
        """Concurrent resolve() calls should share one HTTP fetch."""
        route = http_mock.get(f"{BASE_URL}/api/now/table/sys_choice").mock(
            return_value=Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            ),
        )

        registry = ChoiceRegistry(settings, auth_provider)
        # Fire 10 concurrent resolve calls
        tasks = [
            asyncio.create_task(registry.resolve("incident", "state", "open"))
            for _ in range(10)
        ]
        results = await asyncio.gather(*tasks)

        # All should return the default passthrough or default value
        assert all(r == "1" for r in results)
        # But the HTTP call should have happened only once
        assert route.call_count == 1


class TestChoiceHelpers:
    """Test the extracted pure helper functions."""

    def test_group_choice_records_skips_falsy_name(self) -> None:
        """Records with empty name should be skipped."""
        records = [
            {"name": "", "element": "state", "label": "Open", "value": "1"},
            {"name": "incident", "element": "state", "label": "Closed", "value": "7"},
        ]
        grouped = _group_choice_records(records)
        assert ("incident", "state") in grouped
        assert grouped[("incident", "state")] == {"closed": "7"}
        # The record with empty name should not appear anywhere
        assert len(grouped) == 1

    def test_merge_with_defaults_includes_instance_only_choices(self) -> None:
        """Instance-only choices not present in defaults should appear in merged output."""
        defaults: dict[tuple[str, str], dict[str, str]] = {
            ("incident", "state"): {"open": "1", "closed": "7"},
        }
        grouped: dict[tuple[str, str], dict[str, str]] = {
            ("incident", "state"): {"open": "99"},
            ("custom_table", "priority"): {"high": "1", "low": "3"},
        }
        merged = _merge_with_defaults(grouped, defaults)

        # Default key should be present with instance override
        assert merged[("incident", "state")]["open"] == "99"
        assert merged[("incident", "state")]["closed"] == "7"
        # Instance-only key should be included
        assert ("custom_table", "priority") in merged
        assert merged[("custom_table", "priority")] == {"high": "1", "low": "3"}


class TestChoiceRegistryExceptionPaths:
    """Test async exception handling and edge cases in ChoiceRegistry."""

    @pytest.mark.asyncio()
    async def test_concurrent_waiter_shares_in_flight_load(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
    ) -> None:
        """A second concurrent caller shares the first caller's in-flight load."""
        gate = asyncio.Event()

        original_fetch = ChoiceRegistry._fetch_from_instance

        async def slow_fetch(
            self_inner: ChoiceRegistry,
        ) -> dict[tuple[str, str], dict[str, str]]:
            """Delay fetch so the second caller shares the pending load."""
            await gate.wait()
            return await original_fetch(self_inner)

        http_mock.get(f"{BASE_URL}/api/now/table/sys_choice").mock(
            return_value=Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            ),
        )

        registry = ChoiceRegistry(settings, auth_provider)

        with patch.object(ChoiceRegistry, "_fetch_from_instance", slow_fetch):
            task1 = asyncio.create_task(registry.resolve("incident", "state", "open"))
            task2 = asyncio.create_task(registry.resolve("incident", "state", "closed"))

            # Let both tasks start and share the pending load
            await asyncio.sleep(0.05)

            # Release the gate so the first fetch completes
            gate.set()

            results = await asyncio.gather(task1, task2)

        # Both should resolve using defaults (empty instance data merged with defaults)
        assert results[0] == "1"  # "open" from defaults
        assert results[1] == "7"  # "closed" from defaults

    @pytest.mark.asyncio()
    async def test_ensure_fetched_falls_back_on_fetch_error(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
    ) -> None:
        """When _fetch_from_instance raises, the registry should fall back to OOTB defaults."""
        http_mock.get(f"{BASE_URL}/api/now/table/sys_choice").mock(
            return_value=Response(
                500, json={"error": {"message": "Internal Server Error"}}
            ),
        )

        registry = ChoiceRegistry(settings, auth_provider)
        result = await registry.resolve("incident", "state", "open")

        # Should fall back to default value
        assert result == "1"

    @pytest.mark.asyncio()
    async def test_fetch_from_instance_empty_defaults(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
    ) -> None:
        """An empty tracked set returns an empty mapping without an HTTP request."""
        registry = ChoiceRegistry(settings, auth_provider)

        with patch.object(ChoiceRegistry, "_DEFAULTS", {}):
            assert await registry._fetch_from_instance() == {}
