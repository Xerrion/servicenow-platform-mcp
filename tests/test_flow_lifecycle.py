"""Read-only lifecycle state and execution evidence."""

import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow import _register_and_get_tools

BASE = "https://test.service-now.com"
FLOW = "a" * 32


def status_header():
    return {
        "sys_id": FLOW,
        "type": "flow",
        "sys_scope": "global",
        "active": "false",
        "status": "draft",
        "master_snapshot": "b" * 32,
        "latest_snapshot": "c" * 32,
        "sys_updated_on": "2026-10-14 10:00:00",
        "sys_mod_count": "7",
    }


@pytest.mark.parametrize("state", ["COMPLETE", "WAITING", "UNRECOGNIZED"])
async def test_status_reads_exact_execution_context_without_starting_it(
    settings, state
):
    capture_routes()
    execution = "d" * 32
    snapshot = "e" * 32
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": status_header()}
    )
    http_mock.get(f"{BASE}/api/now/table/sys_flow_context/{execution}").respond(
        200,
        json={
            "result": {
                "sys_id": execution,
                "flow": FLOW,
                "snapshot": snapshot,
                "state": state,
                "sys_created_on": "2026-10-14 09:00:00",
                "sys_updated_on": "2026-10-14 10:00:00",
            },
        },
    )
    http_mock.get(f"{BASE}/api/now/table/sys_hub_snapshot/{snapshot}").respond(
        200,
        json={
            "result": {
                "sys_id": snapshot,
                "parent": FLOW,
                "sys_mod_count": "1",
                "sys_updated_on": "2026-10-14 10:00:00",
            },
        },
    )
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(
        await tools["flow"](action="status", sys_id=FLOW, execution_sys_id=execution)
    )
    assert result["status"] == "success", result
    data = result["data"]
    assert data["flow"]["active"] is False
    assert data["flow"]["draft"] is True
    assert data["flow"]["has_published_snapshot"] is True
    assert data["execution"]["state"] == state
    assert data["execution"]["snapshot_sys_id"] == snapshot
    assert data["execution"]["snapshot_owner_verified"] is True
    assert data["execution"]["snapshot_content_verified"] is False
    assert data["execution"]["version_verified"] is False
    assert data["complete"] is (state != "UNRECOGNIZED")
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_status_rejects_unrelated_execution_and_partial_active(settings):
    capture_routes()
    header = status_header()
    header.pop("active")
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": header}
    )
    http_mock.get(f"{BASE}/api/now/table/sys_flow_context/{'d' * 32}").respond(
        200,
        json={"result": {"sys_id": "d" * 32, "flow": "e" * 32, "state": "COMPLETE"}},
    )
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    result = decode_response(
        await tools["flow"](action="status", sys_id=FLOW, execution_sys_id="d" * 32)
    )
    assert result["data"]["complete"] is False
    assert result["data"]["flow"]["active"] is None
    assert result["data"]["execution"]["state"] is None


def capture_routes():
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": status_header()}
    )
