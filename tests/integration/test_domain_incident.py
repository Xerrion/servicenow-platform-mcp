"""Integration tests for Incident domain tools against a live ServiceNow instance."""

import pytest

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings

pytestmark = pytest.mark.integration


class TestDomainIncident:
    """Test Incident domain API operations on a live instance."""

    async def test_incident_list_returns_records(self, live_settings: Settings, live_auth: BasicAuthProvider) -> None:
        """Query incidents without filters returns results."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.query_records(
                "incident",
                "",
                display_values=True,
                limit=5,
            )
        assert isinstance(result["records"], list)
        assert len(result["records"]) > 0, "No incidents found on instance"

    async def test_incident_list_with_state_filter(self, live_settings: Settings, live_auth: BasicAuthProvider) -> None:
        """Query incidents filtered by active state."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.query_records(
                "incident",
                "active=true",
                display_values=True,
                limit=5,
            )
        assert isinstance(result["records"], list)

    async def test_incident_get_by_sys_id(
        self,
        live_settings: Settings,
        live_auth: BasicAuthProvider,
        incident_sys_id: str | None,
    ) -> None:
        """Fetch a single incident by sys_id."""
        if not incident_sys_id:
            pytest.skip("No incident found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            record = await client.get_record(
                "incident",
                incident_sys_id,
                fields=[
                    "sys_id",
                    "number",
                    "short_description",
                    "state",
                    "priority",
                ],
            )
        assert record["sys_id"] == incident_sys_id
        assert "number" in record

    async def test_incident_get_by_number(
        self,
        live_settings: Settings,
        live_auth: BasicAuthProvider,
        incident_sys_id: str | None,
    ) -> None:
        """Fetch an incident by its INC number (simulates incident_get tool)."""
        if not incident_sys_id:
            pytest.skip("No incident found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            record = await client.get_record("incident", incident_sys_id, fields=["number"])
            number = record.get("number", "")
            assert number.startswith("INC"), f"Unexpected incident number format: {number}"

            result = await client.query_records(
                "incident",
                f"number={number}",
                display_values=True,
                limit=1,
            )
        assert len(result["records"]) == 1
        assert result["records"][0]["number"] == number

    async def test_incident_list_with_priority_filter(
        self, live_settings: Settings, live_auth: BasicAuthProvider
    ) -> None:
        """Query incidents filtered by priority."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.query_records(
                "incident",
                "priority=3",
                display_values=True,
                limit=5,
            )
        assert isinstance(result["records"], list)
