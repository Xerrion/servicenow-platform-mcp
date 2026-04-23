"""Tests for Flow Designer introspection and migration analysis tools."""

from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.tools.flow_designer import _process_neighbor
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict[str, Any]:
    """Register flow designer tools and return a name-to-function mapping."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.flow_designer import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return get_tool_functions(mcp)


# ---------------------------------------------------------------------------
# Tool Registration
# ---------------------------------------------------------------------------


class TestFlowDesignerToolRegistration:
    """Verify all flow designer tools register correctly."""

    def test_all_tools_registered(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """All eight flow designer tools are registered on the MCP server."""
        tools = _register_and_get_tools(settings, auth_provider)
        expected = {
            "flow_list",
            "flow_get",
            "flow_map",
            "flow_action_detail",
            "flow_execution_list",
            "flow_execution_detail",
            "flow_snapshot_list",
            "workflow_migration_analysis",
        }
        assert expected == set(tools.keys())

    def test_tool_count(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Exactly eight tools are registered."""
        tools = _register_and_get_tools(settings, auth_provider)
        assert len(tools) == 8


# ---------------------------------------------------------------------------
# flow_list
# ---------------------------------------------------------------------------


class TestFlowList:
    """Tests for the flow_list tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_list_no_filters(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns flows with no extra filters (only active=true by default)."""
        respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "8eecae1b3a5230387ff5f06a91b9fbe9",
                            "name": "Auto Assignment Flow",
                            "status": "published",
                            "type": "flow",
                            "table": "incident",
                            "active": "true",
                            "description": "Assigns incidents",
                            "sys_updated_on": "2026-02-20 10:00:00",
                            "sys_created_on": "2026-01-15 08:00:00",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_list"]()
        result = decode_response(raw)

        assert result["status"] == "success"
        assert len(result["data"]["flows"]) == 1
        assert result["data"]["flows"][0]["name"] == "Auto Assignment Flow"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_list_with_type_filter(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Passes flow_type filter into the encoded query."""
        flow_route = respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_list"](flow_type="subflow")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert flow_route.calls.last is not None
        last_request = flow_route.calls.last.request
        qs = parse_qs(urlparse(str(last_request.url)).query)
        assert "type=subflow" in qs["sysparm_query"][0]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_list_with_status_filter(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Passes status filter into the encoded query."""
        flow_route = respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_list"](status="draft")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert flow_route.calls.last is not None
        last_request = flow_route.calls.last.request
        qs = parse_qs(urlparse(str(last_request.url)).query)
        assert "status=draft" in qs["sysparm_query"][0]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_list_with_table_filter(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Passes table filter into the encoded query."""
        flow_route = respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_list"](table="incident")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert flow_route.calls.last is not None
        last_request = flow_route.calls.last.request
        qs = parse_qs(urlparse(str(last_request.url)).query)
        assert "table=incident" in qs["sysparm_query"][0]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_list_active_only_default(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Active=true filter is present by default."""
        flow_route = respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_list"]()
        result = decode_response(raw)

        assert result["status"] == "success"
        assert flow_route.calls.last is not None
        last_request = flow_route.calls.last.request
        qs = parse_qs(urlparse(str(last_request.url)).query)
        assert "active=true" in qs["sysparm_query"][0]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_list_active_only_false(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """No active filter when active_only=False."""
        flow_route = respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_list"](active_only=False)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert flow_route.calls.last is not None
        last_request = flow_route.calls.last.request
        qs = parse_qs(urlparse(str(last_request.url)).query)
        query_str = qs.get("sysparm_query", [""])[0]
        assert "active=true" not in query_str

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_list_empty_results(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Empty result set returns success with empty list."""
        respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_list"]()
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["flows"] == []

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_list_with_limit(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Limit is respected in query params."""
        flow_route = respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_list"](limit=5)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert flow_route.calls.last is not None
        last_request = flow_route.calls.last.request
        qs = parse_qs(urlparse(str(last_request.url)).query)
        assert qs["sysparm_limit"][0] == "5"

    @pytest.mark.asyncio()
    async def test_flow_list_invalid_table_name(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Invalid table name is rejected by validate_identifier."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_list"](table="INVALID-TABLE!")
        result = decode_response(raw)
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# flow_get
# ---------------------------------------------------------------------------


class TestFlowGet:
    """Tests for the flow_get tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_get_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns flow record with its variables."""
        respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow/8eecae1b3a5230387ff5f06a91b9fbe9").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "8eecae1b3a5230387ff5f06a91b9fbe9",
                        "name": "Auto Assignment Flow",
                        "status": "published",
                        "type": "flow",
                        "table": "incident",
                        "active": "true",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow_variable").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "47a920e21ce3b82c733a27e71b6e24b7",
                            "name": "assignment_group",
                            "type": "reference",
                            "mandatory": "true",
                            "default_value": "",
                        },
                        {
                            "sys_id": "f26728a87efd1d5a8256284c435cc0c4",
                            "name": "priority",
                            "type": "integer",
                            "mandatory": "false",
                            "default_value": "3",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_get"](flow_sys_id="8eecae1b3a5230387ff5f06a91b9fbe9")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["flow"]["name"] == "Auto Assignment Flow"
        assert len(result["data"]["variables"]) == 2
        assert result["data"]["variables"][0]["name"] == "assignment_group"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_get_with_no_variables(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Flow exists but has no variables."""
        respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow/68fccdc772b24d949d4ec0381cb858eb").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "68fccdc772b24d949d4ec0381cb858eb",
                        "name": "Simple Flow",
                        "status": "published",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow_variable").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_get"](flow_sys_id="68fccdc772b24d949d4ec0381cb858eb")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["flow"]["sys_id"] == "68fccdc772b24d949d4ec0381cb858eb"
        assert result["data"]["variables"] == []

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_get_not_found(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """404 response returns error."""
        respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow/a83d4bf9070ae6eb080b4cc7b2703e17").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Record not found"}})
        )
        # Variable query may or may not fire depending on gather; mock it to avoid ConnectionError
        respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow_variable").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_get"](flow_sys_id="a83d4bf9070ae6eb080b4cc7b2703e17")
        result = decode_response(raw)

        assert result["status"] == "error"
        assert isinstance(result["error"], dict)
        assert "not found" in result["error"]["message"].lower()


# ---------------------------------------------------------------------------
# flow_map
# ---------------------------------------------------------------------------


class TestFlowMap:
    """Tests for the flow_map tool."""

    def _mock_parent_flow_lookup(
        self,
        flow_sys_id: str,
        *,
        latest_snapshot: str = "",
        master_snapshot: str = "",
    ) -> None:
        """Mock the raw parent flow lookup used to resolve child linkage."""
        respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow/{flow_sys_id}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": flow_sys_id,
                        "latest_snapshot": latest_snapshot,
                        "master_snapshot": master_snapshot,
                    }
                },
            )
        )

    @staticmethod
    def _get_query_target(route: respx.Route) -> str:
        """Extract the flow target from the most recent query."""
        assert route.calls.last is not None
        last_request = route.calls.last.request
        query_str = parse_qs(urlparse(str(last_request.url)).query)["sysparm_query"][0]
        return query_str.split("^")[0].removeprefix("flow=")

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_map_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Uses latest_snapshot when child records are linked to the newest snapshot."""
        self._mock_parent_flow_lookup(
            "8eecae1b3a5230387ff5f06a91b9fbe9", latest_snapshot="b6069c7ab2fac9d79864075defa6176c"
        )
        action_route = respx.get(f"{BASE_URL}/api/now/table/sys_hub_action_instance").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "636f282a5dd34466ab9e91c1706b1998",
                            "name": "Create Task",
                            "action_type": "de31c068fa3d1adb0687dc4d7bbf0648",
                            "order": "1",
                            "position": "0",
                            "sys_created_on": "2026-01-15 08:00:00",
                        },
                        {
                            "sys_id": "9903d5f9dbdac520c4e23b8550661763",
                            "name": "Send Email",
                            "action_type": "c0d996b6aee4ec53e6b3e2f86a64dd3e",
                            "order": "2",
                            "position": "1",
                            "sys_created_on": "2026-01-15 08:01:00",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )
        logic_route = respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow_logic").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "390c28e84854396993c1dcc315dfe48c",
                            "name": "If Priority 1",
                            "type": "if",
                            "order": "1",
                            "position": "0",
                            "sys_created_on": "2026-01-15 08:00:00",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_map"](flow_sys_id="8eecae1b3a5230387ff5f06a91b9fbe9")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert len(result["data"]["actions"]) == 2
        assert len(result["data"]["logic_blocks"]) == 1
        assert result["data"]["actions"][0]["name"] == "Create Task"
        assert result["data"]["logic_blocks"][0]["name"] == "If Priority 1"
        assert self._get_query_target(action_route) == "b6069c7ab2fac9d79864075defa6176c"
        assert self._get_query_target(logic_route) == "b6069c7ab2fac9d79864075defa6176c"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_map_only_actions(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Falls back to master_snapshot when latest_snapshot is missing."""
        self._mock_parent_flow_lookup(
            "06bed3558e7fd1e9359964cedb4dc271", master_snapshot="9b250a88678953224d007032837d3d92"
        )
        action_route = respx.get(f"{BASE_URL}/api/now/table/sys_hub_action_instance").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "3ff4a88b3dd61ff2b18f015f0ad2e484",
                            "name": "Update Record",
                            "action_type": "7f15696e98f35a0906ca2804a9a177aa",
                            "order": "1",
                            "position": "0",
                            "sys_created_on": "2026-01-20 09:00:00",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        logic_route = respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow_logic").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_map"](flow_sys_id="06bed3558e7fd1e9359964cedb4dc271")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert len(result["data"]["actions"]) == 1
        assert result["data"]["logic_blocks"] == []
        assert self._get_query_target(action_route) == "9b250a88678953224d007032837d3d92"
        assert self._get_query_target(logic_route) == "9b250a88678953224d007032837d3d92"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_map_only_logic(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Flow with logic blocks but no actions."""
        self._mock_parent_flow_lookup(
            "f05a9e989019b2cd934dc2ba0c31a1d2", latest_snapshot="9095d45621112c8881812276e734f504"
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_hub_action_instance").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow_logic").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "8a912c42d911682d7f9b9625d3cb2246",
                            "name": "For Each Record",
                            "type": "for_each",
                            "order": "1",
                            "position": "0",
                            "sys_created_on": "2026-02-01 10:00:00",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_map"](flow_sys_id="f05a9e989019b2cd934dc2ba0c31a1d2")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["actions"] == []
        assert len(result["data"]["logic_blocks"]) == 1

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_map_empty(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Falls back to the original flow sys_id when no snapshot references exist."""
        self._mock_parent_flow_lookup("188a7d874a4b60bd7ef309c6f8872ba7")
        action_route = respx.get(f"{BASE_URL}/api/now/table/sys_hub_action_instance").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        logic_route = respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow_logic").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_map"](flow_sys_id="188a7d874a4b60bd7ef309c6f8872ba7")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["actions"] == []
        assert result["data"]["logic_blocks"] == []
        assert self._get_query_target(action_route) == "188a7d874a4b60bd7ef309c6f8872ba7"
        assert self._get_query_target(logic_route) == "188a7d874a4b60bd7ef309c6f8872ba7"


# ---------------------------------------------------------------------------
# flow_action_detail
# ---------------------------------------------------------------------------


class TestFlowActionDetail:
    """Tests for the flow_action_detail tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_action_detail_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns instance, type definition, and steps when all exist."""

        def _instance_side_effect(request: httpx.Request) -> httpx.Response:
            if "sysparm_display_value=true" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "result": {
                            "sys_id": "636f282a5dd34466ab9e91c1706b1998",
                            "name": "Create Task",
                            "action_type": "Create Record",
                        }
                    },
                )
            return httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "636f282a5dd34466ab9e91c1706b1998",
                        "name": "Create Task",
                        "action_type": "8f60c39c7a0b23a9865bf30e8363153f",
                    }
                },
            )

        respx.get(f"{BASE_URL}/api/now/table/sys_hub_action_instance/636f282a5dd34466ab9e91c1706b1998").mock(
            side_effect=_instance_side_effect
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_hub_action_type_definition/8f60c39c7a0b23a9865bf30e8363153f").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "8f60c39c7a0b23a9865bf30e8363153f",
                        "name": "Create Record",
                        "description": "Creates a new record",
                        "access": "public",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_hub_step_instance").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "d07a54fe040684bbd267d6e61a2e11e0",
                            "name": "Set Fields",
                            "step_type": "set_values",
                            "order": "1",
                            "sys_created_on": "2026-01-15 08:00:00",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_action_detail"](action_instance_sys_id="636f282a5dd34466ab9e91c1706b1998")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["instance"]["sys_id"] == "636f282a5dd34466ab9e91c1706b1998"
        assert result["data"]["type_definition"]["name"] == "Create Record"
        assert len(result["data"]["steps"]) == 1
        assert result["data"]["steps"][0]["name"] == "Set Fields"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_action_detail_no_action_type(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Instance without action_type returns instance only, no type definition or steps."""

        def _nodef_side_effect(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "bff4b2197387890d8565ff7bba7374fe",
                        "name": "Custom Instance",
                        "action_type": "",
                    }
                },
            )

        respx.get(f"{BASE_URL}/api/now/table/sys_hub_action_instance/bff4b2197387890d8565ff7bba7374fe").mock(
            side_effect=_nodef_side_effect
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_action_detail"](action_instance_sys_id="bff4b2197387890d8565ff7bba7374fe")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["instance"]["sys_id"] == "bff4b2197387890d8565ff7bba7374fe"
        assert result["data"]["type_definition"] is None
        assert result["data"]["steps"] == []

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_action_detail_no_steps(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Type definition exists but has no steps."""

        def _instance_side_effect(request: httpx.Request) -> httpx.Response:
            if "sysparm_display_value=true" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "result": {
                            "sys_id": "048920ff3a5c5fbe81afd4f5ffd8b0f4",
                            "name": "Lookup Record",
                            "action_type": "Lookup Record",
                        }
                    },
                )
            return httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "048920ff3a5c5fbe81afd4f5ffd8b0f4",
                        "name": "Lookup Record",
                        "action_type": "21487c9e1a7c18c2b8f94fe2da220cae",
                    }
                },
            )

        respx.get(f"{BASE_URL}/api/now/table/sys_hub_action_instance/048920ff3a5c5fbe81afd4f5ffd8b0f4").mock(
            side_effect=_instance_side_effect
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_hub_action_type_definition/21487c9e1a7c18c2b8f94fe2da220cae").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "21487c9e1a7c18c2b8f94fe2da220cae",
                        "name": "Lookup Record",
                        "description": "Looks up a record",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_hub_step_instance").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_action_detail"](action_instance_sys_id="048920ff3a5c5fbe81afd4f5ffd8b0f4")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["type_definition"]["name"] == "Lookup Record"
        assert result["data"]["steps"] == []


# ---------------------------------------------------------------------------
# flow_execution_list
# ---------------------------------------------------------------------------


class TestFlowExecutionList:
    """Tests for the flow_execution_list tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_execution_list_by_flow(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Filter by flow_sys_id returns matching executions."""
        respx.get(f"{BASE_URL}/api/now/table/sys_flow_context").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "18dbe9bd70e88bd7d141d13c8a46e7d7",
                            "name": "Exec 1",
                            "flow": "8eecae1b3a5230387ff5f06a91b9fbe9",
                            "source_record": "6d55028a7049dbf2f4275991d6fc81cf",
                            "source_table": "incident",
                            "state": "COMPLETE",
                            "started": "2026-02-20 09:00:00",
                            "ended": "2026-02-20 09:00:05",
                            "sys_created_on": "2026-02-20 09:00:00",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_execution_list"](flow_sys_id="8eecae1b3a5230387ff5f06a91b9fbe9")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert len(result["data"]["executions"]) == 1
        assert result["data"]["executions"][0]["state"] == "COMPLETE"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_execution_list_by_record(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Filter by source_record returns matching executions."""
        exec_route = respx.get(f"{BASE_URL}/api/now/table/sys_flow_context").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "ffff7ed3a8c7ffe871d910c0eb40322e",
                            "name": "Exec 2",
                            "flow": "fdf7325b5fa9fb3a675311af2efbe71c",
                            "source_record": "2edef9aa2e99060fd11a80ae6eed85b5",
                            "source_table": "incident",
                            "state": "IN_PROGRESS",
                            "started": "2026-02-20 10:00:00",
                            "ended": "",
                            "sys_created_on": "2026-02-20 10:00:00",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_execution_list"](source_record="2edef9aa2e99060fd11a80ae6eed85b5")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert len(result["data"]["executions"]) == 1
        assert exec_route.calls.last is not None
        last_request = exec_route.calls.last.request
        qs = parse_qs(urlparse(str(last_request.url)).query)
        assert "source_record=2edef9aa2e99060fd11a80ae6eed85b5" in qs["sysparm_query"][0]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_execution_list_by_state(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Filter by state."""
        exec_route = respx.get(f"{BASE_URL}/api/now/table/sys_flow_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_execution_list"](flow_sys_id="8eecae1b3a5230387ff5f06a91b9fbe9", state="ERROR")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert exec_route.calls.last is not None
        last_request = exec_route.calls.last.request
        qs = parse_qs(urlparse(str(last_request.url)).query)
        query_str = qs["sysparm_query"][0]
        assert "state=ERROR" in query_str

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_execution_list_combined_filters(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Multiple filters are combined in the encoded query."""
        exec_route = respx.get(f"{BASE_URL}/api/now/table/sys_flow_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_execution_list"](
            flow_sys_id="8eecae1b3a5230387ff5f06a91b9fbe9",
            source_record="6d55028a7049dbf2f4275991d6fc81cf",
            state="COMPLETE",
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert exec_route.calls.last is not None
        last_request = exec_route.calls.last.request
        qs = parse_qs(urlparse(str(last_request.url)).query)
        query_str = qs["sysparm_query"][0]
        assert "flow=8eecae1b3a5230387ff5f06a91b9fbe9" in query_str
        assert "source_record=6d55028a7049dbf2f4275991d6fc81cf" in query_str
        assert "state=COMPLETE" in query_str

    @pytest.mark.asyncio()
    async def test_flow_execution_list_no_filter_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """No filters provided returns error response."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_execution_list"]()
        result = decode_response(raw)

        assert result["status"] == "error"
        error_msg = result["error"]["message"] if isinstance(result["error"], dict) else result["error"]
        assert "at least one" in error_msg.lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_execution_list_empty(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Valid filter but no results returns empty list."""
        respx.get(f"{BASE_URL}/api/now/table/sys_flow_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_execution_list"](flow_sys_id="6d90efaf0787be213a9f58202ec86f89")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["executions"] == []


# ---------------------------------------------------------------------------
# flow_execution_detail
# ---------------------------------------------------------------------------


class TestFlowExecutionDetail:
    """Tests for the flow_execution_detail tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_execution_detail_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns context record and ordered log entries."""
        respx.get(f"{BASE_URL}/api/now/table/sys_flow_context/18dbe9bd70e88bd7d141d13c8a46e7d7").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "18dbe9bd70e88bd7d141d13c8a46e7d7",
                        "name": "Auto Assignment Execution",
                        "state": "COMPLETE",
                        "started": "2026-02-20 09:00:00",
                        "ended": "2026-02-20 09:00:05",
                        "flow": "8eecae1b3a5230387ff5f06a91b9fbe9",
                        "source_table": "incident",
                        "source_record": "6d55028a7049dbf2f4275991d6fc81cf",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_flow_log").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "c7437b6f8d9c00ea14eab197e745aacd",
                            "action": "Create Task",
                            "operation": "execute",
                            "level": "info",
                            "message": "Task created successfully",
                            "order": "1",
                            "sys_created_on": "2026-02-20 09:00:01",
                            "output_data": "",
                            "error_message": "",
                            "duration": "0:00:01",
                        },
                        {
                            "sys_id": "d2b4eb18597b547b72c70b31bf26b49e",
                            "action": "Send Email",
                            "operation": "execute",
                            "level": "info",
                            "message": "Email sent",
                            "order": "2",
                            "sys_created_on": "2026-02-20 09:00:03",
                            "output_data": "",
                            "error_message": "",
                            "duration": "0:00:02",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_execution_detail"](context_id="18dbe9bd70e88bd7d141d13c8a46e7d7")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["context"]["state"] == "COMPLETE"
        assert result["data"]["log_count"] == 2
        assert len(result["data"]["logs"]) == 2
        assert result["data"]["logs"][0]["action"] == "Create Task"
        assert result["data"]["logs"][1]["action"] == "Send Email"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_execution_detail_no_logs(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Context exists but no log entries."""
        respx.get(f"{BASE_URL}/api/now/table/sys_flow_context/ddbc98fc6f659935756886374856a903").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "ddbc98fc6f659935756886374856a903",
                        "name": "Empty Execution",
                        "state": "IN_PROGRESS",
                        "started": "2026-02-20 10:00:00",
                        "ended": "",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_flow_log").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_execution_detail"](context_id="ddbc98fc6f659935756886374856a903")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["context"]["sys_id"] == "ddbc98fc6f659935756886374856a903"
        assert result["data"]["log_count"] == 0
        assert result["data"]["logs"] == []

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_execution_detail_not_found(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Context 404 returns error."""
        respx.get(f"{BASE_URL}/api/now/table/sys_flow_context/8aa2a5f2515585b27a0b1e3a9db73823").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Record not found"}})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_flow_log").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_execution_detail"](context_id="8aa2a5f2515585b27a0b1e3a9db73823")
        result = decode_response(raw)

        assert result["status"] == "error"
        assert isinstance(result["error"], dict)
        assert "not found" in result["error"]["message"].lower()


# ---------------------------------------------------------------------------
# flow_snapshot_list
# ---------------------------------------------------------------------------


class TestFlowSnapshotList:
    """Tests for the flow_snapshot_list tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_snapshot_list_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns snapshots for a flow."""
        respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow_snapshot").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "7ee71fe03706c9bcb43380fbe0d2d71e",
                            "name": "Version 2.0",
                            "parent_flow": "8eecae1b3a5230387ff5f06a91b9fbe9",
                            "version": "2.0",
                            "status": "published",
                            "sys_created_on": "2026-02-20 10:00:00",
                            "sys_updated_on": "2026-02-20 10:00:00",
                        },
                        {
                            "sys_id": "f6181240cc65ea9f4a1391d16567586c",
                            "name": "Version 1.0",
                            "parent_flow": "8eecae1b3a5230387ff5f06a91b9fbe9",
                            "version": "1.0",
                            "status": "published",
                            "sys_created_on": "2026-01-15 08:00:00",
                            "sys_updated_on": "2026-01-15 08:00:00",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_snapshot_list"](flow_sys_id="8eecae1b3a5230387ff5f06a91b9fbe9")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert len(result["data"]["snapshots"]) == 2
        assert result["data"]["snapshots"][0]["version"] == "2.0"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_snapshot_list_empty(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """No snapshots returns empty list."""
        respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow_snapshot").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_snapshot_list"](flow_sys_id="6d90efaf0787be213a9f58202ec86f89")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["snapshots"] == []

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_snapshot_list_ordering(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Verify ORDERBYDESCsys_created_on is in the encoded query."""
        snap_route = respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow_snapshot").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_snapshot_list"](flow_sys_id="8eecae1b3a5230387ff5f06a91b9fbe9")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert snap_route.calls.last is not None
        last_request = snap_route.calls.last.request
        qs = parse_qs(urlparse(str(last_request.url)).query)
        assert "ORDERBYDESCsys_created_on" in qs["sysparm_query"][0]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_snapshot_list_with_limit(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Limit is respected in query params."""
        respx.get(f"{BASE_URL}/api/now/table/sys_hub_flow_snapshot").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_snapshot_list"](flow_sys_id="6367c48dd193d56ea7b0baad25b19455", limit=5)
        result = decode_response(raw)
        assert result["status"] == "success"
        req = respx.calls[0].request
        assert "sysparm_limit=5" in str(req.url)


# ---------------------------------------------------------------------------
# workflow_migration_analysis
# ---------------------------------------------------------------------------


class TestWorkflowMigrationAnalysis:
    """Tests for the workflow_migration_analysis tool."""

    def _mock_version(
        self,
        sys_id: str = "e35fec24db6d035c7a6fa33e76847858",
        name: str = "Incident WF 5a6df720540c20d95d530d3fd6885511",
    ) -> None:
        """Mock a wf_workflow_version GET."""
        respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version/{sys_id}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": sys_id,
                        "name": name,
                        "table": "incident",
                        "condition": "priority=1",
                    }
                },
            )
        )

    def _mock_activities(self, activities: list[dict[str, Any]]) -> None:
        """Mock a wf_activity query."""
        respx.get(f"{BASE_URL}/api/now/table/wf_activity").mock(
            return_value=httpx.Response(
                200,
                json={"result": activities},
                headers={"X-Total-Count": str(len(activities))},
            )
        )

    def _mock_transitions(self, transitions: list[dict[str, Any]]) -> None:
        """Mock a wf_transition query."""
        respx.get(f"{BASE_URL}/api/now/table/wf_transition").mock(
            return_value=httpx.Response(
                200,
                json={"result": transitions},
                headers={"X-Total-Count": str(len(transitions))},
            )
        )

    def _mock_variables(self, variables: list[dict[str, Any]]) -> None:
        """Mock a sys_variable_value query."""
        respx.get(f"{BASE_URL}/api/now/table/sys_variable_value").mock(
            return_value=httpx.Response(
                200,
                json={"result": variables},
                headers={"X-Total-Count": str(len(variables))},
            )
        )

    @pytest.mark.asyncio()
    @respx.mock
    async def test_migration_analysis_simple_workflow(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Acyclic workflow with 3 activities and 2 transitions."""
        self._mock_version()
        self._mock_activities(
            [
                {
                    "sys_id": "a37b4556fc38ce6b2a3fd1521b1291bc",
                    "name": "Begin",
                    "activity_definition": "def_begin",
                    "activity_definition.name": "Begin",
                    "activity_definition.category": "Core",
                    "x": "10",
                    "y": "50",
                    "timeout": "",
                    "notes": "",
                },
                {
                    "sys_id": "6ae7ca85d4792cabe5bafd3b8d148725",
                    "name": "Run Script",
                    "activity_definition": "def_rs",
                    "activity_definition.name": "Run Script",
                    "activity_definition.category": "Utilities",
                    "x": "200",
                    "y": "50",
                    "timeout": "",
                    "notes": "",
                },
                {
                    "sys_id": "2ece7051da817573c5081f76dd089a30",
                    "name": "End",
                    "activity_definition": "def_end",
                    "activity_definition.name": "End",
                    "activity_definition.category": "Core",
                    "x": "400",
                    "y": "50",
                    "timeout": "",
                    "notes": "",
                },
            ]
        )
        self._mock_transitions(
            [
                {
                    "sys_id": "82b655b7980ce1431a5665bd5e3fc4fb",
                    "from": "a37b4556fc38ce6b2a3fd1521b1291bc",
                    "from.name": "Begin",
                    "to": "6ae7ca85d4792cabe5bafd3b8d148725",
                    "to.name": "Run Script",
                    "condition": "",
                },
                {
                    "sys_id": "5b2d0cf104fe83a192898b0e1874244f",
                    "from": "6ae7ca85d4792cabe5bafd3b8d148725",
                    "from.name": "Run Script",
                    "to": "2ece7051da817573c5081f76dd089a30",
                    "to.name": "End",
                    "condition": "",
                },
            ]
        )
        self._mock_variables([])

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_migration_analysis"](workflow_version_sys_id="e35fec24db6d035c7a6fa33e76847858")
        result = decode_response(raw)

        assert result["status"] == "success"
        data = result["data"]
        assert data["workflow"]["activity_count"] == 3
        assert data["workflow"]["transition_count"] == 2
        assert data["topology"]["cycles"] == []
        assert data["migration_blockers"] == []
        assert len(data["activity_mapping"]) == 3
        instructions = data["manual_migration_instructions"]
        assert instructions["summary"].startswith("Manually build a Flow Designer flow")
        assert len(instructions["prerequisites"]) >= 3
        assert instructions["build_steps"][0]["title"] == "Create the target flow shell"
        assert len(instructions["activity_translation_steps"]) == 3
        assert instructions["script_migration_notes"] == []
        assert instructions["known_manual_work"] == [
            "No explicit blockers were detected, but the rebuilt flow still needs manual validation in Flow Designer."
        ]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_migration_analysis_with_cycle(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Workflow with a loopback transition detects cycle and creates blocker."""
        self._mock_version()
        self._mock_activities(
            [
                {
                    "sys_id": "a37b4556fc38ce6b2a3fd1521b1291bc",
                    "name": "Begin",
                    "activity_definition": "def_begin",
                    "activity_definition.name": "Begin",
                    "activity_definition.category": "Core",
                    "x": "10",
                    "y": "50",
                    "timeout": "",
                    "notes": "",
                },
                {
                    "sys_id": "6ae7ca85d4792cabe5bafd3b8d148725",
                    "name": "Approval",
                    "activity_definition": "def_approval",
                    "activity_definition.name": "Approval - User",
                    "activity_definition.category": "Approvals",
                    "x": "200",
                    "y": "50",
                    "timeout": "",
                    "notes": "",
                },
                {
                    "sys_id": "2ece7051da817573c5081f76dd089a30",
                    "name": "Set Values",
                    "activity_definition": "def_sv",
                    "activity_definition.name": "Set Values",
                    "activity_definition.category": "Utilities",
                    "x": "400",
                    "y": "50",
                    "timeout": "",
                    "notes": "",
                },
            ]
        )
        self._mock_transitions(
            [
                {
                    "sys_id": "82b655b7980ce1431a5665bd5e3fc4fb",
                    "from": "a37b4556fc38ce6b2a3fd1521b1291bc",
                    "from.name": "Begin",
                    "to": "6ae7ca85d4792cabe5bafd3b8d148725",
                    "to.name": "Approval",
                    "condition": "",
                },
                {
                    "sys_id": "5b2d0cf104fe83a192898b0e1874244f",
                    "from": "6ae7ca85d4792cabe5bafd3b8d148725",
                    "from.name": "Approval",
                    "to": "2ece7051da817573c5081f76dd089a30",
                    "to.name": "Set Values",
                    "condition": "",
                },
                {
                    "sys_id": "fdb792aa92ea3fcdd5ae0cacec93485b",
                    "from": "2ece7051da817573c5081f76dd089a30",
                    "from.name": "Set Values",
                    "to": "6ae7ca85d4792cabe5bafd3b8d148725",
                    "to.name": "Approval",
                    "condition": "rejected",
                },
            ]
        )
        self._mock_variables([])

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_migration_analysis"](workflow_version_sys_id="e35fec24db6d035c7a6fa33e76847858")
        result = decode_response(raw)

        assert result["status"] == "success"
        data = result["data"]
        # Cycle detected between act002 and act003
        assert len(data["topology"]["cycles"]) >= 1
        # At least one cycle-type blocker
        cycle_blockers = [b for b in data["migration_blockers"] if b["type"] == "cycle"]
        assert len(cycle_blockers) >= 1
        assert "Cyclic path" in cycle_blockers[0]["description"]
        # Cycle penalty should be reflected in complexity score
        assert data["complexity"]["breakdown"]["cycle_penalty"] >= 2

    @pytest.mark.asyncio()
    @respx.mock
    async def test_migration_analysis_with_scripts(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Activities with embedded scripts in sys_variable_value are detected."""
        self._mock_version()
        self._mock_activities(
            [
                {
                    "sys_id": "a37b4556fc38ce6b2a3fd1521b1291bc",
                    "name": "Run Script",
                    "activity_definition": "def_rs",
                    "activity_definition.name": "Run Script",
                    "activity_definition.category": "Utilities",
                    "x": "10",
                    "y": "50",
                    "timeout": "",
                    "notes": "",
                },
            ]
        )
        self._mock_transitions([])
        # Multi-line script body (>10 lines triggers script_penalty)
        script_body = "\n".join([f"line_{i} = true;" for i in range(15)])
        self._mock_variables(
            [
                {
                    "sys_id": "eaa2e11f5972c0fd8e423f1c6234180d",
                    "variable": "script",
                    "value": script_body,
                    "document_key": "a37b4556fc38ce6b2a3fd1521b1291bc",
                },
            ]
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_migration_analysis"](workflow_version_sys_id="e35fec24db6d035c7a6fa33e76847858")
        result = decode_response(raw)

        assert result["status"] == "success"
        data = result["data"]
        # Script detected on the activity
        assert data["activity_mapping"][0]["has_script"] is True
        assert data["activity_mapping"][0]["script_line_count"] == 15
        # Script penalty in complexity breakdown
        assert data["complexity"]["breakdown"]["script_penalty"] >= 1
        # Extracted scripts present
        assert len(data["extracted_scripts"]) == 1
        assert data["extracted_scripts"][0]["activity_name"] == "Run Script"
        instructions = data["manual_migration_instructions"]
        assert len(instructions["script_migration_notes"]) == 1
        assert instructions["script_migration_notes"][0]["activity_name"] == "Run Script"
        assert "manually reimplement" in instructions["script_migration_notes"][0]["instruction"].lower()
        assert any("script" in item.lower() for item in instructions["known_manual_work"])

    @pytest.mark.asyncio()
    @respx.mock
    async def test_migration_analysis_multiline_non_script_value_is_not_misclassified(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Multiline text in non-script variables is not treated as executable script."""
        self._mock_version()
        self._mock_activities(
            [
                {
                    "sys_id": "a37b4556fc38ce6b2a3fd1521b1291bc",
                    "name": "Notification Body",
                    "activity_definition": "def_notif",
                    "activity_definition.name": "Notification",
                    "activity_definition.category": "Utilities",
                    "x": "10",
                    "y": "50",
                    "timeout": "",
                    "notes": "",
                },
            ]
        )
        self._mock_transitions([])
        self._mock_variables(
            [
                {
                    "sys_id": "eaa2e11f5972c0fd8e423f1c6234180d",
                    "variable": "message_template",
                    "value": "Hello team,\nPlease review this request.\nThanks.",
                    "document_key": "a37b4556fc38ce6b2a3fd1521b1291bc",
                },
            ]
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_migration_analysis"](workflow_version_sys_id="e35fec24db6d035c7a6fa33e76847858")
        result = decode_response(raw)

        assert result["status"] == "success"
        data = result["data"]
        assert data["activity_mapping"][0]["has_script"] is False
        assert data["activity_mapping"][0]["script_line_count"] == 0
        assert data["extracted_scripts"] == []
        assert data["complexity"]["breakdown"]["script_penalty"] == 0
        assert data["manual_migration_instructions"]["script_migration_notes"] == []

    @pytest.mark.asyncio()
    @respx.mock
    async def test_migration_analysis_code_like_template_value_is_not_treated_as_script(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Code-like words in a non-script variable do not trigger script detection."""
        self._mock_version()
        self._mock_activities(
            [
                {
                    "sys_id": "a37b4556fc38ce6b2a3fd1521b1291bc",
                    "name": "Notification Body",
                    "activity_definition": "def_notif",
                    "activity_definition.name": "Notification",
                    "activity_definition.category": "Utilities",
                    "x": "10",
                    "y": "50",
                    "timeout": "",
                    "notes": "",
                },
            ]
        )
        self._mock_transitions([])
        self._mock_variables(
            [
                {
                    "sys_id": "eaa2e11f5972c0fd8e423f1c6234180d",
                    "variable": "message_template",
                    "value": "If the requester asks for help, return this article to the customer.",
                    "document_key": "a37b4556fc38ce6b2a3fd1521b1291bc",
                },
            ]
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_migration_analysis"](workflow_version_sys_id="e35fec24db6d035c7a6fa33e76847858")
        result = decode_response(raw)

        assert result["status"] == "success"
        data = result["data"]
        assert data["activity_mapping"][0]["has_script"] is False
        assert data["activity_mapping"][0]["script_line_count"] == 0
        assert data["extracted_scripts"] == []
        assert data["manual_migration_instructions"]["script_migration_notes"] == []

    @pytest.mark.asyncio()
    @respx.mock
    async def test_migration_analysis_activity_mapping(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Activity types map to correct Flow Designer equivalents."""
        self._mock_version()
        self._mock_activities(
            [
                {
                    "sys_id": "a37b4556fc38ce6b2a3fd1521b1291bc",
                    "name": "Begin",
                    "activity_definition": "def_begin",
                    "activity_definition.name": "Begin",
                    "activity_definition.category": "Core",
                    "x": "10",
                    "y": "50",
                    "timeout": "",
                    "notes": "",
                },
                {
                    "sys_id": "6ae7ca85d4792cabe5bafd3b8d148725",
                    "name": "Approval Step",
                    "activity_definition": "def_approval",
                    "activity_definition.name": "Approval - User",
                    "activity_definition.category": "Approvals",
                    "x": "200",
                    "y": "50",
                    "timeout": "",
                    "notes": "",
                },
                {
                    "sys_id": "2ece7051da817573c5081f76dd089a30",
                    "name": "Notify User",
                    "activity_definition": "def_notif",
                    "activity_definition.name": "Notification",
                    "activity_definition.category": "Utilities",
                    "x": "400",
                    "y": "50",
                    "timeout": "",
                    "notes": "",
                },
            ]
        )
        self._mock_transitions([])
        self._mock_variables([])

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_migration_analysis"](workflow_version_sys_id="e35fec24db6d035c7a6fa33e76847858")
        result = decode_response(raw)

        assert result["status"] == "success"
        mapping = {m["activity_name"]: m["flow_designer_equivalent"] for m in result["data"]["activity_mapping"]}
        assert mapping["Begin"] == "Flow Trigger"
        assert mapping["Approval Step"] == "Ask for Approval Action"
        assert mapping["Notify User"] == "Send Email Action"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_migration_analysis_unmapped_activity(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Activity with unknown type maps to 'Unknown (Review Manually)'."""
        self._mock_version()
        self._mock_activities(
            [
                {
                    "sys_id": "a37b4556fc38ce6b2a3fd1521b1291bc",
                    "name": "Custom Widget",
                    "activity_definition": "def_custom",
                    "activity_definition.name": "Custom Widget Type",
                    "activity_definition.category": "Custom",
                    "x": "10",
                    "y": "50",
                    "timeout": "",
                    "notes": "",
                },
            ]
        )
        self._mock_transitions([])
        self._mock_variables([])

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_migration_analysis"](workflow_version_sys_id="e35fec24db6d035c7a6fa33e76847858")
        result = decode_response(raw)

        assert result["status"] == "success"
        data = result["data"]
        assert data["activity_mapping"][0]["flow_designer_equivalent"] == "Unknown (Review Manually)"
        assert data["complexity"]["breakdown"]["unmapped_penalty"] == 1
        blockers = [blocker for blocker in data["migration_blockers"] if blocker["type"] == "unmapped_activity"]
        assert len(blockers) == 1
        assert blockers[0]["activity_name"] == "Custom Widget"
        instructions = data["manual_migration_instructions"]
        assert len(instructions["activity_translation_steps"]) == 1
        assert (
            "no safe direct flow designer equivalent"
            in instructions["activity_translation_steps"][0]["manual_instruction"].lower()
        )
        assert any("custom widget" in item.lower() for item in instructions["known_manual_work"])

    @pytest.mark.asyncio()
    @respx.mock
    async def test_migration_analysis_manual_instructions_include_blockers(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Manual instructions surface blocker-driven redesign work for cycles and unmapped activities."""
        self._mock_version(name="Approval Loop Workflow")
        self._mock_activities(
            [
                {
                    "sys_id": "a37b4556fc38ce6b2a3fd1521b1291bc",
                    "name": "Begin",
                    "activity_definition": "def_begin",
                    "activity_definition.name": "Begin",
                    "activity_definition.category": "Core",
                    "x": "10",
                    "y": "50",
                    "timeout": "",
                    "notes": "",
                },
                {
                    "sys_id": "6ae7ca85d4792cabe5bafd3b8d148725",
                    "name": "Rollback Step",
                    "activity_definition": "def_rb",
                    "activity_definition.name": "Rollback To",
                    "activity_definition.category": "Core",
                    "x": "200",
                    "y": "50",
                    "timeout": "",
                    "notes": "",
                },
                {
                    "sys_id": "2ece7051da817573c5081f76dd089a30",
                    "name": "Approval",
                    "activity_definition": "def_approval",
                    "activity_definition.name": "Approval - User",
                    "activity_definition.category": "Approvals",
                    "x": "400",
                    "y": "50",
                    "timeout": "",
                    "notes": "",
                },
            ]
        )
        self._mock_transitions(
            [
                {
                    "sys_id": "82b655b7980ce1431a5665bd5e3fc4fb",
                    "from": "a37b4556fc38ce6b2a3fd1521b1291bc",
                    "from.name": "Begin",
                    "to": "6ae7ca85d4792cabe5bafd3b8d148725",
                    "to.name": "Rollback Step",
                    "condition": "",
                },
                {
                    "sys_id": "5b2d0cf104fe83a192898b0e1874244f",
                    "from": "6ae7ca85d4792cabe5bafd3b8d148725",
                    "from.name": "Rollback Step",
                    "to": "2ece7051da817573c5081f76dd089a30",
                    "to.name": "Approval",
                    "condition": "",
                },
                {
                    "sys_id": "fdb792aa92ea3fcdd5ae0cacec93485b",
                    "from": "2ece7051da817573c5081f76dd089a30",
                    "from.name": "Approval",
                    "to": "6ae7ca85d4792cabe5bafd3b8d148725",
                    "to.name": "Rollback Step",
                    "condition": "rejected",
                },
            ]
        )
        self._mock_variables([])

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_migration_analysis"](workflow_version_sys_id="e35fec24db6d035c7a6fa33e76847858")
        result = decode_response(raw)

        assert result["status"] == "success"
        instructions = result["data"]["manual_migration_instructions"]
        assert any("cyclic paths" in item.lower() for item in instructions["prerequisites"])
        assert any(step["title"] == "Redesign loopback logic" for step in instructions["build_steps"])
        assert any("cyclic path detected" in item.lower() for item in instructions["known_manual_work"])
        assert any("no direct flow designer equivalent" in item.lower() for item in instructions["known_manual_work"])

    @pytest.mark.asyncio()
    @respx.mock
    async def test_migration_analysis_complexity_scoring(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Verify score = base_activities + cycle_penalty + script_penalty + unmapped_penalty."""
        self._mock_version()
        # 2 activities: one unmapped, one with a script >10 lines
        script_body = "\n".join([f"var x{i} = {i};" for i in range(20)])
        self._mock_activities(
            [
                {
                    "sys_id": "a37b4556fc38ce6b2a3fd1521b1291bc",
                    "name": "Known Activity",
                    "activity_definition": "def_if",
                    "activity_definition.name": "If",
                    "activity_definition.category": "Core",
                    "x": "10",
                    "y": "50",
                    "timeout": "",
                    "notes": "",
                },
                {
                    "sys_id": "6ae7ca85d4792cabe5bafd3b8d148725",
                    "name": "Mystery Activity",
                    "activity_definition": "def_mystery",
                    "activity_definition.name": "Mystery Type",
                    "activity_definition.category": "Custom",
                    "x": "200",
                    "y": "50",
                    "timeout": "",
                    "notes": "",
                },
            ]
        )
        # Cycle: act001 -> act002 -> act001
        self._mock_transitions(
            [
                {
                    "sys_id": "82b655b7980ce1431a5665bd5e3fc4fb",
                    "from": "a37b4556fc38ce6b2a3fd1521b1291bc",
                    "from.name": "Known Activity",
                    "to": "6ae7ca85d4792cabe5bafd3b8d148725",
                    "to.name": "Mystery Activity",
                    "condition": "",
                },
                {
                    "sys_id": "5b2d0cf104fe83a192898b0e1874244f",
                    "from": "6ae7ca85d4792cabe5bafd3b8d148725",
                    "from.name": "Mystery Activity",
                    "to": "a37b4556fc38ce6b2a3fd1521b1291bc",
                    "to.name": "Known Activity",
                    "condition": "",
                },
            ]
        )
        self._mock_variables(
            [
                {
                    "sys_id": "eaa2e11f5972c0fd8e423f1c6234180d",
                    "variable": "script",
                    "value": script_body,
                    "document_key": "a37b4556fc38ce6b2a3fd1521b1291bc",
                },
            ]
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_migration_analysis"](workflow_version_sys_id="e35fec24db6d035c7a6fa33e76847858")
        result = decode_response(raw)

        assert result["status"] == "success"
        breakdown = result["data"]["complexity"]["breakdown"]
        assert breakdown["base_activities"] == 2
        assert breakdown["cycle_penalty"] >= 2  # At least 1 cycle * 2
        assert breakdown["script_penalty"] == 1  # One script >10 lines
        assert breakdown["unmapped_penalty"] == 1  # One unknown type

        # Total score = base + cycle + script + unmapped
        expected_score = (
            breakdown["base_activities"]
            + breakdown["cycle_penalty"]
            + breakdown["script_penalty"]
            + breakdown["unmapped_penalty"]
        )
        assert result["data"]["complexity"]["score"] == expected_score

    @pytest.mark.asyncio()
    @respx.mock
    async def test_migration_analysis_empty_workflow(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Version with no activities or transitions."""
        self._mock_version(name="Empty Workflow")
        self._mock_activities([])
        self._mock_transitions([])
        # No activities means no sys_variable_value query is made

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_migration_analysis"](workflow_version_sys_id="e35fec24db6d035c7a6fa33e76847858")
        result = decode_response(raw)

        assert result["status"] == "success"
        data = result["data"]
        assert data["workflow"]["activity_count"] == 0
        assert data["workflow"]["transition_count"] == 0
        assert data["topology"]["activities"] == []
        assert data["topology"]["transitions"] == []
        assert data["topology"]["cycles"] == []
        assert data["activity_mapping"] == []
        assert data["extracted_scripts"] == []
        assert data["complexity"]["score"] == 0
        assert data["complexity"]["breakdown"]["base_activities"] == 0

    @pytest.mark.asyncio()
    @respx.mock
    async def test_migration_analysis_rollback_turnback_blockers(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Activities with Rollback To / Turnback To generate unmapped_activity blockers."""
        self._mock_version()
        self._mock_activities(
            [
                {
                    "sys_id": "fdc79c389c219498815dbde80a740ac4",
                    "name": "Rollback Step",
                    "activity_definition": "def_rb",
                    "activity_definition.name": "Rollback To",
                    "activity_definition.category": "Core",
                    "x": "10",
                    "y": "50",
                    "timeout": "",
                    "notes": "",
                },
                {
                    "sys_id": "bd3952ef766e436ed570bc8af337e0ab",
                    "name": "Turnback Step",
                    "activity_definition": "def_tb",
                    "activity_definition.name": "Turnback To",
                    "activity_definition.category": "Core",
                    "x": "200",
                    "y": "50",
                    "timeout": "",
                    "notes": "",
                },
            ]
        )
        self._mock_transitions([])
        self._mock_variables([])

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_migration_analysis"](workflow_version_sys_id="e35fec24db6d035c7a6fa33e76847858")
        result = decode_response(raw)

        assert result["status"] == "success"
        blockers = result["data"]["migration_blockers"]
        unmapped = [b for b in blockers if b["type"] == "unmapped_activity"]
        assert len(unmapped) == 2
        blocker_names = {b["activity_name"] for b in unmapped}
        assert blocker_names == {"Rollback Step", "Turnback Step"}


# ---------------------------------------------------------------------------
# Dict reference field handling
# ---------------------------------------------------------------------------


class TestFlowDesignerDictReferenceFields:
    """Verify flow designer tools handle dict reference fields from ServiceNow display_value responses."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flow_action_detail_handles_dict_action_type(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """flow_action_detail handles action_type returned as a dict reference."""
        dict_action_type = {
            "display_value": "13619c05f5ecfe7907f8a0677a47a1d2",
            "link": "https://test.service-now.com/api/...",
        }

        respx.get(f"{BASE_URL}/api/now/table/sys_hub_action_instance/ee62d9d23f50afc16c59cd7bf652888b").mock(
            side_effect=[
                # First call: raw (display_values=False) - returns dict reference
                httpx.Response(
                    200,
                    json={
                        "result": {
                            "sys_id": "ee62d9d23f50afc16c59cd7bf652888b",
                            "name": "Send Email",
                            "action_type": dict_action_type,
                        }
                    },
                ),
                # Second call: display (display_values=True)
                httpx.Response(
                    200,
                    json={
                        "result": {
                            "sys_id": "ee62d9d23f50afc16c59cd7bf652888b",
                            "name": "Send Email",
                            "action_type": "Send Email Action",
                        }
                    },
                ),
            ]
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_hub_action_type_definition/13619c05f5ecfe7907f8a0677a47a1d2").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": "13619c05f5ecfe7907f8a0677a47a1d2", "name": "Send Email Type"}},
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_hub_step_instance").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["flow_action_detail"](action_instance_sys_id="ee62d9d23f50afc16c59cd7bf652888b")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["type_definition"] is not None
        assert result["data"]["type_definition"]["name"] == "Send Email Type"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_migration_analysis_handles_dict_sys_ids(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """workflow_migration_analysis handles from, to, document_key, and definition name as dicts."""
        respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version/ed05ba708c14a19d2afe245830c0f1e5").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "ed05ba708c14a19d2afe245830c0f1e5",
                        "name": "Migration WF",
                        "table": "incident",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_activity").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "f8a6be5668c8de0f81a1b30a4cf8face",
                            "name": "Start",
                            "activity_definition": "def_begin",
                            "activity_definition.name": {
                                "display_value": "Begin",
                                "link": "https://test.service-now.com/api/...",
                            },
                            "activity_definition.category": "Core",
                            "x": "10",
                            "y": "20",
                            "timeout": "",
                            "notes": "",
                        },
                        {
                            "sys_id": "27ebb39863c212df73df6e9ce50b58a4",
                            "name": "End",
                            "activity_definition": "def_end",
                            "activity_definition.name": {
                                "display_value": "End",
                                "link": "https://test.service-now.com/api/...",
                            },
                            "activity_definition.category": "Core",
                            "x": "200",
                            "y": "20",
                            "timeout": "",
                            "notes": "",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_transition").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "a5d23ceaba84465f5cebeb9a4c0c4836",
                            "from": {
                                "display_value": "f8a6be5668c8de0f81a1b30a4cf8face",
                                "link": "https://test.service-now.com/api/...",
                            },
                            "from.name": "Start",
                            "to": {
                                "display_value": "27ebb39863c212df73df6e9ce50b58a4",
                                "link": "https://test.service-now.com/api/...",
                            },
                            "to.name": "End",
                            "condition": "",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_variable_value").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "var_dict",
                            "variable": "script",
                            "value": "gs.log('test');",
                            "document_key": {
                                "display_value": "f8a6be5668c8de0f81a1b30a4cf8face",
                                "link": "https://test.service-now.com/api/...",
                            },
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_migration_analysis"](workflow_version_sys_id="ed05ba708c14a19d2afe245830c0f1e5")
        result = decode_response(raw)

        assert result["status"] == "success"
        data = result["data"]
        assert data["workflow"]["activity_count"] == 2
        assert data["workflow"]["transition_count"] == 1
        # Activity mapping should resolve dict reference fields to strings
        mapping = data["activity_mapping"]
        assert len(mapping) == 2
        assert mapping[0]["activity_sys_id"] == "f8a6be5668c8de0f81a1b30a4cf8face"
        assert mapping[1]["activity_sys_id"] == "27ebb39863c212df73df6e9ce50b58a4"
        # Definition names should be resolved from dicts
        assert mapping[0]["legacy_type"] == "Begin"
        assert mapping[1]["legacy_type"] == "End"


# ---------------------------------------------------------------------------
# _process_neighbor helper
# ---------------------------------------------------------------------------


class TestProcessNeighborHelper:
    """Unit tests for the _process_neighbor graph traversal helper."""

    def test_neighbor_not_in_color_returns_early(self) -> None:
        """When neighbor is not in the color dict, _process_neighbor returns without side effects."""
        color: dict[str, int] = {"A": 0}  # neighbor "B" is not in color
        path: list[str] = ["A"]
        stack: list[tuple[str, int]] = [("A", 0)]
        cycles: list[list[str]] = []

        _process_neighbor("B", color, path, stack, cycles)

        # Nothing should change
        assert color == {"A": 0}
        assert path == ["A"]
        assert stack == [("A", 0)]
        assert cycles == []
