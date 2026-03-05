"""Integration tests for Change Management domain tools against a live ServiceNow instance."""

import pytest

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings

pytestmark = pytest.mark.integration


class TestDomainChange:
    """Test Change Management domain API operations on a live instance."""

    async def test_change_list_returns_records(self, live_settings: Settings, live_auth: BasicAuthProvider) -> None:
        """Query change requests without filters."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.query_records(
                "change_request",
                "",
                display_values=True,
                limit=5,
            )
        assert isinstance(result["records"], list)

    async def test_change_get_by_sys_id(
        self,
        live_settings: Settings,
        live_auth: BasicAuthProvider,
        change_request_sys_id: str | None,
    ) -> None:
        """Fetch a single change request by sys_id."""
        if not change_request_sys_id:
            pytest.skip("No change request found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            record = await client.get_record(
                "change_request",
                change_request_sys_id,
                fields=[
                    "sys_id",
                    "number",
                    "short_description",
                    "state",
                    "type",
                ],
            )
        assert record["sys_id"] == change_request_sys_id

    async def test_change_get_by_number(
        self,
        live_settings: Settings,
        live_auth: BasicAuthProvider,
        change_request_sys_id: str | None,
    ) -> None:
        """Fetch a change request by CHG number (simulates change_get tool)."""
        if not change_request_sys_id:
            pytest.skip("No change request found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            record = await client.get_record("change_request", change_request_sys_id, fields=["number"])
            number = record.get("number", "")
            assert number.startswith("CHG"), f"Unexpected change number format: {number}"

            result = await client.query_records(
                "change_request",
                f"number={number}",
                display_values=True,
                limit=1,
            )
        assert len(result["records"]) == 1

    async def test_change_tasks_query(
        self,
        live_settings: Settings,
        live_auth: BasicAuthProvider,
        change_request_sys_id: str | None,
    ) -> None:
        """Query change tasks for a change request (simulates change_tasks tool)."""
        if not change_request_sys_id:
            pytest.skip("No change request found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            record = await client.get_record("change_request", change_request_sys_id, fields=["number"])
            number = record.get("number", "")

            result = await client.query_records(
                "change_task",
                f"change_request.number={number}",
                display_values=True,
                limit=20,
            )
        assert isinstance(result["records"], list)

    async def test_change_list_with_type_filter(self, live_settings: Settings, live_auth: BasicAuthProvider) -> None:
        """Query change requests filtered by type."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.query_records(
                "change_request",
                "type=normal",
                display_values=True,
                limit=5,
            )
        assert isinstance(result["records"], list)
