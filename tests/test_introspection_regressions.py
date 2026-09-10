"""HTTP-level regressions for introspection failures seen during agent analysis."""

import httpx
import pytest
import respx

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.errors import ServerError


BASE_URL = "https://test.service-now.com"


@respx.mock
async def test_v1_table_lookup_joins_remote_record_trigger(settings: Settings) -> None:
    """V1 trigger instances have remote_sys_id, not a table column."""
    remote_id = "a" * 32
    respx.get(f"{BASE_URL}/api/now/table/sys_flow_record_trigger").mock(
        return_value=httpx.Response(200, json={"result": [{"sys_id": remote_id, "table": "sc_task"}]})
    )
    route = respx.get(f"{BASE_URL}/api/now/table/sys_hub_trigger_instance").mock(
        return_value=httpx.Response(200, json={"result": []})
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        assert await client.list_v1_triggers_by_table("sc_task") == []
    assert route.calls.last.request.url.params["sysparm_query"] == f"remote_sys_idIN{remote_id}"


@respx.mock
async def test_v1_table_lookup_without_record_triggers_does_not_scan(
    settings: Settings,
) -> None:
    """An empty join must not become an unfiltered trigger scan."""
    respx.get(f"{BASE_URL}/api/now/table/sys_flow_record_trigger").mock(
        return_value=httpx.Response(200, json={"result": []})
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        assert await client.list_v1_triggers_by_table("sc_task") == []
    assert len(respx.calls) == 1


@pytest.mark.parametrize("body", [b"", b"<html>private response</html>"])
@respx.mock
async def test_non_json_flow_response_has_safe_context(settings: Settings, body: bytes) -> None:
    """Invalid HTTP payloads name the endpoint, not the body or query string."""
    path = "/api/now/table/sys_hub_action_instance_v2"
    respx.get(f"{BASE_URL}{path}").mock(
        return_value=httpx.Response(200, content=body, headers={"Content-Type": "text/html"})
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(ServerError, match="Invalid JSON response") as exc:
            await client.list_action_instances_v2("a" * 32)
    message = str(exc.value)
    assert path in message
    assert "200" in message
    assert "private response" not in message
    assert "sysparm_query" not in message


@respx.mock
async def test_filtered_triggers_use_version_specific_relations(
    settings: Settings,
) -> None:
    """Table filters join remote records rather than nonexistent trigger columns."""
    remote_id = "a" * 32
    respx.get(f"{BASE_URL}/api/now/table/sys_flow_record_trigger").mock(
        return_value=httpx.Response(200, json={"result": [{"sys_id": {"value": remote_id}}]})
    )
    v1 = respx.get(f"{BASE_URL}/api/now/table/sys_hub_trigger_instance").mock(
        return_value=httpx.Response(200, json={"result": []})
    )
    v2 = respx.get(f"{BASE_URL}/api/now/table/sys_hub_trigger_instance_v2").mock(
        return_value=httpx.Response(200, json={"result": []})
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        assert await client.list_triggers_filtered(table="sc_task", trigger_type="record_update") == {
            "v1": [],
            "v2": [],
        }
    assert v1.calls.last.request.url.params["sysparm_query"] == (
        f"trigger_type=record_update^remote_sys_idIN{remote_id}"
    )
    assert v2.calls.last.request.url.params["sysparm_query"] == (
        f"type=record_update^remote_trigger_idIN{remote_id}^ORsys_idIN{remote_id}"
    )


@respx.mock
async def test_filtered_triggers_empty_join_does_not_scan(settings: Settings) -> None:
    """An empty record-trigger join cannot fan out into unfiltered reads."""
    respx.get(f"{BASE_URL}/api/now/table/sys_flow_record_trigger").mock(
        return_value=httpx.Response(200, json={"result": []})
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        assert await client.list_triggers_filtered(table="sc_task") == {
            "v1": [],
            "v2": [],
        }
    assert len(respx.calls) == 1
