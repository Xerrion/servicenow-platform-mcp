"""Integration tests for table tools against a live ServiceNow instance."""

import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings


pytestmark = pytest.mark.integration


class TestTableIntegration:
    """Test table operations on a live instance."""

    async def test_table_describe_returns_fields(self, live_settings: Settings, live_auth: OAuthPKCEProvider) -> None:
        """table_describe: fetch field metadata for the incident table."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            metadata = await client.get_metadata("incident")

        fields = [
            {
                "element": e.get("element", ""),
                "internal_type": e.get("internal_type", ""),
            }
            for e in metadata
        ]
        assert len(fields) > 0, "No fields returned for incident table"

    async def test_table_query_returns_records(self, live_settings: Settings, live_auth: OAuthPKCEProvider) -> None:
        """table_query: query active incidents."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.query_records(
                "incident",
                "active=true",
                fields=["sys_id", "number", "short_description", "priority"],
                limit=5,
            )

        assert len(result["records"]) > 0, "No active incidents found"
        assert "count" in result

    async def test_record_get_fetches_single_record(
        self,
        live_settings: Settings,
        live_auth: OAuthPKCEProvider,
        incident_sys_id: str | None,
    ) -> None:
        """record_get: fetch a single incident by sys_id."""
        if not incident_sys_id:
            pytest.skip("No incident found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            record = await client.get_record(
                "incident",
                incident_sys_id,
                fields=["sys_id", "number", "short_description", "state"],
            )

        assert record["sys_id"] == incident_sys_id

    async def test_table_aggregate_returns_stats(self, live_settings: Settings, live_auth: OAuthPKCEProvider) -> None:
        """table_aggregate: get count of incidents."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.aggregate("incident", "")

        assert result is not None, "No aggregate result returned"
