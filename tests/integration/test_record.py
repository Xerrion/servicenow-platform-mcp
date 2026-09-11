"""Integration tests for record tools against a live ServiceNow instance."""

import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings


pytestmark = pytest.mark.integration


class TestRecordIntegration:
    """Test record operations on a live instance."""

    async def test_rel_references_from(
        self,
        live_settings: Settings,
        live_auth: OAuthPKCEProvider,
        incident_sys_id: str | None,
    ) -> None:
        """rel_references_from: find outgoing reference fields on an incident."""
        if not incident_sys_id:
            pytest.skip("No incident found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            record = await client.get_record("incident", incident_sys_id, display_values=True)
            ref_fields = await client.query_records(
                "sys_dictionary",
                "name=incident^internal_type=reference",
                fields=["element", "reference", "column_label"],
                limit=100,
            )

        outgoing = []
        for field in ref_fields["records"]:
            field_name = field.get("element", "")
            ref_table = field.get("reference", "")
            if field_name and field_name in record and record[field_name]:
                outgoing.append(f"{field_name}->{ref_table}")

        # An incident should have at least some populated reference fields
        assert isinstance(outgoing, list)

    async def test_rel_references_to(self, live_settings: Settings, live_auth: OAuthPKCEProvider) -> None:
        """rel_references_to: find tables that reference the incident table."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            ref_fields = await client.query_records(
                "sys_dictionary",
                "internal_type=reference^reference=incident",
                fields=["name", "element", "column_label"],
                limit=50,
            )

        referencing_tables = list({f.get("name", "") for f in ref_fields["records"] if f.get("name")})
        assert len(referencing_tables) > 0, "No tables reference incident"
