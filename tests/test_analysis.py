"""Tests for the composed read-only analysis tool."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from mcp.server import MCPServer

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.tools.analysis import register_tools
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"
SYS_ID = "a" * 32


def _tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict[str, Any]:
    mcp = MCPServer("test")
    register_tools(mcp, settings, auth_provider)
    return get_tool_functions(mcp)


@pytest.mark.asyncio()
@respx.mock
async def test_describe_has_no_io(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    result = decode_response(await _tools(settings, auth_provider)["analysis"](action="describe"))
    assert set(result["data"]["actions"]) == {"ritm_variables", "journal_history", "describe"}
    assert not respx.calls


@pytest.mark.asyncio()
@respx.mock
async def test_ritm_variables_masks_sensitive_answer_and_paginates(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    option_id = "b" * 32
    definition_id = "c" * 32
    respx.get(f"{BASE_URL}/api/now/table/sc_req_item/{SYS_ID}").mock(
        return_value=httpx.Response(200, json={"result": {"sys_id": SYS_ID}})
    )
    respx.get(f"{BASE_URL}/api/now/table/sc_item_option_mtom").mock(
        return_value=httpx.Response(
            200,
            json={"result": [{"sys_id": "d" * 32, "sc_item_option": option_id}]},
            headers={"X-Total-Count": "2"},
        )
    )
    respx.get(f"{BASE_URL}/api/now/table/sc_item_option").mock(
        return_value=httpx.Response(
            200,
            json={"result": [{"sys_id": option_id, "item_option_new": definition_id, "value": "synthetic"}]},
        )
    )
    respx.get(f"{BASE_URL}/api/now/table/item_option_new").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": [
                    {
                        "sys_id": definition_id,
                        "name": "requested_value",
                        "question_text": "API Key",
                        "type": "string",
                        "reference": "",
                        "variable_set": "",
                    }
                ]
            },
        )
    )
    result = decode_response(
        await _tools(settings, auth_provider)["analysis"](action="ritm_variables", sys_id=SYS_ID, limit=1)
    )
    entry = result["data"]["entries"][0]
    assert entry["raw_value"] == "***MASKED***"
    assert entry["display_value"] == "***MASKED***"
    assert result["pagination"] == {"offset": 0, "limit": 1, "total": 2}
    assert result["selection"]["next_offset"] == 1


@pytest.mark.asyncio()
@respx.mock
async def test_ritm_variables_empty_or_orphaned(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    respx.get(f"{BASE_URL}/api/now/table/sc_req_item/{SYS_ID}").mock(
        return_value=httpx.Response(200, json={"result": {"sys_id": SYS_ID}})
    )
    links = respx.get(f"{BASE_URL}/api/now/table/sc_item_option_mtom").mock(
        return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
    )
    tools = _tools(settings, auth_provider)
    empty = decode_response(await tools["analysis"](action="ritm_variables", sys_id=SYS_ID))
    assert empty["data"]["entries"] == []

    links.return_value = httpx.Response(
        200,
        json={"result": [{"sys_id": "d" * 32, "sc_item_option": "b" * 32}]},
    )
    respx.get(f"{BASE_URL}/api/now/table/sc_item_option").mock(return_value=httpx.Response(200, json={"result": []}))
    orphaned = decode_response(await tools["analysis"](action="ritm_variables", sys_id=SYS_ID))
    assert orphaned["data"]["entries"][0]["status"] == "orphaned_option"


@pytest.mark.asyncio()
@respx.mock
async def test_ritm_variables_missing_and_invalid(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    tools = _tools(settings, auth_provider)
    invalid = decode_response(await tools["analysis"](action="ritm_variables", sys_id="invalid"))
    assert invalid["status"] == "error"
    assert not respx.calls

    respx.get(f"{BASE_URL}/api/now/table/sc_req_item/{SYS_ID}").mock(
        return_value=httpx.Response(404, json={"error": {"message": "not found"}})
    )
    missing = decode_response(await tools["analysis"](action="ritm_variables", sys_id=SYS_ID))
    assert missing["status"] == "error"


@pytest.mark.asyncio()
@respx.mock
async def test_ritm_variables_marks_reference_mrvs_and_duplicate_answers(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    option_id = "b" * 32
    definition_id = "c" * 32
    respx.get(f"{BASE_URL}/api/now/table/sc_req_item/{SYS_ID}").mock(
        return_value=httpx.Response(200, json={"result": {"sys_id": SYS_ID}})
    )
    respx.get(f"{BASE_URL}/api/now/table/sc_item_option_mtom").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": [
                    {"sys_id": "d" * 32, "sc_item_option": option_id},
                    {"sys_id": "e" * 32, "sc_item_option": option_id},
                ]
            },
        )
    )
    respx.get(f"{BASE_URL}/api/now/table/sc_item_option").mock(
        return_value=httpx.Response(
            200,
            json={"result": [{"sys_id": option_id, "item_option_new": definition_id, "value": "f" * 32}]},
        )
    )
    respx.get(f"{BASE_URL}/api/now/table/item_option_new").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": [
                    {
                        "sys_id": definition_id,
                        "name": "rows",
                        "question_text": "Rows",
                        "type": "21",
                        "reference": "sys_user",
                        "variable_set": "g" * 32,
                    }
                ]
            },
        )
    )
    result = decode_response(await _tools(settings, auth_provider)["analysis"](action="ritm_variables", sys_id=SYS_ID))
    assert [entry["status"] for entry in result["data"]["entries"]] == ["unsupported_mrvs", "unsupported_mrvs"]
    assert all(entry["display_value"] is None for entry in result["data"]["entries"])
    assert any("raw sys_ids" in warning for warning in result["warnings"])
    assert any("Duplicate" in warning for warning in result["warnings"])


@pytest.mark.asyncio()
@respx.mock
async def test_journal_history_is_bounded_and_deterministic(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    db_route = respx.get(f"{BASE_URL}/api/now/table/sys_db_object").mock(
        side_effect=[
            httpx.Response(200, json={"result": [{"super_class.name": "task"}]}),
            httpx.Response(200, json={"result": [{"super_class.name": ""}]}),
        ]
    )
    del db_route
    respx.get(f"{BASE_URL}/api/now/table/sys_dictionary").mock(
        side_effect=[
            httpx.Response(200, json={"result": []}),
            httpx.Response(
                200,
                json={
                    "result": [
                        {"element": "comments", "internal_type": "journal_input", "attributes": ""},
                        {"element": "work_notes", "internal_type": "journal_input", "attributes": ""},
                    ]
                },
            ),
        ]
    )
    respx.get(f"{BASE_URL}/api/now/table/incident").mock(
        return_value=httpx.Response(200, json={"result": [{"sys_id": SYS_ID}]})
    )

    def journal_handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["sysparm_query"]
        assert f"name=incident^element_id={SYS_ID}" in query
        assert "elementINcomments,work_notes" in query
        assert "sys_created_on>=2024-01-01" in query
        assert "ORDERBYsys_created_on^ORDERBYsys_id" in query
        return httpx.Response(
            200,
            json={"result": [{"sys_id": "b" * 32, "element": "comments", "value": "synthetic"}]},
            headers={"X-Total-Count": "2"},
        )

    respx.get(f"{BASE_URL}/api/now/table/sys_journal_field").mock(side_effect=journal_handler)
    result = decode_response(
        await _tools(settings, auth_provider)["analysis"](
            action="journal_history",
            table="incident",
            sys_id=SYS_ID,
            since="2024-01-01",
            limit=1,
        )
    )
    assert result["status"] == "success"
    assert result["pagination"] == {"offset": 0, "limit": 1, "total": 2}
    assert result["selection"]["next_offset"] == 1
    assert "ACLs" in result["warnings"][0]


@pytest.mark.asyncio()
async def test_journal_history_rejects_invalid_or_denied_input(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    tools = _tools(settings, auth_provider)
    invalid = decode_response(
        await tools["analysis"](
            action="journal_history",
            table="incident",
            sys_id=SYS_ID,
            fields_csv="value^ORname=task",
        )
    )
    assert invalid["status"] == "error"
    denied = decode_response(await tools["analysis"](action="journal_history", table="sys_credentials", sys_id=SYS_ID))
    assert denied["status"] == "error"


@pytest.mark.asyncio()
@respx.mock
async def test_analysis_http_error_returns_envelope(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    respx.get(f"{BASE_URL}/api/now/table/sc_req_item/{SYS_ID}").mock(
        return_value=httpx.Response(500, json={"error": {"message": "synthetic failure"}})
    )
    result = decode_response(await _tools(settings, auth_provider)["analysis"](action="ritm_variables", sys_id=SYS_ID))
    assert result["status"] == "error"
    assert result["error"]["message"] == "synthetic failure"
