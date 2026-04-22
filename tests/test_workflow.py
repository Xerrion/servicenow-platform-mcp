"""Tests for workflow introspection tools."""

from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict[str, Any]:
    """Helper: register workflow tools on a fresh MCP server and return tool map."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.workflow import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return get_tool_functions(mcp)


# ---------------------------------------------------------------------------
# Tool Registration
# ---------------------------------------------------------------------------


class TestWorkflowToolRegistration:
    """Verify all workflow tools register correctly."""

    def test_registers_all_five_tools(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """All five workflow tools are registered on the MCP server."""
        tools = _register_and_get_tools(settings, auth_provider)
        expected = {
            "workflow_contexts",
            "workflow_map",
            "workflow_status",
            "workflow_activity_detail",
            "workflow_version_list",
        }
        assert expected == set(tools.keys())


# ---------------------------------------------------------------------------
# workflow_contexts
# ---------------------------------------------------------------------------


class TestWorkflowContexts:
    """Tests for the workflow_contexts tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_both_legacy_and_flow_contexts(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Returns legacy workflow contexts and flow designer contexts."""
        respx.get(f"{BASE_URL}/api/now/table/wf_context").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "0b194673d7cf75a9b42d71790986d412",
                            "name": "Incident Workflow",
                            "state": "Executing",
                            "started": "2026-02-20 09:00:00",
                            "ended": "",
                            "workflow_version": "e35fec24db6d035c7a6fa33e76847858",
                            "table": "incident",
                            "result": "",
                            "running_duration": "00:05:00",
                            "active": "true",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_flow_context").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "b45e2ae965e607a079b6677d64ba7c83",
                            "name": "Auto-assign Flow",
                            "state": "Completed",
                            "started": "2026-02-20 09:00:00",
                            "ended": "2026-02-20 09:00:05",
                            "flow_version": "fc9fb9c558787ac4d0406eeb7e1814ac",
                            "source_table": "incident",
                            "source_record": "6d55028a7049dbf2f4275991d6fc81cf",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_contexts"](record_sys_id="6d55028a7049dbf2f4275991d6fc81cf")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert len(result["data"]["legacy_workflows"]) == 1
        assert len(result["data"]["flow_designer"]) == 1
        assert result["data"]["legacy_workflows"][0]["name"] == "Incident Workflow"
        assert result["data"]["flow_designer"][0]["name"] == "Auto-assign Flow"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_empty_results_for_both_engines(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns empty lists when no contexts found."""
        respx.get(f"{BASE_URL}/api/now/table/wf_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_flow_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_contexts"](record_sys_id="2edef9aa2e99060fd11a80ae6eed85b5")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["legacy_workflows"] == []
        assert result["data"]["flow_designer"] == []

    @pytest.mark.asyncio()
    @respx.mock
    async def test_filters_by_state(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Passes state filter through to the legacy query."""
        legacy_route = respx.get(f"{BASE_URL}/api/now/table/wf_context").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "36ddcab7c62bc858d677d1883197e5c3",
                            "name": "Finished WF",
                            "state": "Finished",
                            "started": "2026-02-20 08:00:00",
                            "ended": "2026-02-20 08:05:00",
                            "workflow_version": "5780f78f6d55b5bf954f58a084659629",
                            "table": "incident",
                            "result": "success",
                            "running_duration": "00:05:00",
                            "active": "false",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_flow_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_contexts"](record_sys_id="6d55028a7049dbf2f4275991d6fc81cf", state="finished")
        result = decode_response(raw)

        assert result["status"] == "success"
        # Verify state was included in the legacy encoded query
        assert legacy_route.calls.last is not None
        last_request = legacy_route.calls.last.request
        qs = parse_qs(urlparse(str(last_request.url)).query)
        assert "state=finished" in qs["sysparm_query"][0]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_filters_by_table(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Passes table filter through to the legacy query."""
        legacy_route = respx.get(f"{BASE_URL}/api/now/table/wf_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_flow_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_contexts"](record_sys_id="6d55028a7049dbf2f4275991d6fc81cf", table="incident")
        result = decode_response(raw)

        assert result["status"] == "success"
        # Verify table was included in the legacy encoded query
        assert legacy_route.calls.last is not None
        last_request = legacy_route.calls.last.request
        qs = parse_qs(urlparse(str(last_request.url)).query)
        assert "table=incident" in qs["sysparm_query"][0]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_handles_server_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error envelope when legacy context query fails."""
        respx.get(f"{BASE_URL}/api/now/table/wf_context").mock(
            return_value=httpx.Response(500, json={"error": {"message": "Server error"}})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_flow_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_contexts"](record_sys_id="6d55028a7049dbf2f4275991d6fc81cf")
        result = decode_response(raw)

        assert result["status"] == "error"
        assert isinstance(result["error"], dict)
        assert "Server error" in result["error"]["message"]


# ---------------------------------------------------------------------------
# workflow_map
# ---------------------------------------------------------------------------


class TestWorkflowMap:
    """Tests for the workflow_map tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_full_map(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns version, activities, and transitions."""
        respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version/e35fec24db6d035c7a6fa33e76847858").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "e35fec24db6d035c7a6fa33e76847858",
                        "name": "Incident Workflow a1047eab1035d58682a53557e0b2a75e",
                        "table": "incident",
                        "active": "true",
                        "published": "true",
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
                            "sys_id": "a37b4556fc38ce6b2a3fd1521b1291bc",
                            "name": "Begin",
                            "activity_definition": "f45ee39efdcca5805d1e7e2eaa97f27b",
                            "activity_definition.name": "Begin",
                            "activity_definition.category": "Core",
                            "x": "50",
                            "y": "100",
                            "timeout": "",
                            "notes": "",
                            "out_of_date": "false",
                            "is_parent": "false",
                            "stage": "",
                        },
                        {
                            "sys_id": "6ae7ca85d4792cabe5bafd3b8d148725",
                            "name": "Approval",
                            "activity_definition": "b7c009d7ad464b132e5a45f7f541ac3e",
                            "activity_definition.name": "Approval - User",
                            "activity_definition.category": "Approvals",
                            "x": "200",
                            "y": "100",
                            "timeout": "86400",
                            "notes": "Manager approval",
                            "out_of_date": "false",
                            "is_parent": "false",
                            "stage": "",
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
                            "sys_id": "82b655b7980ce1431a5665bd5e3fc4fb",
                            "from": "a37b4556fc38ce6b2a3fd1521b1291bc",
                            "from.name": "Begin",
                            "to": "6ae7ca85d4792cabe5bafd3b8d148725",
                            "to.name": "Approval",
                            "condition": "",
                        },
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
                            "sys_id": "eaa2e11f5972c0fd8e423f1c6234180d",
                            "variable": "var_def_001",
                            "value": "some_script_body",
                            "document_key": "6ae7ca85d4792cabe5bafd3b8d148725",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_map"](workflow_version_sys_id="e35fec24db6d035c7a6fa33e76847858")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["version"]["name"] == "Incident Workflow a1047eab1035d58682a53557e0b2a75e"
        assert len(result["data"]["activities"]) == 2
        assert len(result["data"]["transitions"]) == 1
        assert result["data"]["transitions"][0]["from.name"] == "Begin"
        # act001 has no variables, act002 has one
        assert result["data"]["activities"][0].get("variables") == []
        assert len(result["data"]["activities"][1]["variables"]) == 1
        assert result["data"]["activities"][1]["variables"][0]["value"] == "some_script_body"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_empty_activities_and_transitions(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns version with empty activities and transitions lists."""
        respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version/7e5366f16c281ccf6eb5fab870d009b3").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "7e5366f16c281ccf6eb5fab870d009b3",
                        "name": "Empty Workflow",
                        "table": "incident",
                        "active": "true",
                        "published": "false",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_activity").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_transition").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_map"](workflow_version_sys_id="7e5366f16c281ccf6eb5fab870d009b3")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["activities"] == []
        assert result["data"]["transitions"] == []

    @pytest.mark.asyncio()
    @respx.mock
    async def test_activities_sorted_by_x_position(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Activities are returned ordered by x position from the query."""
        respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version/2759e538b7ba0684c83e050d1a9f0977").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": "2759e538b7ba0684c83e050d1a9f0977", "name": "Sort Test WF"}},
            )
        )
        # Activities returned pre-sorted by x (as ServiceNow would return them)
        respx.get(f"{BASE_URL}/api/now/table/wf_activity").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "f29bc91bbdab169fc0c0a326965953d1",
                            "name": "First",
                            "x": "10",
                            "y": "50",
                        },
                        {
                            "sys_id": "b9f85daa6f83cf02ce5c31913d1f64d3",
                            "name": "Second",
                            "x": "200",
                            "y": "50",
                        },
                        {
                            "sys_id": "252bc06763afb3b6c2a0802f7346700a",
                            "name": "Third",
                            "x": "400",
                            "y": "50",
                        },
                    ]
                },
                headers={"X-Total-Count": "3"},
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_transition").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_variable_value").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_map"](workflow_version_sys_id="2759e538b7ba0684c83e050d1a9f0977")
        result = decode_response(raw)

        assert result["status"] == "success"
        names = [a["name"] for a in result["data"]["activities"]]
        assert names == ["First", "Second", "Third"]
        assert all(a["variables"] == [] for a in result["data"]["activities"])

    @pytest.mark.asyncio()
    @respx.mock
    async def test_error_on_missing_version(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns success with a warning when the workflow version is not found (graceful degradation)."""
        respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version/35a1af58a3b00bde3d8af97f82562ac2").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_activity").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_transition").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_map"](workflow_version_sys_id="35a1af58a3b00bde3d8af97f82562ac2")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result.get("warnings")
        assert any("version" in w.lower() for w in result["warnings"])

    @pytest.mark.asyncio()
    @respx.mock
    async def test_map_groups_variables_by_activity(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Variables are grouped correctly per activity in the map."""
        respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version/f4e5800ef04e1ddae096cab27268a211").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": "f4e5800ef04e1ddae096cab27268a211", "name": "Vars Test WF"}},
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_activity").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "f29bc91bbdab169fc0c0a326965953d1",
                            "name": "Script 1",
                            "x": "10",
                            "y": "50",
                        },
                        {
                            "sys_id": "b9f85daa6f83cf02ce5c31913d1f64d3",
                            "name": "Script 2",
                            "x": "200",
                            "y": "50",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_transition").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_variable_value").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "30a262ee0cddf15a17abb23e11148aa3",
                            "variable": "a1dd4114c98069523cdf1d90ff3a4322",
                            "value": "script_a1",
                            "document_key": "f29bc91bbdab169fc0c0a326965953d1",
                        },
                        {
                            "sys_id": "2f0c71c73a82a38798e5249c3cfcecd6",
                            "variable": "9b63072850833c63c5200f2c35b3edc4",
                            "value": "script_a2",
                            "document_key": "b9f85daa6f83cf02ce5c31913d1f64d3",
                        },
                        {
                            "sys_id": "8c958ae3b81b07ee490d75b7677f1b23",
                            "variable": "216971a1b9ae32b8e314c5908f4e6f32",
                            "value": "condition_a1",
                            "document_key": "f29bc91bbdab169fc0c0a326965953d1",
                        },
                    ]
                },
                headers={"X-Total-Count": "3"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_map"](workflow_version_sys_id="f4e5800ef04e1ddae096cab27268a211")
        result = decode_response(raw)

        assert result["status"] == "success"
        activities = result["data"]["activities"]
        # a1 should have 2 variables, a2 should have 1
        a1 = next(a for a in activities if a["sys_id"] == "f29bc91bbdab169fc0c0a326965953d1")
        a2 = next(a for a in activities if a["sys_id"] == "b9f85daa6f83cf02ce5c31913d1f64d3")
        assert len(a1["variables"]) == 2
        assert len(a2["variables"]) == 1

    @pytest.mark.asyncio()
    @respx.mock
    async def test_map_empty_activities_skips_variable_fetch(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """No sys_variable_value call when there are no activities."""
        respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version/a7d9eb2e84eb49b6fcca92d84b48c1b4").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "a7d9eb2e84eb49b6fcca92d84b48c1b4",
                        "name": "No Activities WF",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_activity").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_transition").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        # Do NOT mock sys_variable_value - if it's called, the test will fail with ConnectionError

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_map"](workflow_version_sys_id="a7d9eb2e84eb49b6fcca92d84b48c1b4")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["activities"] == []


# ---------------------------------------------------------------------------
# workflow_status
# ---------------------------------------------------------------------------


class TestWorkflowStatus:
    """Tests for the workflow_status tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_context_with_executing_and_history(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Returns context record alongside executing and history lists."""
        respx.get(f"{BASE_URL}/api/now/table/wf_context/18dbe9bd70e88bd7d141d13c8a46e7d7").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "18dbe9bd70e88bd7d141d13c8a46e7d7",
                        "name": "Incident WF",
                        "state": "Executing",
                        "started": "2026-02-20 09:00:00",
                        "ended": "",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_executing").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "594e89ed796189a6c8478cdb53c74590",
                            "activity": "a37b4556fc38ce6b2a3fd1521b1291bc",
                            "activity.name": "Approval",
                            "activity.activity_definition.name": "Approval - User",
                            "state": "executing",
                            "started": "2026-02-20 09:01:00",
                            "due": "2026-02-21 09:01:00",
                            "result": "",
                            "fault_description": "",
                            "activity_index": "1",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_history").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "8163f692da15397336fa06c365f6d8d5",
                            "activity": "1038802d0f0b75fbcd213663da9b65e4",
                            "activity.name": "Begin",
                            "activity.activity_definition.name": "Begin",
                            "state": "finished",
                            "started": "2026-02-20 09:00:00",
                            "ended": "2026-02-20 09:00:01",
                            "result": "success",
                            "fault_description": "",
                            "activity_index": "0",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_status"](context_sys_id="18dbe9bd70e88bd7d141d13c8a46e7d7")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["context"]["state"] == "Executing"
        assert len(result["data"]["executing"]) == 1
        assert len(result["data"]["history"]) == 1
        assert result["data"]["executing"][0]["activity.name"] == "Approval"
        assert result["data"]["history"][0]["result"] == "success"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_empty_executing_all_completed(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns empty executing list when all activities have finished."""
        respx.get(f"{BASE_URL}/api/now/table/wf_context/c7d0e000000000000000000000000001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "c7d0e000000000000000000000000001",
                        "name": "Completed WF",
                        "state": "Finished",
                        "started": "2026-02-20 08:00:00",
                        "ended": "2026-02-20 08:10:00",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_executing").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_history").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "ac4ae97285c19b13201deb9b192d9213",
                            "activity": "f29bc91bbdab169fc0c0a326965953d1",
                            "activity.name": "Begin",
                            "state": "finished",
                            "started": "2026-02-20 08:00:00",
                            "ended": "2026-02-20 08:00:01",
                            "result": "success",
                            "fault_description": "",
                            "activity_index": "0",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_status"](context_sys_id="c7d0e000000000000000000000000001")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["executing"] == []
        assert len(result["data"]["history"]) == 1

    @pytest.mark.asyncio()
    @respx.mock
    async def test_multiple_executing_activities(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns multiple currently-executing activities."""
        respx.get(f"{BASE_URL}/api/now/table/wf_context/c7d0e000000000000000000000000002").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "c7d0e000000000000000000000000002",
                        "name": "Parallel WF",
                        "state": "Executing",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_executing").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "d2044f50319b6b469429ec5351129c8e",
                            "activity": "f29bc91bbdab169fc0c0a326965953d1",
                            "activity.name": "Task A",
                            "state": "executing",
                            "started": "2026-02-20 09:00:00",
                            "fault_description": "",
                        },
                        {
                            "sys_id": "f28239a0fde944f345b70dcb6b6a24a2",
                            "activity": "b9f85daa6f83cf02ce5c31913d1f64d3",
                            "activity.name": "Task B",
                            "state": "executing",
                            "started": "2026-02-20 09:00:00",
                            "fault_description": "",
                        },
                        {
                            "sys_id": "9f7f3ade76b193090f617a43abc94ead",
                            "activity": "252bc06763afb3b6c2a0802f7346700a",
                            "activity.name": "Task C",
                            "state": "executing",
                            "started": "2026-02-20 09:00:01",
                            "fault_description": "",
                        },
                    ]
                },
                headers={"X-Total-Count": "3"},
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_history").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_status"](context_sys_id="c7d0e000000000000000000000000002")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert len(result["data"]["executing"]) == 3

    @pytest.mark.asyncio()
    @respx.mock
    async def test_history_with_faults(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns history entries with populated fault_description."""
        respx.get(f"{BASE_URL}/api/now/table/wf_context/c7d0e000000000000000000000000003").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "c7d0e000000000000000000000000003",
                        "name": "Faulted WF",
                        "state": "Finished",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_executing").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_history").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "ac4ae97285c19b13201deb9b192d9213",
                            "activity": "f29bc91bbdab169fc0c0a326965953d1",
                            "activity.name": "Run Script",
                            "state": "faulted",
                            "started": "2026-02-20 09:00:00",
                            "ended": "2026-02-20 09:00:02",
                            "result": "error",
                            "fault_description": "NullPointerException at line 42",
                            "activity_index": "1",
                        },
                        {
                            "sys_id": "bf1c365741a4bfb5fee5c3150335ab4f",
                            "activity": "b9f85daa6f83cf02ce5c31913d1f64d3",
                            "activity.name": "Notification",
                            "state": "faulted",
                            "started": "2026-02-20 09:00:03",
                            "ended": "2026-02-20 09:00:04",
                            "result": "error",
                            "fault_description": "Invalid email address",
                            "activity_index": "2",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_status"](context_sys_id="c7d0e000000000000000000000000003")
        result = decode_response(raw)

        assert result["status"] == "success"
        history = result["data"]["history"]
        assert len(history) == 2
        assert history[0]["fault_description"] == "NullPointerException at line 42"
        assert history[1]["fault_description"] == "Invalid email address"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_error_on_missing_context(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error envelope when wf_context is not found."""
        respx.get(f"{BASE_URL}/api/now/table/wf_context/05b6177456ea350bf56f9bc663468a10").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Record not found"}})
        )
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_status"](context_sys_id="05b6177456ea350bf56f9bc663468a10")
        result = decode_response(raw)

        assert result["status"] == "error"
        assert isinstance(result["error"], dict)
        assert "Record not found" in result["error"]["message"]


# ---------------------------------------------------------------------------
# workflow_activity_detail
# ---------------------------------------------------------------------------


class TestWorkflowActivityDetail:
    """Tests for the workflow_activity_detail tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_activity_with_linked_definition(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Returns activity display values alongside the element definition."""

        # Phase 1: raw activity (display_values=False) to get the definition sys_id
        def _act001_side_effect(request: httpx.Request) -> httpx.Response:
            result = (
                {
                    "sys_id": "a37b4556fc38ce6b2a3fd1521b1291bc",
                    "name": "Approval",
                    "activity_definition": "f45ee39efdcca5805d1e7e2eaa97f27b",
                    "x": "200",
                    "y": "100",
                }
                if "sysparm_display_value" not in str(request.url)
                else {
                    "sys_id": "a37b4556fc38ce6b2a3fd1521b1291bc",
                    "name": "Approval",
                    "activity_definition": "Approval - User",
                    "x": "200",
                    "y": "100",
                }
            )
            return httpx.Response(200, json={"result": result})

        respx.get(f"{BASE_URL}/api/now/table/wf_activity/a37b4556fc38ce6b2a3fd1521b1291bc").mock(side_effect=_act001_side_effect)
        # Phase 2: element definition with display values
        respx.get(f"{BASE_URL}/api/now/table/wf_element_definition/f45ee39efdcca5805d1e7e2eaa97f27b").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "f45ee39efdcca5805d1e7e2eaa97f27b",
                        "name": "Approval - User",
                        "category": "Approvals",
                        "description": "Generates a user approval request",
                        "access": "public",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_variable_value").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "47a920e21ce3b82c733a27e71b6e24b7",
                            "variable": "Script",
                            "value": "current.state = 2;",
                            "document_key": "a37b4556fc38ce6b2a3fd1521b1291bc",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_activity_detail"](activity_sys_id="a37b4556fc38ce6b2a3fd1521b1291bc")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["activity"]["sys_id"] == "a37b4556fc38ce6b2a3fd1521b1291bc"
        assert result["data"]["definition"]["name"] == "Approval - User"
        assert result["data"]["definition"]["category"] == "Approvals"
        assert len(result["data"]["variables"]) == 1
        # Script-body masking is on by default; this assertion covers the
        # unmasked round-trip path exposed by ``include_script_body=True``.
        raw_with_body = await tools["workflow_activity_detail"](
            activity_sys_id="a37b4556fc38ce6b2a3fd1521b1291bc",
            include_script_body=True,
        )
        result_with_body = decode_response(raw_with_body)
        assert result_with_body["data"]["variables"][0]["value"] == "current.state = 2;"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_activity_with_no_definition(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns definition as None when activity has no linked definition."""

        # Phase 1: raw activity with empty activity_definition
        def _nodef_side_effect(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "a40bb1d2559337415c49dfd982cf2fe2",
                        "name": "Custom Activity",
                        "activity_definition": "",
                        "x": "100",
                        "y": "50",
                    }
                },
            )

        respx.get(f"{BASE_URL}/api/now/table/wf_activity/a40bb1d2559337415c49dfd982cf2fe2").mock(side_effect=_nodef_side_effect)
        respx.get(f"{BASE_URL}/api/now/table/sys_variable_value").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_activity_detail"](activity_sys_id="a40bb1d2559337415c49dfd982cf2fe2")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["activity"]["sys_id"] == "a40bb1d2559337415c49dfd982cf2fe2"
        assert result["data"]["definition"] is None
        assert result["data"]["variables"] == []

    @pytest.mark.asyncio()
    @respx.mock
    async def test_error_on_missing_activity(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error when the activity record is not found."""
        respx.get(f"{BASE_URL}/api/now/table/wf_activity/35a1af58a3b00bde3d8af97f82562ac2").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_activity_detail"](activity_sys_id="35a1af58a3b00bde3d8af97f82562ac2")
        result = decode_response(raw)

        assert result["status"] == "error"
        assert isinstance(result["error"], dict)
        assert "Not found" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_activity_includes_multiple_variables(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Returns multiple configured variables for an activity."""

        def _act_vars_side_effect(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "20de94ee488dbd2b7154c3afbf85cd22",
                        "name": "Run Script",
                        "activity_definition": "def_script",
                        "x": "100",
                        "y": "50",
                    }
                },
            )

        respx.get(f"{BASE_URL}/api/now/table/wf_activity/20de94ee488dbd2b7154c3afbf85cd22").mock(side_effect=_act_vars_side_effect)
        respx.get(f"{BASE_URL}/api/now/table/wf_element_definition/def_script").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "def_script",
                        "name": "Run Script",
                        "category": "Utilities",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_variable_value").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "30a262ee0cddf15a17abb23e11148aa3",
                            "variable": "Script",
                            "value": "gs.log('hello');",
                            "document_key": "20de94ee488dbd2b7154c3afbf85cd22",
                        },
                        {
                            "sys_id": "2f0c71c73a82a38798e5249c3cfcecd6",
                            "variable": "Condition",
                            "value": "current.active == true",
                            "document_key": "20de94ee488dbd2b7154c3afbf85cd22",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_activity_detail"](
            activity_sys_id="20de94ee488dbd2b7154c3afbf85cd22",
            include_script_body=True,
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert len(result["data"]["variables"]) == 2
        values = [v["value"] for v in result["data"]["variables"]]
        assert "gs.log('hello');" in values
        assert "current.active == true" in values


# ---------------------------------------------------------------------------
# workflow_version_list
# ---------------------------------------------------------------------------


class TestWorkflowVersionList:
    """Tests for the workflow_version_list tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_versions_for_table(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns workflow versions defined for a specific table."""
        respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "e35fec24db6d035c7a6fa33e76847858",
                            "name": "Incident Workflow 5a6df720540c20d95d530d3fd6885511",
                            "table": "incident",
                            "description": "Handles incident lifecycle",
                            "active": "true",
                            "published": "true",
                            "checked_out": "",
                            "checked_out_by": "",
                            "workflow": "7d252727b60e4ff223a6aa6f13e46417",
                        },
                        {
                            "sys_id": "5780f78f6d55b5bf954f58a084659629",
                            "name": "Incident Workflow a1047eab1035d58682a53557e0b2a75e",
                            "table": "incident",
                            "description": "Updated incident lifecycle",
                            "active": "true",
                            "published": "true",
                            "checked_out": "",
                            "checked_out_by": "",
                            "workflow": "7d252727b60e4ff223a6aa6f13e46417",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_version_list"](table="incident")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert len(result["data"]["versions"]) == 2
        assert result["data"]["versions"][0]["table"] == "incident"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_empty_result(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns empty versions list when none found."""
        respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_version_list"](table="change_request")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["versions"] == []

    @pytest.mark.asyncio()
    @respx.mock
    async def test_active_only_false_includes_inactive(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Omits the active filter when active_only is False."""
        version_route = respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "ff36fb9fb5cad6865cdbd6293b2a5ad1",
                            "name": "Old Workflow",
                            "table": "incident",
                            "active": "false",
                            "published": "false",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_version_list"](table="incident", active_only=False)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert len(result["data"]["versions"]) == 1
        # Verify active=true was NOT in the encoded query
        assert version_route.calls.last is not None
        last_request = version_route.calls.last.request
        qs = parse_qs(urlparse(str(last_request.url)).query)
        assert "active=true" not in qs["sysparm_query"][0]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_versions_with_display_values(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Display values are requested for version records."""
        version_route = respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "6732202e50cf54c4edbc514a820a4e2e",
                            "name": "Problem Workflow",
                            "table": "problem",
                            "description": "Problem management",
                            "active": "true",
                            "published": "true",
                            "checked_out": "",
                            "checked_out_by": "",
                            "workflow": "Parent Workflow Name",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_version_list"](table="problem")
        result = decode_response(raw)

        assert result["status"] == "success"
        # Verify display_value=true was in the query params
        assert version_route.calls.last is not None
        last_request = version_route.calls.last.request
        assert "sysparm_display_value=true" in str(last_request.url)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_handles_server_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error envelope on server error."""
        respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version").mock(
            return_value=httpx.Response(500, json={"error": {"message": "Internal server error"}})
        )
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_version_list"](table="incident")
        result = decode_response(raw)

        assert result["status"] == "error"
        assert isinstance(result["error"], dict)
        assert "Internal server error" in result["error"]["message"]


# ---------------------------------------------------------------------------
# Dict reference field handling
# ---------------------------------------------------------------------------


class TestWorkflowDictReferenceFields:
    """Verify workflow tools handle dict reference fields from ServiceNow display_value responses."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_workflow_map_handles_dict_sys_ids(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """workflow_map handles document_key and activity_definition returned as dicts.

        sys_id is always a plain string in ServiceNow; reference fields like
        activity_definition and document_key are the ones that may come back
        as dicts when display_value query params are used.
        """
        dict_activity_def = {"display_value": "f45ee39efdcca5805d1e7e2eaa97f27b", "link": "https://test.service-now.com/api/..."}
        dict_doc_key = {"display_value": "a37b4556fc38ce6b2a3fd1521b1291bc", "link": "https://test.service-now.com/api/..."}

        respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version/789b9a03486e4434d8e435a024103c95").mock(
            return_value=httpx.Response(200, json={"result": {"sys_id": "789b9a03486e4434d8e435a024103c95", "name": "Test WF"}})
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_activity").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "a37b4556fc38ce6b2a3fd1521b1291bc",
                            "name": "Script Step",
                            "activity_definition": dict_activity_def,
                            "activity_definition.name": "Run Script",
                            "activity_definition.category": "Core",
                            "x": "10",
                            "y": "20",
                            "timeout": "",
                            "notes": "",
                            "out_of_date": "false",
                            "is_parent": "false",
                            "stage": "",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_transition").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_variable_value").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "47a920e21ce3b82c733a27e71b6e24b7",
                            "variable": "script",
                            "value": "gs.log('hello');",
                            "document_key": dict_doc_key,
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_map"](workflow_version_sys_id="789b9a03486e4434d8e435a024103c95")
        result = decode_response(raw)

        assert result["status"] == "success"
        activities = result["data"]["activities"]
        assert len(activities) == 1
        # Variables should be attached via resolved dict key matching
        assert len(activities[0]["variables"]) == 1

    @pytest.mark.asyncio()
    @respx.mock
    async def test_workflow_activity_detail_handles_dict_definition(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """workflow_activity_detail handles activity_definition returned as a dict."""
        dict_definition = {"display_value": "ac7de000000000000000000000000001", "link": "https://test.service-now.com/api/..."}

        # Phase 1: raw activity with dict reference
        respx.get(f"{BASE_URL}/api/now/table/wf_activity/2eac3cfb70af19b72304086d2d97c52d").mock(
            side_effect=[
                # First call: raw (display_values=False)
                httpx.Response(
                    200,
                    json={
                        "result": {
                            "sys_id": "2eac3cfb70af19b72304086d2d97c52d",
                            "name": "My Activity",
                            "activity_definition": dict_definition,
                        }
                    },
                ),
                # Second call: display (display_values=True)
                httpx.Response(
                    200,
                    json={
                        "result": {
                            "sys_id": "2eac3cfb70af19b72304086d2d97c52d",
                            "name": "My Activity",
                            "activity_definition": "Run Script",
                        }
                    },
                ),
            ]
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_variable_value").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_element_definition/ac7de000000000000000000000000001").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": "ac7de000000000000000000000000001", "name": "Run Script Definition"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_activity_detail"](activity_sys_id="2eac3cfb70af19b72304086d2d97c52d")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["definition"] is not None
        assert result["data"]["definition"]["name"] == "Run Script Definition"
