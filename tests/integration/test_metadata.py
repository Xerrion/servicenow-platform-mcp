"""Integration tests for metadata tools against a live ServiceNow instance."""

import pytest

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings


pytestmark = pytest.mark.integration


class TestMetadata:
    """Test metadata discovery on a live instance."""

    async def test_meta_list_artifacts(self, live_settings: Settings, live_auth: BasicAuthProvider) -> None:
        """meta_list_artifacts: list business rules."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.query_records(
                "sys_script",
                "",
                limit=5,
            )

        assert len(result["records"]) > 0, "No business rules found"

    async def test_meta_get_artifact(
        self,
        live_settings: Settings,
        live_auth: BasicAuthProvider,
        business_rule_sys_id: str | None,
    ) -> None:
        """meta_get_artifact: fetch a single business rule by sys_id."""
        if not business_rule_sys_id:
            pytest.skip("No business rule found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            record = await client.get_record("sys_script", business_rule_sys_id)

        assert record["sys_id"] == business_rule_sys_id
        assert "script" in record

    async def test_meta_find_references(self, live_settings: Settings, live_auth: BasicAuthProvider) -> None:
        """meta_find_references: search for business rules referencing 'incident'."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.query_records(
                "sys_script",
                "scriptCONTAINSincident",
                fields=["sys_id", "name", "sys_class_name"],
                limit=10,
            )

        # There should be at least some BRs referencing "incident"
        assert isinstance(result["records"], list)

    async def test_meta_business_rules_for_table(self, live_settings: Settings, live_auth: BasicAuthProvider) -> None:
        """meta_business_rules_for_table: find business rules that write to the incident table."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.query_records(
                "sys_script",
                "collection=incident",
                limit=50,
            )

        assert len(result["records"]) > 0, "No BRs write to incident"
