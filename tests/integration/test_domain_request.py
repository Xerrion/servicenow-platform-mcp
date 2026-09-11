"""Integration tests for Request Management domain tools against a live ServiceNow instance."""

import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings


pytestmark = pytest.mark.integration


class TestDomainRequest:
    """Test Request Management domain API operations on a live instance."""

    async def test_request_list_returns_records(self, live_settings: Settings, live_auth: OAuthPKCEProvider) -> None:
        """Query service catalog requests without filters."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.query_records(
                "sc_request",
                "",
                display_values=True,
                limit=5,
            )
        assert isinstance(result["records"], list)

    async def test_request_get_by_sys_id(
        self,
        live_settings: Settings,
        live_auth: OAuthPKCEProvider,
        sc_request_sys_id: str | None,
    ) -> None:
        """Fetch a single request by sys_id."""
        if not sc_request_sys_id:
            pytest.skip("No service catalog request found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            record = await client.get_record(
                "sc_request",
                sc_request_sys_id,
                fields=["sys_id", "number", "short_description", "state"],
            )
        assert record["sys_id"] == sc_request_sys_id

    async def test_request_get_by_number(
        self,
        live_settings: Settings,
        live_auth: OAuthPKCEProvider,
        sc_request_sys_id: str | None,
    ) -> None:
        """Fetch a request by REQ number (simulates request_get tool)."""
        if not sc_request_sys_id:
            pytest.skip("No service catalog request found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            record = await client.get_record("sc_request", sc_request_sys_id, fields=["number"])
            number = record.get("number", "")
            assert number.startswith("REQ"), f"Unexpected request number format: {number}"

            result = await client.query_records(
                "sc_request",
                f"number={number}",
                display_values=True,
                limit=1,
            )
        assert len(result["records"]) == 1

    async def test_request_items_query(
        self,
        live_settings: Settings,
        live_auth: OAuthPKCEProvider,
        sc_request_sys_id: str | None,
    ) -> None:
        """Query request items for a request (simulates request_items tool)."""
        if not sc_request_sys_id:
            pytest.skip("No service catalog request found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            record = await client.get_record("sc_request", sc_request_sys_id, fields=["number"])
            number = record.get("number", "")

            result = await client.query_records(
                "sc_req_item",
                f"request.number={number}",
                display_values=True,
                limit=20,
            )
        # May have 0 items - just verify API call succeeds
        assert isinstance(result["records"], list)

    async def test_request_item_table_accessible(self, live_settings: Settings, live_auth: OAuthPKCEProvider) -> None:
        """Verify sc_req_item table is queryable."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.query_records(
                "sc_req_item",
                "",
                display_values=True,
                limit=1,
            )
        assert isinstance(result["records"], list)
