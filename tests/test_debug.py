"""Tests for debug/trace tools."""

import json

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider

BASE_URL = "https://test.service-now.com"


@pytest.fixture
def auth_provider(settings):
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings, auth_provider):
    """Helper: register debug tools on a fresh MCP server and return tool map."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.debug import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}


class TestDebugTrace:
    """Tests for the debug_trace tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_merged_timeline(self, settings, auth_provider):
        """Returns merged timeline from sys_audit, syslog, sys_journal_field."""
        # Mock sys_audit
        respx.get(f"{BASE_URL}/api/now/table/sys_audit").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "a1",
                            "user": "admin",
                            "fieldname": "state",
                            "oldvalue": "1",
                            "newvalue": "2",
                            "sys_created_on": "2026-02-20 09:00:00",
                            "tablename": "incident",
                            "documentkey": "inc001",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        # Mock syslog
        respx.get(f"{BASE_URL}/api/now/table/syslog").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "l1",
                            "message": "BR: Auto-assign triggered",
                            "source": "sys_script",
                            "level": "0",
                            "sys_created_on": "2026-02-20 09:00:01",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        # Mock sys_journal_field
        respx.get(f"{BASE_URL}/api/now/table/sys_journal_field").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "j1",
                            "element": "comments",
                            "value": "Working on this issue",
                            "sys_created_on": "2026-02-20 09:01:00",
                            "sys_created_by": "admin",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["debug_trace"](record_sys_id="inc001", table="incident", minutes=60)
        result = json.loads(raw)

        assert result["status"] == "success"
        assert len(result["data"]["timeline"]) == 3
        # Timeline should be sorted by timestamp
        timestamps = [e["timestamp"] for e in result["data"]["timeline"]]
        assert timestamps == sorted(timestamps)

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_trace(self, settings, auth_provider):
        """Returns empty timeline when no events found."""
        respx.get(f"{BASE_URL}/api/now/table/sys_audit").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/syslog").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_journal_field").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["debug_trace"](record_sys_id="inc999", table="incident")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["timeline"] == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_filters_by_minutes(self, settings, auth_provider):
        """Queries include gs.minutesAgoStart time filter."""
        respx.get(f"{BASE_URL}/api/now/table/sys_audit").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/syslog").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_journal_field").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["debug_trace"](record_sys_id="inc001", table="incident", minutes=30)
        result = json.loads(raw)
        assert result["status"] == "success"


