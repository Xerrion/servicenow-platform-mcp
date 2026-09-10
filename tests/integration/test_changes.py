"""Integration tests for change intelligence tools against a live ServiceNow instance."""

import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings


pytestmark = pytest.mark.integration


class TestChanges:
    """Test change intelligence operations on a live instance."""

    async def test_changes_updateset_inspect(
        self,
        live_settings: Settings,
        live_auth: OAuthPKCEProvider,
        update_set_sys_id: str | None,
    ) -> None:
        """Inspect an update set: fetch header and members."""
        if not update_set_sys_id:
            pytest.skip("No update set found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            update_set = await client.get_record("sys_update_set", update_set_sys_id)
            members_result = await client.query_records(
                "sys_update_xml",
                f"update_set={update_set_sys_id}",
                fields=["sys_id", "name", "type", "action", "target_name"],
                limit=50,
            )

        assert update_set["sys_id"] == update_set_sys_id
        assert isinstance(members_result["records"], list)

    async def test_changes_last_touched(
        self,
        live_settings: Settings,
        live_auth: OAuthPKCEProvider,
        incident_sys_id: str | None,
    ) -> None:
        """Query audit trail for an incident record."""
        if not incident_sys_id:
            pytest.skip("No incident found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            audit_result = await client.query_records(
                "sys_audit",
                f"tablename=incident^documentkey={incident_sys_id}",
                fields=[
                    "sys_id",
                    "user",
                    "fieldname",
                    "oldvalue",
                    "newvalue",
                    "sys_created_on",
                ],
                limit=10,
                order_by="sys_created_on",
            )

        # API call succeeds; may return 0 entries if no audit history
        assert isinstance(audit_result["records"], list)

    async def test_changes_release_notes(
        self,
        live_settings: Settings,
        live_auth: OAuthPKCEProvider,
        update_set_sys_id: str | None,
    ) -> None:
        """Fetch update set metadata for release note generation."""
        if not update_set_sys_id:
            pytest.skip("No update set found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            update_set = await client.get_record("sys_update_set", update_set_sys_id)

        assert "name" in update_set
        assert "state" in update_set
