"""Exercise registered investigations through the tool and real HTTP client."""

import json
import re
from typing import Any

import httpx
import pytest
import respx
from mcp.server import MCPServer

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.investigations import INVESTIGATION_REGISTRY
from servicenow_mcp.investigations.slow_transactions import PERFORMANCE_TABLES
from servicenow_mcp.tools.investigate import register_tools
from tests.helpers import decode_response, get_tool_functions


BASE = "https://test.service-now.com"
RECORD_ID = "a" * 32


async def _invoke(settings: Settings, name: str, action: str = "run", **kwargs: Any) -> dict[str, Any]:
    mcp = MCPServer("investigation-workflows")
    register_tools(mcp, settings, OAuthPKCEProvider(settings))
    return decode_response(await get_tool_functions(mcp)["investigate"](action=action, name=name, **kwargs))


def _mock_platform(*, missing_tables: tuple[str, ...] = ()) -> None:
    records: dict[str, list[dict[str, Any]]] = {
        "sys_script": [{"sys_id": f"{index:032x}", "name": "Rule", "collection": "incident"} for index in range(11)],
        "sys_script_include": [{"sys_id": RECORD_ID, "name": "Include", "api_name": "global.Include"}],
        "sys_flow_context": [{"sys_id": RECORD_ID, "name": "Flow", "sys_created_on": "2026-01-01"}],
        "sys_trigger": [{"sys_id": RECORD_ID, "name": "Job", "sys_updated_on": "2026-01-01"}],
        "sysauto_script": [{"sys_id": RECORD_ID, "name": "Job", "run_type": "periodically"}],
        "sys_script_client": [{"sys_id": RECORD_ID, "name": "Client", "password": "fixture-secret"}],
        "sys_ui_policy": [{"sys_id": RECORD_ID, "short_description": "Policy"}],
        "sys_security_acl": [
            {"sys_id": f"{index:032x}", "name": "incident", "operation": "read", "condition": str(index)}
            for index in range(21)
        ],
        "syslog": [
            {"sys_id": RECORD_ID, "source": "mail", "message": "First", "sys_created_on": "2026-01-01 01:00:00"},
            {"sys_id": RECORD_ID, "source": "other", "message": "Other", "sys_created_on": "2026-01-01 02:00:00"},
            {"sys_id": RECORD_ID, "source": "mail", "message": "Last", "sys_created_on": "2026-01-01 03:00:00"},
        ],
    }
    records.update({table: [{"sys_id": RECORD_ID, "name": "Pattern", "count": "5"}] for table, _ in PERFORMANCE_TABLES})

    def query(request: httpx.Request) -> httpx.Response:
        table = request.url.path.rsplit("/", 1)[-1]
        if table in missing_tables:
            return httpx.Response(404, json={"error": {"message": "missing"}})
        rows = records[table]
        return httpx.Response(200, json={"result": rows}, headers={"X-Total-Count": str(len(rows))})

    respx.get(re.compile(re.escape(BASE) + r"/api/now/table/[^/?]+(?:\?.*)?$")).mock(side_effect=query)
    respx.get(f"{BASE}/api/now/stats/incident").respond(200, json={"result": {"stats": {"count": "99"}}})
    respx.get(f"{BASE}/api/sn_codesearch/code_search/search").respond(
        200,
        json={"result": {"search_results": [{"sys_id": RECORD_ID, "className": "sys_script", "name": "Rule"}]}},
    )


