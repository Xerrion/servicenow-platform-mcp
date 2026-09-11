"""HTTP regressions for bounded Flow Designer joins and completeness reporting."""

import httpx
import pytest
import respx
from mcp.server import MCPServer

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.errors import ServerError
from servicenow_mcp.tools.flow import register_tools
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com/api/now/table"


@pytest.mark.parametrize(
    "method",
    [
        "list_triggers_filtered",
        "get_flows_bulk",
        "list_v2_triggers_by_remote_ids",
        "list_v1_triggers_by_table",
    ],
)
@respx.mock
async def test_large_lookup_batches_every_id(settings: Settings, method: str) -> None:
    """Every ID is searched without constructing an oversized HTTP query."""
    ids = [f"{index:032x}" for index in range(1000)]
    respx.get(f"{BASE_URL}/sys_flow_record_trigger").respond(
        200,
        json={"result": [{"sys_id": value} for value in ids]},
        headers={"X-Total-Count": "1000"},
    )
    routes = {
        table: respx.get(f"{BASE_URL}/{table}").respond(200, json={"result": []})
        for table in (
            "sys_hub_trigger_instance",
            "sys_hub_trigger_instance_v2",
            "sys_hub_flow",
        )
    }
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        if method == "list_triggers_filtered":
            await client.list_triggers_filtered(table="sc_task", trigger_type="record_update", active="true")
        elif method == "list_v1_triggers_by_table":
            await client.list_v1_triggers_by_table("sc_task")
        else:
            await getattr(client, method)(ids)
    called_routes = [route for route in routes.values() if route.called]
    assert called_routes
    for route in called_routes:
        queries = [call.request.url.params["sysparm_query"] for call in route.calls]
        assert len(queries) > 1
        assert all(len(call.request.url.query) < 8000 for call in route.calls)
        assert all(any(value in query for query in queries) for value in ids)
        if method == "list_triggers_filtered":
            assert all("record_update" in query and "active=true" in query for query in queries)


@pytest.mark.parametrize("total", ["1001", None, "invalid", "-1", "0"])
@respx.mock
async def test_empty_join_reports_incomplete_source(settings: Settings, total: str | None) -> None:
    """Zero matches from a capped source page cannot imply an exhaustive search."""
    respx.get(f"{BASE_URL}/sys_flow_record_trigger").respond(
        200,
        json={"result": [{"sys_id": f"{index:032x}"} for index in range(1000)]},
        headers={"X-Total-Count": total} if total else {},
    )
    for table in ("sys_hub_trigger_instance", "sys_hub_trigger_instance_v2"):
        respx.get(f"{BASE_URL}/{table}").respond(200, json={"result": []})
    mcp = MCPServer("test")
    register_tools(mcp, settings, OAuthPKCEProvider(settings))
    result = decode_response(await get_tool_functions(mcp)["flow"](action="list_triggers", table="sc_task"))
    assert result["status"] == "success"
    assert result["data"]["triggers"] == []
    assert result["data"]["is_complete"] is False
    assert result["warnings"]
    source = result["selection"]["truncation"]["sys_flow_record_trigger"]
    assert source["returned"] == 1000
    assert source["total"] == (1001 if total == "1001" else None)
    assert "offset=1000" in source["continuation"]
    assert "table=sc_task" in source["continuation"]


@respx.mock
async def test_known_total_reveals_server_short_page(settings: Settings) -> None:
    """A server returning fewer rows than requested can still truncate a source."""
    respx.get(f"{BASE_URL}/sys_flow_record_trigger").respond(200, json={"result": []}, headers={"X-Total-Count": "5"})
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await client.list_triggers_filtered(table="sc_task")
    assert result["v1"] == result["v2"] == []
    assert result["truncation"]["sys_flow_record_trigger"]["total"] == 5


@respx.mock
async def test_header_batches_deduplicate_and_reach_later_ids(
    settings: Settings,
) -> None:
    """Snapshot aliases across batches resolve once without skipping later headers."""
    ids = [f"{index:032x}" for index in range(101)]
    shared = {"sys_id": {"value": "f" * 32}}
    later = {"sys_id": {"value": "e" * 32}}

    def respond(request: httpx.Request) -> httpx.Response:
        rows = [shared, later] if ids[-1] in request.url.params["sysparm_query"] else [shared]
        return httpx.Response(200, json={"result": rows})

    route = respx.get(f"{BASE_URL}/sys_hub_flow").mock(side_effect=respond)
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        rows = await client.get_flows_bulk(ids)
    assert route.call_count > 1
    assert rows == [shared, later]


