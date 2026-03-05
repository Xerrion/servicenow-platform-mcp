"""Tests for workflow introspection tools."""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from toon_format import decode as toon_decode

from servicenow_mcp.auth import BasicAuthProvider

BASE_URL = "https://test.service-now.com"


@pytest.fixture
def auth_provider(settings):
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings, auth_provider):
    """Helper: register workflow tools on a fresh MCP server and return tool map."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.workflow import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}


# ---------------------------------------------------------------------------
# Tool Registration
# ---------------------------------------------------------------------------


class TestWorkflowToolRegistration:
    """Verify all workflow tools register correctly."""

    def test_registers_all_five_tools(self, settings, auth_provider):
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

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_both_legacy_and_flow_contexts(self, settings, auth_provider):
        """Returns legacy workflow contexts and flow designer contexts."""
        respx.get(f"{BASE_URL}/api/now/table/wf_context").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "wfc001",
                            "name": "Incident Workflow",
                            "state": "Executing",
                            "started": "2026-02-20 09:00:00",
                            "ended": "",
                            "workflow_version": "wfv001",
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
                            "sys_id": "fc001",
                            "name": "Auto-assign Flow",
                            "state": "Completed",
                            "started": "2026-02-20 09:00:00",
                            "ended": "2026-02-20 09:00:05",
                            "flow_version": "fv001",
                            "source_table": "incident",
                            "source_record": "inc001",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_contexts"](record_sys_id="inc001")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert len(result["data"]["legacy_workflows"]) == 1
        assert len(result["data"]["flow_designer"]) == 1
        assert result["data"]["legacy_workflows"][0]["name"] == "Incident Workflow"
        assert result["data"]["flow_designer"][0]["name"] == "Auto-assign Flow"

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_results_for_both_engines(self, settings, auth_provider):
        """Returns empty lists when no contexts found."""
        respx.get(f"{BASE_URL}/api/now/table/wf_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_flow_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_contexts"](record_sys_id="inc999")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["legacy_workflows"] == []
        assert result["data"]["flow_designer"] == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_filters_by_state(self, settings, auth_provider):
        """Passes state filter through to the legacy query."""
        legacy_route = respx.get(f"{BASE_URL}/api/now/table/wf_context").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "wfc002",
                            "name": "Finished WF",
                            "state": "Finished",
                            "started": "2026-02-20 08:00:00",
                            "ended": "2026-02-20 08:05:00",
                            "workflow_version": "wfv002",
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
        raw = await tools["workflow_contexts"](record_sys_id="inc001", state="finished")
        result = toon_decode(raw)

        assert result["status"] == "success"
        # Verify state was included in the legacy encoded query
        request = legacy_route.calls[0].request
        qs = parse_qs(urlparse(str(request.url)).query)
        assert "state=finished" in qs["sysparm_query"][0]

    @pytest.mark.asyncio
    @respx.mock
    async def test_filters_by_table(self, settings, auth_provider):
        """Passes table filter through to the legacy query."""
        legacy_route = respx.get(f"{BASE_URL}/api/now/table/wf_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_flow_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_contexts"](record_sys_id="inc001", table="incident")
        result = toon_decode(raw)

        assert result["status"] == "success"
        # Verify table was included in the legacy encoded query
        request = legacy_route.calls[0].request
        qs = parse_qs(urlparse(str(request.url)).query)
        assert "table=incident" in qs["sysparm_query"][0]

    @pytest.mark.asyncio
    @respx.mock
    async def test_handles_server_error(self, settings, auth_provider):
        """Returns error envelope when legacy context query fails."""
        respx.get(f"{BASE_URL}/api/now/table/wf_context").mock(
            return_value=httpx.Response(500, json={"error": {"message": "Server error"}})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_flow_context").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_contexts"](record_sys_id="inc001")
        result = toon_decode(raw)

        assert result["status"] == "error"
        assert isinstance(result["error"], dict)
        assert "Server error" in result["error"]["message"]


# ---------------------------------------------------------------------------
# workflow_map
# ---------------------------------------------------------------------------


class TestWorkflowMap:
    """Tests for the workflow_map tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_full_map(self, settings, auth_provider):
        """Returns version, activities, and transitions."""
        respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version/wfv001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "wfv001",
                        "name": "Incident Workflow v2",
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
                            "sys_id": "act001",
                            "name": "Begin",
                            "activity_definition": "def001",
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
                            "sys_id": "act002",
                            "name": "Approval",
                            "activity_definition": "def002",
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
                            "sys_id": "tr001",
                            "from": "act001",
                            "from.name": "Begin",
                            "to": "act002",
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
                            "sys_id": "sv001",
                            "variable": "var_def_001",
                            "value": "some_script_body",
                            "document_key": "act002",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_map"](workflow_version_sys_id="wfv001")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["version"]["name"] == "Incident Workflow v2"
        assert len(result["data"]["activities"]) == 2
        assert len(result["data"]["transitions"]) == 1
        assert result["data"]["transitions"][0]["from.name"] == "Begin"
        # act001 has no variables, act002 has one
        assert result["data"]["activities"][0].get("variables") == []
        assert len(result["data"]["activities"][1]["variables"]) == 1
        assert result["data"]["activities"][1]["variables"][0]["value"] == "some_script_body"

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_activities_and_transitions(self, settings, auth_provider):
        """Returns version with empty activities and transitions lists."""
        respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version/wfv_empty").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "wfv_empty",
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
        raw = await tools["workflow_map"](workflow_version_sys_id="wfv_empty")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["activities"] == []
        assert result["data"]["transitions"] == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_activities_sorted_by_x_position(self, settings, auth_provider):
        """Activities are returned ordered by x position from the query."""
        respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version/wfv_sort").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": "wfv_sort", "name": "Sort Test WF"}},
            )
        )
        # Activities returned pre-sorted by x (as ServiceNow would return them)
        respx.get(f"{BASE_URL}/api/now/table/wf_activity").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {"sys_id": "a1", "name": "First", "x": "10", "y": "50"},
                        {"sys_id": "a2", "name": "Second", "x": "200", "y": "50"},
                        {"sys_id": "a3", "name": "Third", "x": "400", "y": "50"},
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
        raw = await tools["workflow_map"](workflow_version_sys_id="wfv_sort")
        result = toon_decode(raw)

        assert result["status"] == "success"
        names = [a["name"] for a in result["data"]["activities"]]
        assert names == ["First", "Second", "Third"]
        assert all(a["variables"] == [] for a in result["data"]["activities"])

    @pytest.mark.asyncio
    @respx.mock
    async def test_error_on_missing_version(self, settings, auth_provider):
        """Returns error when the workflow version is not found."""
        respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version/bad_id").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_activity").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_transition").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_map"](workflow_version_sys_id="bad_id")
        result = toon_decode(raw)

        assert result["status"] == "error"
        assert isinstance(result["error"], dict)
        assert "Not found" in result["error"]["message"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_map_groups_variables_by_activity(self, settings, auth_provider):
        """Variables are grouped correctly per activity in the map."""
        respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version/wfv_vars").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": "wfv_vars", "name": "Vars Test WF"}},
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/wf_activity").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {"sys_id": "a1", "name": "Script 1", "x": "10", "y": "50"},
                        {"sys_id": "a2", "name": "Script 2", "x": "200", "y": "50"},
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
                        {"sys_id": "sv1", "variable": "var1", "value": "script_a1", "document_key": "a1"},
                        {"sys_id": "sv2", "variable": "var2", "value": "script_a2", "document_key": "a2"},
                        {"sys_id": "sv3", "variable": "var3", "value": "condition_a1", "document_key": "a1"},
                    ]
                },
                headers={"X-Total-Count": "3"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_map"](workflow_version_sys_id="wfv_vars")
        result = toon_decode(raw)

        assert result["status"] == "success"
        activities = result["data"]["activities"]
        # a1 should have 2 variables, a2 should have 1
        a1 = next(a for a in activities if a["sys_id"] == "a1")
        a2 = next(a for a in activities if a["sys_id"] == "a2")
        assert len(a1["variables"]) == 2
        assert len(a2["variables"]) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_map_empty_activities_skips_variable_fetch(self, settings, auth_provider):
        """No sys_variable_value call when there are no activities."""
        respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version/wfv_none").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": "wfv_none", "name": "No Activities WF"}},
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
        raw = await tools["workflow_map"](workflow_version_sys_id="wfv_none")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["activities"] == []


