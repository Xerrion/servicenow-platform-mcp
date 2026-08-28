"""Tests for ChoiceRegistry lazy-loaded choice list cache."""

import asyncio
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
import respx
from httpx import Response

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry, _group_choice_records, _merge_with_defaults
from servicenow_mcp.client import ServiceNowClientProvider
from servicenow_mcp.config import Settings


BASE_URL = "https://test.service-now.com"


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """Test auth provider fixture."""
    return BasicAuthProvider(settings)


def _make_registry_with_defaults(settings: Settings, auth_provider: BasicAuthProvider) -> ChoiceRegistry:
    """Create a ChoiceRegistry pre-populated with OOTB defaults (no network)."""
    registry = ChoiceRegistry(settings, auth_provider)
    registry._cache = {k: dict(v) for k, v in ChoiceRegistry._DEFAULTS.items()}
    registry._metadata_cache.seed("all", registry._cache)
    return registry


class TestChoiceRegistryDefaults:
    """Test default (OOTB) choice resolution without network access."""

    @pytest.mark.asyncio()
    async def test_resolve_known_label_returns_value(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """resolve() should return the stored value for a known label."""
        registry = _make_registry_with_defaults(settings, auth_provider)

        result = await registry.resolve("incident", "state", "open")
        assert result == "1"

    @pytest.mark.asyncio()
    async def test_resolve_unknown_label_returns_passthrough(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """resolve() should passthrough an unrecognized label as-is."""
        registry = _make_registry_with_defaults(settings, auth_provider)

        result = await registry.resolve("incident", "state", "nonexistent")
        assert result == "nonexistent"

    @pytest.mark.asyncio()
    async def test_resolve_unknown_table_returns_passthrough(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """resolve() should passthrough when the table is not tracked."""
        registry = _make_registry_with_defaults(settings, auth_provider)

        result = await registry.resolve("fake_table", "state", "open")
        assert result == "open"

    @pytest.mark.asyncio()
    async def test_resolve_unknown_field_returns_passthrough(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """resolve() should passthrough when the field is not tracked."""
        registry = _make_registry_with_defaults(settings, auth_provider)

        result = await registry.resolve("incident", "fake_field", "open")
        assert result == "open"

    @pytest.mark.asyncio()
    async def test_get_choices_returns_default_map(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """get_choices() should return the full label-to-value dict for a known table/field."""
        registry = _make_registry_with_defaults(settings, auth_provider)

        choices = await registry.get_choices("incident", "state")
        assert isinstance(choices, dict)
        assert choices["open"] == "1"
        assert choices["in_progress"] == "2"
        assert choices["resolved"] == "6"
        assert choices["closed"] == "7"
        assert choices["canceled"] == "8"

    @pytest.mark.asyncio()
    async def test_get_choices_unknown_returns_empty(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """get_choices() should return an empty dict for an unknown table/field pair."""
        registry = _make_registry_with_defaults(settings, auth_provider)

        choices = await registry.get_choices("fake_table", "fake_field")
        assert choices == {}


class TestChoiceRegistryFetch:
    """Test HTTP-fetching behavior using respx mocks."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_fetch_merges_instance_data_over_defaults(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Instance data should override defaults while preserving non-overridden entries."""
        # Mock sys_choice response: override "open" value for incident.state
        respx.get(f"{BASE_URL}/api/now/table/sys_choice").mock(
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
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        registry = ChoiceRegistry(settings, auth_provider)
        result = await registry.resolve("incident", "state", "open")

        # Instance overrode "open" from "1" to "99"
        assert result == "99"
        # Default "closed" should still be available
        closed = await registry.resolve("incident", "state", "closed")
        assert closed == "7"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_fetch_only_happens_once(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Repeated resolve() calls should only trigger one HTTP fetch."""
        route = respx.get(f"{BASE_URL}/api/now/table/sys_choice").mock(
            return_value=Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        registry = ChoiceRegistry(settings, auth_provider)
        await registry.resolve("incident", "state", "open")
        await registry.resolve("incident", "state", "closed")
        await registry.resolve("problem", "state", "new")

        assert route.call_count == 1

    @pytest.mark.asyncio()
    async def test_fresh_hit_precedes_client_creation(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """A fresh choices hit does not call the client factory."""
        client_factory = AsyncMock(side_effect=AssertionError("client factory called"))
        registry = ChoiceRegistry(settings, auth_provider, cast("ServiceNowClientProvider", client_factory))
        registry._cache = {("incident", "state"): {"open": "1"}}
        registry._metadata_cache.seed("all", registry._cache)

        assert await registry.resolve("incident", "state", "open") == "1"
        client_factory.assert_not_called()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_fetch_adds_custom_instance_values(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Instance-only choices not in defaults should be available after fetch."""
        respx.get(f"{BASE_URL}/api/now/table/sys_choice").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "name": "incident",
                            "element": "state",
                            "value": "77",
                            "label": "Awaiting Approval",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        registry = ChoiceRegistry(settings, auth_provider)
        result = await registry.resolve("incident", "state", "awaiting_approval")

        assert result == "77"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_concurrent_fetch_uses_lock(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Concurrent resolve() calls should only trigger one HTTP fetch via asyncio.Lock."""
        route = respx.get(f"{BASE_URL}/api/now/table/sys_choice").mock(
            return_value=Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        registry = ChoiceRegistry(settings, auth_provider)
        # Fire 10 concurrent resolve calls
        tasks = [asyncio.create_task(registry.resolve("incident", "state", "open")) for _ in range(10)]
        results = await asyncio.gather(*tasks)

        # All should return the default passthrough or default value
        assert all(r == "1" for r in results)
        # But the HTTP call should have happened only once
        assert route.call_count == 1


class TestChoiceRegistryLabelNormalization:
    """Test label normalization behavior (spaces, case)."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_label_with_spaces_normalized(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Labels with spaces should be stored as underscore-separated lowercase keys."""
        respx.get(f"{BASE_URL}/api/now/table/sys_choice").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "name": "incident",
                            "element": "state",
                            "value": "2",
                            "label": "In Progress",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        registry = ChoiceRegistry(settings, auth_provider)
        result = await registry.resolve("incident", "state", "in_progress")
        assert result == "2"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_label_case_insensitive(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Labels like 'NEW' should be stored as the lowercase key 'new'."""
        respx.get(f"{BASE_URL}/api/now/table/sys_choice").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "name": "problem",
                            "element": "state",
                            "value": "1",
                            "label": "NEW",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        registry = ChoiceRegistry(settings, auth_provider)
        result = await registry.resolve("problem", "state", "new")
        assert result == "1"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_label_with_mixed_case_and_spaces(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """'Root Cause Analysis' should normalize to 'root_cause_analysis'."""
        respx.get(f"{BASE_URL}/api/now/table/sys_choice").mock(
            return_value=Response(
                200,
                json={
                    "result": [
                        {
                            "name": "problem",
                            "element": "state",
                            "value": "4",
                            "label": "Root Cause Analysis",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        registry = ChoiceRegistry(settings, auth_provider)
        result = await registry.resolve("problem", "state", "root_cause_analysis")
        assert result == "4"


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

    def test_group_choice_records_skips_falsy_element(self) -> None:
        """Records with empty element should be skipped."""
        records = [
            {"name": "incident", "element": "", "label": "Open", "value": "1"},
            {"name": "incident", "element": "state", "label": "Closed", "value": "7"},
        ]
        grouped = _group_choice_records(records)
        assert grouped == {("incident", "state"): {"closed": "7"}}

    def test_group_choice_records_skips_falsy_label(self) -> None:
        """Records with empty label should be skipped."""
        records = [
            {"name": "incident", "element": "state", "label": "", "value": "1"},
            {"name": "incident", "element": "state", "label": "Closed", "value": "7"},
        ]
        grouped = _group_choice_records(records)
        assert grouped == {("incident", "state"): {"closed": "7"}}

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
    @respx.mock
    async def test_concurrent_waiter_shares_in_flight_load(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """A second concurrent caller shares the first caller's in-flight load."""
        gate = asyncio.Event()

        original_fetch = ChoiceRegistry._fetch_from_instance

        async def slow_fetch(self_inner: ChoiceRegistry) -> None:
            """Delay fetch so the second caller queues on the lock."""
            await gate.wait()
            await original_fetch(self_inner)

        respx.get(f"{BASE_URL}/api/now/table/sys_choice").mock(
            return_value=Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        registry = ChoiceRegistry(settings, auth_provider)

        with patch.object(ChoiceRegistry, "_fetch_from_instance", slow_fetch):
            task1 = asyncio.create_task(registry.resolve("incident", "state", "open"))
            task2 = asyncio.create_task(registry.resolve("incident", "state", "closed"))

            # Let both tasks start and queue on the lock
            await asyncio.sleep(0.05)

            # Release the gate so the first fetch completes
            gate.set()

            results = await asyncio.gather(task1, task2)

        # Both should resolve using defaults (empty instance data merged with defaults)
        assert results[0] == "1"  # "open" from defaults
        assert results[1] == "7"  # "closed" from defaults

        # Fetch should only have been called once - the second task hit the double-check

    @pytest.mark.asyncio()
    @respx.mock
    async def test_ensure_fetched_falls_back_on_fetch_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """When _fetch_from_instance raises, the registry should fall back to OOTB defaults."""
        respx.get(f"{BASE_URL}/api/now/table/sys_choice").mock(
            return_value=Response(500, json={"error": {"message": "Internal Server Error"}})
        )

        registry = ChoiceRegistry(settings, auth_provider)
        result = await registry.resolve("incident", "state", "open")

        # Should fall back to default value
        assert result == "1"

    @pytest.mark.asyncio()
    async def test_fetch_from_instance_empty_defaults(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """When _DEFAULTS is empty, _fetch_from_instance should set cache to {} and return early."""
        registry = ChoiceRegistry(settings, auth_provider)

        with patch.object(ChoiceRegistry, "_DEFAULTS", {}):
            await registry._fetch_from_instance()

        assert registry._cache == {}