class TestDebugFlowExecution:
    """Tests for the debug_flow_execution tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_flow_steps(self, settings, auth_provider):
        """Returns flow execution steps from sys_flow_context and sys_flow_log."""
        # Mock flow context
        respx.get(f"{BASE_URL}/api/now/table/sys_flow_context/ctx001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "ctx001",
                        "name": "Auto-assign flow",
                        "state": "Completed",
                        "started": "2026-02-20 08:00:00",
                        "ended": "2026-02-20 08:00:05",
                    }
                },
            )
        )
        # Mock flow log entries
        respx.get(f"{BASE_URL}/api/now/table/sys_flow_log").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "fl1",
                            "step_label": "Lookup Record",
                            "state": "Completed",
                            "sys_created_on": "2026-02-20 08:00:01",
                            "output_data": '{"record": "inc001"}',
                            "error_message": "",
                        },
                        {
                            "sys_id": "fl2",
                            "step_label": "Update Record",
                            "state": "Completed",
                            "sys_created_on": "2026-02-20 08:00:03",
                            "output_data": "{}",
                            "error_message": "",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["debug_flow_execution"](context_id="ctx001")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["context"]["name"] == "Auto-assign flow"
        assert len(result["data"]["steps"]) == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_handles_flow_with_errors(self, settings, auth_provider):
        """Returns error info when flow steps have errors."""
        respx.get(f"{BASE_URL}/api/now/table/sys_flow_context/ctx002").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "ctx002",
                        "name": "Failed flow",
                        "state": "Error",
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
                            "sys_id": "fl1",
                            "step_label": "Script Step",
                            "state": "Error",
                            "sys_created_on": "2026-02-20 08:00:01",
                            "output_data": "",
                            "error_message": "NullPointerException at line 5",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["debug_flow_execution"](context_id="ctx002")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["steps"][0]["error_message"] == "NullPointerException at line 5"


class TestDebugEmailTrace:
    """Tests for the debug_email_trace tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_email_chain(self, settings, auth_provider):
        """Reconstructs email chain for a record."""
        respx.get(f"{BASE_URL}/api/now/table/sys_email").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "e1",
                            "type": "received",
                            "subject": "RE: INC0001",
                            "recipients": "admin@example.com",
                            "sys_created_on": "2026-02-20 10:00:00",
                            "direct": "true",
                            "body_text": "Thanks for the update",
                        },
                        {
                            "sys_id": "e2",
                            "type": "send",
                            "subject": "INC0001 created",
                            "recipients": "user@example.com",
                            "sys_created_on": "2026-02-20 09:00:00",
                            "direct": "true",
                            "body_text": "Incident created",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["debug_email_trace"](record_sys_id="inc001")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert len(result["data"]["emails"]) == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_handles_no_emails(self, settings, auth_provider):
        """Returns empty list when no emails found."""
        respx.get(f"{BASE_URL}/api/now/table/sys_email").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["debug_email_trace"](record_sys_id="inc999")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["emails"] == []


class TestDebugIntegrationHealth:
    """Tests for the debug_integration_health tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_ecc_queue_errors(self, settings, auth_provider):
        """Returns ECC queue error summary."""
        respx.get(f"{BASE_URL}/api/now/table/ecc_queue").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "ecc1",
                            "name": "SOAP",
                            "queue": "input",
                            "state": "error",
                            "error_string": "Connection refused",
                            "sys_created_on": "2026-02-20 08:00:00",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["debug_integration_health"](kind="ecc_queue")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["kind"] == "ecc_queue"
        assert len(result["data"]["errors"]) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_rest_message_errors(self, settings, auth_provider):
        """Returns REST message error summary."""
        respx.get(f"{BASE_URL}/api/now/table/sys_rest_transaction").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "rt1",
                            "rest_message": "ServiceNow API",
                            "http_method": "POST",
                            "http_status": "500",
                            "endpoint": "https://api.example.com/data",
                            "sys_created_on": "2026-02-20 08:00:00",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["debug_integration_health"](kind="rest_message")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["kind"] == "rest_message"
        assert len(result["data"]["errors"]) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_filters_by_hours(self, settings, auth_provider):
        """Queries include gs.hoursAgoStart time filter."""
        respx.get(f"{BASE_URL}/api/now/table/ecc_queue").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["debug_integration_health"](kind="ecc_queue", hours=12)
        result = json.loads(raw)
        assert result["status"] == "success"


class TestDebugImportsetRun:
    """Tests for the debug_importset_run tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_import_set_results(self, settings, auth_provider):
        """Returns import set run results."""
        # Mock import set header
        respx.get(f"{BASE_URL}/api/now/table/sys_import_set/imp001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "imp001",
                        "table_name": "u_staging",
                        "state": "loaded",
                        "mode": "asynchronous",
                        "sys_created_on": "2026-02-20 07:00:00",
                    }
                },
            )
        )
        # Mock import set rows
        respx.get(f"{BASE_URL}/api/now/table/sys_import_set_row").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "r1",
                            "sys_import_state": "inserted",
                            "sys_target_sys_id": "t1",
                            "sys_import_state_comment": "",
                        },
                        {
                            "sys_id": "r2",
                            "sys_import_state": "error",
                            "sys_target_sys_id": "",
                            "sys_import_state_comment": "Transform error: field mapping failed",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["debug_importset_run"](import_set_sys_id="imp001")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["import_set"]["sys_id"] == "imp001"
        assert result["data"]["summary"]["total"] == 2
        assert result["data"]["summary"]["inserted"] == 1
        assert result["data"]["summary"]["error"] == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_handles_empty_import_set(self, settings, auth_provider):
        """Handles import set with no rows."""
        respx.get(f"{BASE_URL}/api/now/table/sys_import_set/imp002").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "imp002",
                        "table_name": "u_staging",
                        "state": "loaded",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_import_set_row").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["debug_importset_run"](import_set_sys_id="imp002")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["summary"]["total"] == 0


class TestDebugFieldMutationStory:
    """Tests for the debug_field_mutation_story tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_field_history(self, settings, auth_provider):
        """Returns chronological field mutation history."""
        respx.get(f"{BASE_URL}/api/now/table/sys_audit").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "a1",
                            "user": "admin",
                            "fieldname": "state",
                            "oldvalue": "1",
                            "newvalue": "2",
                            "sys_created_on": "2026-02-19 10:00:00",
                        },
                        {
                            "sys_id": "a2",
                            "user": "jdoe",
                            "fieldname": "state",
                            "oldvalue": "2",
                            "newvalue": "6",
                            "sys_created_on": "2026-02-20 10:00:00",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["debug_field_mutation_story"](table="incident", sys_id="inc001", field="state")
        result = json.loads(raw)

        assert result["status"] == "success"
        mutations = result["data"]["mutations"]
        assert len(mutations) == 2
        assert mutations[0]["old_value"] == "1"
        assert mutations[1]["new_value"] == "6"

    @pytest.mark.asyncio
    @respx.mock
    async def test_handles_no_mutations(self, settings, auth_provider):
        """Returns empty when no mutations found for the field."""
        respx.get(f"{BASE_URL}/api/now/table/sys_audit").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["debug_field_mutation_story"](table="incident", sys_id="inc001", field="state")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["mutations"] == []
