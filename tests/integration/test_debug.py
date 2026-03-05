"""Integration tests for debug/trace tools against a live ServiceNow instance."""

import pytest

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings

pytestmark = pytest.mark.integration


class TestDebug:
    """Test debug/trace operations on a live instance."""

    async def test_debug_trace(
        self,
        live_settings: Settings,
        live_auth: BasicAuthProvider,
        incident_sys_id: str | None,
    ) -> None:
        """Build a merged timeline from sys_audit and sys_journal_field."""
        if not incident_sys_id:
            pytest.skip("No incident found on instance")

        timeline_count = 0
        async with ServiceNowClient(live_settings, live_auth) as client:
            audit_r = await client.query_records(
                "sys_audit",
                f"tablename=incident^documentkey={incident_sys_id}",
                fields=["sys_id", "user", "fieldname", "sys_created_on"],
                limit=20,
            )
            timeline_count += len(audit_r["records"])

            journal_r = await client.query_records(
                "sys_journal_field",
                f"element_id={incident_sys_id}",
                fields=["sys_id", "element", "sys_created_on"],
                limit=20,
            )
            timeline_count += len(journal_r["records"])

        # API calls succeed; may return 0 events on a fresh instance
        assert isinstance(audit_r["records"], list)
        assert isinstance(journal_r["records"], list)

    async def test_debug_integration_health(self, live_settings: Settings, live_auth: BasicAuthProvider) -> None:
        """Query ecc_queue for error entries."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            ecc_result = await client.query_records(
                "ecc_queue",
                "state=error",
                fields=[
                    "sys_id",
                    "name",
                    "queue",
                    "error_string",
                    "sys_created_on",
                ],
                limit=20,
            )

        assert isinstance(ecc_result["records"], list)

    async def test_debug_field_mutation_story(
        self,
        live_settings: Settings,
        live_auth: BasicAuthProvider,
        incident_sys_id: str | None,
    ) -> None:
        """Trace the mutation history of the 'state' field on an incident."""
        if not incident_sys_id:
            pytest.skip("No incident found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            mutation_result = await client.query_records(
                "sys_audit",
                f"tablename=incident^documentkey={incident_sys_id}^fieldname=state",
                fields=[
                    "sys_id",
                    "user",
                    "oldvalue",
                    "newvalue",
                    "sys_created_on",
                ],
                limit=20,
                order_by="sys_created_on",
            )

        assert isinstance(mutation_result["records"], list)
