"""Tests for debug/trace tools."""

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
    """Helper: register debug tools on a fresh MCP server and return tool map."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.debug import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return get_tool_functions(mcp)


class TestDebugTrace:
    """Tests for the debug_trace tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_merged_timeline(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns merged timeline from sys_audit, syslog, sys_journal_field."""
        # Mock sys_audit
        respx.get(f"{BASE_URL}/api/now/table/sys_audit").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "f29bc91bbdab169fc0c0a326965953d1",
                            "user": "admin",
                            "fieldname": "state",
                            "oldvalue": "1",
                            "newvalue": "2",
                            "sys_created_on": "2026-02-20 09:00:00",
                            "tablename": "incident",
                            "documentkey": "6d55028a7049dbf2f4275991d6fc81cf",
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
                            "sys_id": "39a89dd158c8e9747943b00f84be79fc",
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
                            "sys_id": "67921242516c48ac712807f98c98ee13",
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
        raw = await tools["debug_trace"](record_sys_id="6d55028a7049dbf2f4275991d6fc81cf", table="incident", minutes=60)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert len(result["data"]["timeline"]) == 3
        # Timeline should be sorted by timestamp
        timestamps = [e["timestamp"] for e in result["data"]["timeline"]]
        assert timestamps == sorted(timestamps)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_empty_trace(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
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
        raw = await tools["debug_trace"](record_sys_id="2edef9aa2e99060fd11a80ae6eed85b5", table="incident")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["timeline"] == []

    @pytest.mark.asyncio()
    @respx.mock
    async def test_filters_by_minutes(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
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
        raw = await tools["debug_trace"](record_sys_id="6d55028a7049dbf2f4275991d6fc81cf", table="incident", minutes=30)
        result = decode_response(raw)
        assert result["status"] == "success"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_caret_in_sys_id_single_sanitized(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Values with carets are single-sanitized (^ → ^^), not double (^ → ^^^^)."""
        audit_route = respx.get(f"{BASE_URL}/api/now/table/sys_audit").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/syslog").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_journal_field").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["debug_trace"](record_sys_id="abc^def", table="incident", minutes=60)
        result = decode_response(raw)

        assert result["status"] == "success"
        # Inspect the audit query: should contain abc^^def (single sanitization)
        assert audit_route.calls.last is not None
        request = audit_route.calls.last.request
        parsed = urlparse(str(request.url))
        qs = parse_qs(parsed.query)
        query_str = qs["sysparm_query"][0]
        assert "abc^^def" in query_str
        assert "abc^^^^def" not in query_str