# ---------------------------------------------------------------------------
# workflow_status
# ---------------------------------------------------------------------------


class TestWorkflowStatus:
    """Tests for the workflow_status tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_context_with_executing_and_history(self, settings, auth_provider):
        """Returns context record alongside executing and history lists."""
        respx.get(f"{BASE_URL}/api/now/table/wf_context/ctx001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "ctx001",
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
                            "sys_id": "ex001",
                            "activity": "act001",
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
                            "sys_id": "hist001",
                            "activity": "act000",
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
        raw = await tools["workflow_status"](context_sys_id="ctx001")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["context"]["state"] == "Executing"
        assert len(result["data"]["executing"]) == 1
        assert len(result["data"]["history"]) == 1
        assert result["data"]["executing"][0]["activity.name"] == "Approval"
        assert result["data"]["history"][0]["result"] == "success"

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_executing_all_completed(self, settings, auth_provider):
        """Returns empty executing list when all activities have finished."""
        respx.get(f"{BASE_URL}/api/now/table/wf_context/ctx_done").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "ctx_done",
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
                            "sys_id": "h1",
                            "activity": "a1",
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
        raw = await tools["workflow_status"](context_sys_id="ctx_done")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["executing"] == []
        assert len(result["data"]["history"]) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_multiple_executing_activities(self, settings, auth_provider):
        """Returns multiple currently-executing activities."""
        respx.get(f"{BASE_URL}/api/now/table/wf_context/ctx_multi").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "ctx_multi",
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
                            "sys_id": "ex1",
                            "activity": "a1",
                            "activity.name": "Task A",
                            "state": "executing",
                            "started": "2026-02-20 09:00:00",
                            "fault_description": "",
                        },
                        {
                            "sys_id": "ex2",
                            "activity": "a2",
                            "activity.name": "Task B",
                            "state": "executing",
                            "started": "2026-02-20 09:00:00",
                            "fault_description": "",
                        },
                        {
                            "sys_id": "ex3",
                            "activity": "a3",
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
        raw = await tools["workflow_status"](context_sys_id="ctx_multi")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert len(result["data"]["executing"]) == 3

    @pytest.mark.asyncio
    @respx.mock
    async def test_history_with_faults(self, settings, auth_provider):
        """Returns history entries with populated fault_description."""
        respx.get(f"{BASE_URL}/api/now/table/wf_context/ctx_fault").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "ctx_fault",
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
                            "sys_id": "h1",
                            "activity": "a1",
                            "activity.name": "Run Script",
                            "state": "faulted",
                            "started": "2026-02-20 09:00:00",
                            "ended": "2026-02-20 09:00:02",
                            "result": "error",
                            "fault_description": "NullPointerException at line 42",
                            "activity_index": "1",
                        },
                        {
                            "sys_id": "h2",
                            "activity": "a2",
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
        raw = await tools["workflow_status"](context_sys_id="ctx_fault")
        result = toon_decode(raw)

        assert result["status"] == "success"
        history = result["data"]["history"]
        assert len(history) == 2
        assert history[0]["fault_description"] == "NullPointerException at line 42"
        assert history[1]["fault_description"] == "Invalid email address"

    @pytest.mark.asyncio
    @respx.mock
    async def test_error_on_missing_context(self, settings, auth_provider):
        """Returns error envelope when wf_context is not found."""
        respx.get(f"{BASE_URL}/api/now/table/wf_context/ctx404").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Record not found"}})
        )
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_status"](context_sys_id="ctx404")
        result = toon_decode(raw)

        assert result["status"] == "error"
        assert isinstance(result["error"], dict)
        assert "Record not found" in result["error"]["message"]


# ---------------------------------------------------------------------------
# workflow_activity_detail
# ---------------------------------------------------------------------------


class TestWorkflowActivityDetail:
    """Tests for the workflow_activity_detail tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_activity_with_linked_definition(self, settings, auth_provider):
        """Returns activity display values alongside the element definition."""
        # Phase 1: raw activity (display_values=False) to get the definition sys_id
        respx.get(f"{BASE_URL}/api/now/table/wf_activity/act001").mock(
            side_effect=lambda request: httpx.Response(
                200,
                json={
                    "result": (
                        {
                            "sys_id": "act001",
                            "name": "Approval",
                            "activity_definition": "def001",
                            "x": "200",
                            "y": "100",
                        }
                        if "sysparm_display_value" not in str(request.url)
                        else {
                            "sys_id": "act001",
                            "name": "Approval",
                            "activity_definition": "Approval - User",
                            "x": "200",
                            "y": "100",
                        }
                    )
                },
            )
        )
        # Phase 2: element definition with display values
        respx.get(f"{BASE_URL}/api/now/table/wf_element_definition/def001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "def001",
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
                            "sys_id": "var001",
                            "variable": "Script",
                            "value": "current.state = 2;",
                            "document_key": "act001",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_activity_detail"](activity_sys_id="act001")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["activity"]["sys_id"] == "act001"
        assert result["data"]["definition"]["name"] == "Approval - User"
        assert result["data"]["definition"]["category"] == "Approvals"
        assert len(result["data"]["variables"]) == 1
        assert result["data"]["variables"][0]["value"] == "current.state = 2;"

    @pytest.mark.asyncio
    @respx.mock
    async def test_activity_with_no_definition(self, settings, auth_provider):
        """Returns definition as None when activity has no linked definition."""
        # Phase 1: raw activity with empty activity_definition
        respx.get(f"{BASE_URL}/api/now/table/wf_activity/act_nodef").mock(
            side_effect=lambda request: httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "act_nodef",
                        "name": "Custom Activity",
                        "activity_definition": "",
                        "x": "100",
                        "y": "50",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_variable_value").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_activity_detail"](activity_sys_id="act_nodef")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["activity"]["sys_id"] == "act_nodef"
        assert result["data"]["definition"] is None
        assert result["data"]["variables"] == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_error_on_missing_activity(self, settings, auth_provider):
        """Returns error when the activity record is not found."""
        respx.get(f"{BASE_URL}/api/now/table/wf_activity/bad_id").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_activity_detail"](activity_sys_id="bad_id")
        result = toon_decode(raw)

        assert result["status"] == "error"
        assert isinstance(result["error"], dict)
        assert "Not found" in result["error"]["message"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_activity_includes_multiple_variables(self, settings, auth_provider):
        """Returns multiple configured variables for an activity."""
        respx.get(f"{BASE_URL}/api/now/table/wf_activity/act_vars").mock(
            side_effect=lambda request: httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "act_vars",
                        "name": "Run Script",
                        "activity_definition": "def_script",
                        "x": "100",
                        "y": "50",
                    }
                },
            )
        )
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
                            "sys_id": "sv1",
                            "variable": "Script",
                            "value": "gs.log('hello');",
                            "document_key": "act_vars",
                        },
                        {
                            "sys_id": "sv2",
                            "variable": "Condition",
                            "value": "current.active == true",
                            "document_key": "act_vars",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_activity_detail"](activity_sys_id="act_vars")
        result = toon_decode(raw)

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

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_versions_for_table(self, settings, auth_provider):
        """Returns workflow versions defined for a specific table."""
        respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "wfv001",
                            "name": "Incident Workflow v1",
                            "table": "incident",
                            "description": "Handles incident lifecycle",
                            "active": "true",
                            "published": "true",
                            "checked_out": "",
                            "checked_out_by": "",
                            "workflow": "wf001",
                        },
                        {
                            "sys_id": "wfv002",
                            "name": "Incident Workflow v2",
                            "table": "incident",
                            "description": "Updated incident lifecycle",
                            "active": "true",
                            "published": "true",
                            "checked_out": "",
                            "checked_out_by": "",
                            "workflow": "wf001",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_version_list"](table="incident")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert len(result["data"]["versions"]) == 2
        assert result["data"]["versions"][0]["table"] == "incident"

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_result(self, settings, auth_provider):
        """Returns empty versions list when none found."""
        respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_version_list"](table="change_request")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["versions"] == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_active_only_false_includes_inactive(self, settings, auth_provider):
        """Omits the active filter when active_only is False."""
        version_route = respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "wfv_inactive",
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
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert len(result["data"]["versions"]) == 1
        # Verify active=true was NOT in the encoded query
        request = version_route.calls[0].request
        qs = parse_qs(urlparse(str(request.url)).query)
        assert "active=true" not in qs["sysparm_query"][0]

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_versions_with_display_values(self, settings, auth_provider):
        """Display values are requested for version records."""
        version_route = respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "wfv003",
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
        result = toon_decode(raw)

        assert result["status"] == "success"
        # Verify display_value=true was in the query params
        request = version_route.calls[0].request
        assert "sysparm_display_value=true" in str(request.url)

    @pytest.mark.asyncio
    @respx.mock
    async def test_handles_server_error(self, settings, auth_provider):
        """Returns error envelope on server error."""
        respx.get(f"{BASE_URL}/api/now/table/wf_workflow_version").mock(
            return_value=httpx.Response(500, json={"error": {"message": "Internal server error"}})
        )
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["workflow_version_list"](table="incident")
        result = toon_decode(raw)

        assert result["status"] == "error"
        assert isinstance(result["error"], dict)
        assert "Internal server error" in result["error"]["message"]
