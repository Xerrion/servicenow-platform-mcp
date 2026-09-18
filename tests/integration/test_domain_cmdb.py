"""Integration tests for CMDB domain tools against a live ServiceNow instance."""

import pytest
from mcp.server import MCPServer

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.tools.cmdb import register_tools
from tests.helpers import decode_response, get_tool_functions


pytestmark = pytest.mark.integration


class TestDomainCmdb:
    """Test CMDB domain API operations on a live instance."""

    async def test_dedicated_cmdb_tool_reads_instances_and_metadata(
        self, live_settings: Settings, live_auth: OAuthPKCEProvider
    ) -> None:
        """Exercise the real CMDB endpoints through registered tool dispatch."""
        server = MCPServer("live-cmdb-test")
        register_tools(server, live_settings, live_auth)
        cmdb = get_tool_functions(server)["cmdb"]
        page = decode_response(await cmdb(action="query", class_name="cmdb_ci_server", limit=1))
        assert page["status"] == "success"
        assert isinstance(page["data"]["records"], list)
        meta = decode_response(await cmdb(action="meta", class_name="cmdb_ci_server"))
        assert meta["status"] == "success"
        assert meta["data"]["name"] == "cmdb_ci_server"
        records = page["data"]["records"]
        if not records:
            pytest.skip("No visible CMDB server CI for the get action")
        ci = decode_response(await cmdb(action="get", class_name="cmdb_ci_server", sys_id=records[0]["sys_id"]))
        assert ci["status"] == "success"
        assert isinstance(ci["data"]["attributes"], dict)
        assert isinstance(ci["data"]["inbound_relations"], list)
        assert isinstance(ci["data"]["outbound_relations"], list)

    async def test_cmdb_list_returns_records(self, live_settings: Settings, live_auth: OAuthPKCEProvider) -> None:
        """Query CMDB CIs without filters."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.query_records(
                "cmdb_ci",
                "",
                display_values=True,
                limit=5,
            )
        assert isinstance(result["records"], list)

    async def test_cmdb_get_by_sys_id(
        self,
        live_settings: Settings,
        live_auth: OAuthPKCEProvider,
        cmdb_ci_sys_id: str | None,
    ) -> None:
        """Fetch a single CI by sys_id."""
        if not cmdb_ci_sys_id:
            pytest.skip("No CMDB CI found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.query_records(
                "cmdb_ci",
                f"sys_id={cmdb_ci_sys_id}",
                display_values=True,
                limit=1,
            )
        assert len(result["records"]) == 1
        assert result["records"][0]["sys_id"] == cmdb_ci_sys_id

    async def test_cmdb_get_by_name(
        self,
        live_settings: Settings,
        live_auth: OAuthPKCEProvider,
        cmdb_ci_sys_id: str | None,
    ) -> None:
        """Fetch a CI by name (simulates cmdb_get tool name lookup)."""
        if not cmdb_ci_sys_id:
            pytest.skip("No CMDB CI found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            record = await client.get_record("cmdb_ci", cmdb_ci_sys_id, fields=["name"])
            name = record.get("name", "")
            if not name:
                pytest.skip("CI has no name field")

            result = await client.query_records(
                "cmdb_ci",
                f"name={name}",
                display_values=True,
                limit=1,
            )
        assert len(result["records"]) >= 1

    async def test_cmdb_relationships_query(
        self,
        live_settings: Settings,
        live_auth: OAuthPKCEProvider,
        cmdb_ci_sys_id: str | None,
    ) -> None:
        """Query CMDB relationships for a CI (simulates cmdb_relationships tool)."""
        if not cmdb_ci_sys_id:
            pytest.skip("No CMDB CI found on instance")

        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.query_records(
                "cmdb_rel_ci",
                f"child.sys_id={cmdb_ci_sys_id}^ORparent.sys_id={cmdb_ci_sys_id}",
                display_values=True,
                limit=100,
            )
        assert isinstance(result["records"], list)

    async def test_cmdb_classes_aggregate(self, live_settings: Settings, live_auth: OAuthPKCEProvider) -> None:
        """Aggregate CMDB CI classes (simulates cmdb_classes tool)."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.aggregate(
                table="cmdb_ci",
                query="",
                group_by="sys_class_name",
            )
        assert result is not None

    async def test_cmdb_health_aggregate(self, live_settings: Settings, live_auth: OAuthPKCEProvider) -> None:
        """Aggregate CMDB operational status (simulates cmdb_health tool)."""
        async with ServiceNowClient(live_settings, live_auth) as client:
            result = await client.aggregate(
                table="cmdb_ci",
                query="",
                group_by="operational_status",
            )
        assert result is not None