@pytest.mark.parametrize("name", list(INVESTIGATION_REGISTRY))
@respx.mock
async def test_registered_investigations_compute_real_findings(settings: Settings, name: str) -> None:
    _mock_platform()
    response = await _invoke(
        settings, name, params=json.dumps({"table": "incident", "hours": 6, "limit": 20, "stale_days": 30})
    )
    assert response["status"] == "success"
    data = response["data"]
    assert data["investigation"] == name
    assert all(finding["provenance"] == {"investigation": name} for finding in data["findings"])
    assert "fixture-secret" not in json.dumps(response)

    if name == "stale_automations":
        assert data["finding_count"] == 14
        assert {finding["category"] for finding in data["findings"]} == {
            "stuck_flow",
            "disabled_business_rule",
            "disabled_script_include",
            "stale_scheduled_job",
        }
    elif name == "deprecated_apis":
        assert data["patterns_searched"] == ["Packages."]
        assert data["finding_count"] == len(data["patterns_searched"])
        assert all(finding["element_id"] == f"sys_script:{RECORD_ID}" for finding in data["findings"])
    elif name == "table_health":
        assert data["record_count"] == 99
        assert data["automation"]["business_rules"]["count"] == 11
        assert data["automation"]["acl_count"] == 21
        assert data["finding_count"] == 3
        assert data["automation"]["client_scripts"]["records"][0]["password"] == "***MASKED***"
    elif name == "acl_conflicts":
        assert data["finding_count"] == 1
        assert data["findings"][0]["count"] == 21
        assert data["findings"][0]["operation"] == "read"
    elif name == "error_analysis":
        assert data["total_errors"] == 3
        assert data["finding_count"] == 2
        cluster = data["findings"][0]
        assert (cluster["source"], cluster["frequency"]) == ("mail", 2)
        assert cluster["sample_messages"] == ["First", "Last"]
        assert cluster["first_seen"] < cluster["last_seen"]
    elif name == "slow_transactions":
        assert data["finding_count"] == 7
        assert {finding["category"] for finding in data["findings"]} == {category for _, category in PERFORMANCE_TABLES}
    else:
        assert data["finding_count"] == 3
        assert data["findings"][0]["category"] == "heavy_automation"
        assert data["findings"][0]["br_count"] == 11

    for call in respx.calls:
        if "/table/" in call.request.url.path:
            assert 0 < int(call.request.url.params["sysparm_limit"]) <= 1000
        if call.request.url.path.endswith("/syslog"):
            assert "sys_created_on>=javascript:gs.hoursAgoStart(6)" in call.request.url.params["sysparm_query"]


@pytest.mark.parametrize(
    ("name", "element_id"),
    [
        ("stale_automations", f"{table}:{RECORD_ID}")
        for table in ("sys_flow_context", "sys_script", "sys_script_include", "sys_trigger")
    ]
    + [
        ("deprecated_apis", f"sys_script:{RECORD_ID}"),
        ("error_analysis", f"syslog:{RECORD_ID}"),
        ("slow_transactions", f"sys_query_pattern:{RECORD_ID}"),
        ("performance_bottlenecks", f"sysauto_script:{RECORD_ID}"),
        ("table_health", "incident"),
        ("performance_bottlenecks", "incident"),
        ("acl_conflicts", RECORD_ID),
        ("acl_conflicts", f"sys_security_acl:{RECORD_ID}"),
    ],
)
@respx.mock
async def test_real_explanations_accept_their_documented_identifiers(
    settings: Settings, name: str, element_id: str
) -> None:
    _mock_platform()
    respx.get(re.compile(re.escape(BASE) + r"/api/now/table/[^/]+/" + RECORD_ID + r"(?:\?.*)?$")).respond(
        200,
        json={"result": {"sys_id": RECORD_ID, "name": "Example", "count": "2", "password": "fixture-secret"}},
    )
    response = await _invoke(settings, name, action="explain", element_id=element_id)
    assert response["status"] == "success"
    assert response["data"]["explanation"]
    assert "selection" not in response
    assert "fixture-secret" not in json.dumps(response)
    assert respx.calls


@respx.mock
async def test_filtered_slow_transaction_search_reports_only_attempted_tables(settings: Settings) -> None:
    _mock_platform()
    response = await _invoke(settings, "slow_transactions", params='{"categories":"slow_query","hours":2}')
    assert response["data"]["tables_queried"] == ["sys_query_pattern"]
    assert response["data"]["finding_count"] == 1


@pytest.mark.parametrize("name", ["slow_transactions", "deprecated_apis"])
@respx.mock
async def test_investigation_timeout_is_reported_instead_of_hidden(settings: Settings, name: str) -> None:
    respx.get(re.compile(re.escape(BASE) + r"/api/.*")).mock(side_effect=httpx.ReadTimeout("private detail"))
    response = await _invoke(settings, name)
    assert response["status"] == "error"
    assert response["error"]["code"] == "UPSTREAM_TIMEOUT"
    assert respx.calls.call_count == 1
    assert "private detail" not in json.dumps(response)


@respx.mock
async def test_missing_optional_performance_table_marks_result_incomplete(settings: Settings) -> None:
    _mock_platform(missing_tables=("sys_query_pattern",))
    response = await _invoke(settings, "slow_transactions", params='{"categories":"slow_query,slow_script"}')
    assert response["status"] == "success"
    assert response["data"]["finding_count"] == 1
    assert response["data"]["complete"] is False
    assert response["warnings"]