class TestDebugFlowExecution:
    """Tests for the debug_flow_execution tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_flow_steps(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns flow execution steps from sys_flow_context and sys_flow_log."""
        # Mock flow context
        respx.get(f"{BASE_URL}/api/now/table/sys_flow_context/18dbe9bd70e88bd7d141d13c8a46e7d7").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "18dbe9bd70e88bd7d141d13c8a46e7d7",
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
                            "sys_id": "f8e82113421aa92b5fdb30a353b6e82c",
                            "step_label": "Lookup Record",
                            "state": "Completed",
                            "sys_created_on": "2026-02-20 08:00:01",
                            "output_data": '{"record": "6d55028a7049dbf2f4275991d6fc81cf"}',
                            "error_message": "",
                        },
                        {
                            "sys_id": "228c49472381812a668368a02dc4e3e0",
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
        raw = await tools["debug_flow_execution"](context_id="18dbe9bd70e88bd7d141d13c8a46e7d7")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["context"]["name"] == "Auto-assign flow"
        assert len(result["data"]["steps"]) == 2

    @pytest.mark.asyncio()
    @respx.mock
    async def test_handles_flow_with_errors(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error info when flow steps have errors."""
        respx.get(f"{BASE_URL}/api/now/table/sys_flow_context/ffff7ed3a8c7ffe871d910c0eb40322e").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "ffff7ed3a8c7ffe871d910c0eb40322e",
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
                            "sys_id": "f8e82113421aa92b5fdb30a353b6e82c",
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
        raw = await tools["debug_flow_execution"](context_id="ffff7ed3a8c7ffe871d910c0eb40322e")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["steps"][0]["error_message"] == "NullPointerException at line 5"


class TestDebugEmailTrace:
    """Tests for the debug_email_trace tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_email_chain(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Reconstructs email chain for a record."""
        respx.get(f"{BASE_URL}/api/now/table/sys_email").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "84896d3e067884621c0f54334b8d8409",
                            "type": "received",
                            "subject": "RE: INC0001",
                            "recipients": "admin@example.com",
                            "sys_created_on": "2026-02-20 10:00:00",
                            "direct": "true",
                            "body_text": "Thanks for the update",
                        },
                        {
                            "sys_id": "dc1885915a8902bc29de6d834477a26d",
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
        raw = await tools["debug_email_trace"](record_sys_id="6d55028a7049dbf2f4275991d6fc81cf")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert len(result["data"]["emails"]) == 2

    @pytest.mark.asyncio()
    @respx.mock
    async def test_handles_no_emails(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns empty list when no emails found."""
        respx.get(f"{BASE_URL}/api/now/table/sys_email").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["debug_email_trace"](record_sys_id="2edef9aa2e99060fd11a80ae6eed85b5")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["emails"] == []


class TestDebugIntegrationHealth:
    """Tests for the debug_integration_health tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_ecc_queue_errors(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns ECC queue error summary."""
        respx.get(f"{BASE_URL}/api/now/table/ecc_queue").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "c104970cbb6160c996f3cf0d5e7c1955",
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
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["kind"] == "ecc_queue"
        assert len(result["data"]["errors"]) == 1

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_rest_message_errors(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns REST message error summary."""
        respx.get(f"{BASE_URL}/api/now/table/sys_rest_transaction").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "50782307d71bd69a8c3a269bf7346646",
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
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["kind"] == "rest_message"
        assert len(result["data"]["errors"]) == 1

    @pytest.mark.asyncio()
    @respx.mock
    async def test_filters_by_hours(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Queries include gs.hoursAgoStart time filter."""
        respx.get(f"{BASE_URL}/api/now/table/ecc_queue").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["debug_integration_health"](kind="ecc_queue", hours=12)
        result = decode_response(raw)
        assert result["status"] == "success"


class TestDebugImportsetRun:
    """Tests for the debug_importset_run tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_import_set_results(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns import set run results."""
        # Mock import set header
        respx.get(f"{BASE_URL}/api/now/table/sys_import_set/3c8ef400f0d355f096cee5204dbd2819").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "3c8ef400f0d355f096cee5204dbd2819",
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
                            "sys_id": "5573e39b6600496d40f493d00ec76584",
                            "sys_import_state": "inserted",
                            "sys_target_sys_id": "e5353879bd69bfddcb465dad176ff52d",
                            "sys_import_state_comment": "",
                        },
                        {
                            "sys_id": "a50126cc2d6c726de0ca203c3b659f65",
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
        raw = await tools["debug_importset_run"](import_set_sys_id="3c8ef400f0d355f096cee5204dbd2819")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["import_set"]["sys_id"] == "3c8ef400f0d355f096cee5204dbd2819"
        assert result["data"]["summary"]["total"] == 2
        assert result["data"]["summary"]["inserted"] == 1
        assert result["data"]["summary"]["error"] == 1

    @pytest.mark.asyncio()
    @respx.mock
    async def test_handles_empty_import_set(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Handles import set with no rows."""
        respx.get(f"{BASE_URL}/api/now/table/sys_import_set/dfff4e2300bbee9303bb3413aa5d6933").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "dfff4e2300bbee9303bb3413aa5d6933",
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
        raw = await tools["debug_importset_run"](import_set_sys_id="dfff4e2300bbee9303bb3413aa5d6933")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["summary"]["total"] == 0


class TestDebugFieldMutationStory:
    """Tests for the debug_field_mutation_story tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_field_history(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns chronological field mutation history."""
        respx.get(f"{BASE_URL}/api/now/table/sys_audit").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "f29bc91bbdab169fc0c0a326965953d1",
                            "user": "admin",
                            "fieldname": "state",
                            "oldvalue": "1",
                            "newvalue": "2",
                            "sys_created_on": "2026-02-19 10:00:00",
                        },
                        {
                            "sys_id": "b9f85daa6f83cf02ce5c31913d1f64d3",
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
        raw = await tools["debug_field_mutation_story"](
            table="incident", sys_id="6d55028a7049dbf2f4275991d6fc81cf", field="state"
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        mutations = result["data"]["mutations"]
        assert len(mutations) == 2
        assert mutations[0]["old_value"] == "1"
        assert mutations[1]["new_value"] == "6"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_handles_no_mutations(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns empty when no mutations found for the field."""
        respx.get(f"{BASE_URL}/api/now/table/sys_audit").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["debug_field_mutation_story"](
            table="incident", sys_id="6d55028a7049dbf2f4275991d6fc81cf", field="state"
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["mutations"] == []


class TestDebugIntegrationHealthValidation:
    """Tests for debug_integration_health input validation."""

    @pytest.mark.asyncio()
    async def test_invalid_kind_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Unknown kind value is rejected before any HTTP call is made."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["debug_integration_health"](kind="invalid_kind")
        result = decode_response(raw)

        assert result["status"] == "error"
        error_msg = result["error"]["message"] if isinstance(result["error"], dict) else result["error"]
        assert "Unknown kind" in error_msg