@respx.mock
async def test_later_trigger_batches_keep_limit_and_deduplicate(
    settings: Settings,
) -> None:
    """Duplicate V2 relations do not consume the cap or hide later unique rows."""
    ids = [f"{index:032x}" for index in range(101)]
    respx.get(f"{BASE_URL}/sys_flow_record_trigger").respond(
        200,
        json={"result": [{"sys_id": value} for value in ids]},
        headers={"X-Total-Count": "101"},
    )
    shared = {"sys_id": "a" * 32}
    later = {"sys_id": "b" * 32}

    def respond(request: httpx.Request) -> httpx.Response:
        rows = [shared, later] if ids[-1] in request.url.params["sysparm_query"] else [shared]
        return httpx.Response(200, json={"result": rows}, headers={"X-Total-Count": str(len(rows))})

    respx.get(f"{BASE_URL}/sys_hub_trigger_instance_v2").mock(side_effect=respond)
    respx.get(f"{BASE_URL}/sys_hub_trigger_instance").respond(200, json={"result": []})
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await client.list_triggers_filtered(table="sc_task", limit=2)
    assert result["v2"] == [shared, later]
    assert not result.get("truncation")


@pytest.mark.parametrize("total", ["2", None, "1"])
@respx.mock
async def test_trigger_batch_reports_truncation(settings: Settings, total: str | None) -> None:
    """A full trigger page is incomplete unless a reliable total confirms its end."""
    respx.get(f"{BASE_URL}/sys_hub_trigger_instance_v2").respond(
        200,
        json={"result": [{"sys_id": "a" * 32}]},
        headers={"X-Total-Count": total} if total else {},
    )
    respx.get(f"{BASE_URL}/sys_hub_trigger_instance").respond(200, json={"result": []})
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await client.list_triggers_filtered(limit=1, trigger_type="record_update", active="true")
    if total == "1":
        assert not result.get("truncation")
        return
    batch = result["truncation"]["sys_hub_trigger_instance_v2"]["batches"][0]
    assert batch["returned"] == batch["fetched"] == 1
    assert batch["total"] == (2 if total else None)
    assert "type=record_update^active=true" in batch["continuation"]
    assert "offset=0" in batch["continuation"]


@respx.mock
async def test_known_exact_source_cap_is_complete(settings: Settings) -> None:
    """A reliable count avoids a false truncation warning at the source cap."""
    respx.get(f"{BASE_URL}/sys_flow_record_trigger").respond(
        200,
        json={"result": [{"sys_id": f"{index:032x}"} for index in range(1000)]},
        headers={"X-Total-Count": "1000"},
    )
    for table in ("sys_hub_trigger_instance", "sys_hub_trigger_instance_v2"):
        respx.get(f"{BASE_URL}/{table}").respond(200, json={"result": []})
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await client.list_triggers_filtered(table="sc_task")
    assert result == {"v2": [], "v1": []}


@respx.mock
async def test_later_batch_failure_is_not_partial_success(settings: Settings) -> None:
    """A failed HTTP batch must propagate instead of returning earlier matches."""
    route = respx.get(f"{BASE_URL}/sys_hub_flow").mock(
        side_effect=[
            httpx.Response(200, json={"result": [{"sys_id": "a" * 32}]}),
            httpx.Response(500),
        ]
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(ServerError):
            await client.get_flows_bulk([f"{index:032x}" for index in range(51)])
    assert route.call_count == 2


@respx.mock
async def test_merged_cap_discloses_omitted_later_rows(settings: Settings) -> None:
    """A full earlier batch cannot hide unique rows fetched in a later batch."""
    ids = [f"{index:032x}" for index in range(51)]
    respx.get(f"{BASE_URL}/sys_flow_record_trigger").respond(200, json={"result": [{"sys_id": value} for value in ids]})
    respx.get(f"{BASE_URL}/sys_hub_trigger_instance_v2").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"result": [{"sys_id": "a" * 32}]},
                headers={"X-Total-Count": "1"},
            ),
            httpx.Response(
                200,
                json={"result": [{"sys_id": "b" * 32}]},
                headers={"X-Total-Count": "1"},
            ),
        ]
    )
    respx.get(f"{BASE_URL}/sys_hub_trigger_instance").respond(200, json={"result": []})
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await client.list_triggers_filtered(table="sc_task", limit=1)
    assert result["v2"] == [{"sys_id": "a" * 32}]
    batch = result["truncation"]["sys_hub_trigger_instance_v2"]["batches"][0]
    assert batch["returned"] == 0
    assert batch["fetched"] == batch["total"] == 1
    assert ids[-1] in batch["continuation"]
