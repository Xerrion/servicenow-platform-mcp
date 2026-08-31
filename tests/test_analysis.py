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


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """Create a basic authentication provider for analysis tests."""
    return BasicAuthProvider(settings)


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
    respx.get(f"{BASE_URL}/api/now/table/sc_multi_row_question_answer").mock(
        return_value=httpx.Response(200, json={"result": []})
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
    assert result["data"]["unsupported_features"] == {
        "multi_row_variable_sets": {"present": False, "payload_fields_retrieved": False}
    }


@pytest.mark.asyncio()
@respx.mock
async def test_ritm_variables_empty_or_orphaned(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    respx.get(f"{BASE_URL}/api/now/table/sc_req_item/{SYS_ID}").mock(
        return_value=httpx.Response(200, json={"result": {"sys_id": SYS_ID}})
    )
    respx.get(f"{BASE_URL}/api/now/table/sc_multi_row_question_answer").mock(
        return_value=httpx.Response(200, json={"result": []})
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
async def test_ritm_variables_handles_list_collector_and_duplicate_answers(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    option_id = "b" * 32
    definition_id = "c" * 32
    respx.get(f"{BASE_URL}/api/now/table/sc_req_item/{SYS_ID}").mock(
        return_value=httpx.Response(200, json={"result": {"sys_id": SYS_ID}})
    )
    respx.get(f"{BASE_URL}/api/now/table/sc_multi_row_question_answer").mock(
        return_value=httpx.Response(200, json={"result": []})
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
            json={
                "result": [
                    {
                        "sys_id": option_id,
                        "item_option_new": definition_id,
                        "value": f"{'f' * 32},{'h' * 32}",
                    }
                ]
            },
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
    assert [entry["status"] for entry in result["data"]["entries"]] == ["resolved", "resolved"]
    assert all(entry["multi_value"] is True for entry in result["data"]["entries"])
    assert all(entry["display_value"] is None for entry in result["data"]["entries"])
    assert sum("raw sys_ids" in warning for warning in result["warnings"]) == 1
    assert any("Duplicate" in warning for warning in result["warnings"])


@pytest.mark.asyncio()
@respx.mock
async def test_ritm_variables_discloses_mrvs_presence_without_payload(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    respx.get(f"{BASE_URL}/api/now/table/sc_req_item/{SYS_ID}").mock(
        return_value=httpx.Response(200, json={"result": {"sys_id": SYS_ID}})
    )

    def mrvs_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["sysparm_query"] == (
            f"parent_id={SYS_ID}^parent_table_name=sc_req_item^ORDERBYsys_id"
        )
        assert request.url.params["sysparm_fields"] == "sys_id"
        assert request.url.params["sysparm_limit"] == "1"
        return httpx.Response(200, json={"result": [{"sys_id": "b" * 32}]})

    respx.get(f"{BASE_URL}/api/now/table/sc_multi_row_question_answer").mock(side_effect=mrvs_handler)
    respx.get(f"{BASE_URL}/api/now/table/sc_item_option_mtom").mock(
        return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
    )

    result = decode_response(
        await _tools(settings, auth_provider)["analysis"](action="ritm_variables", sys_id=SYS_ID, limit=2, offset=4)
    )
    assert result["data"]["entries"] == []
    assert result["data"]["entry_count"] == 0
    assert result["data"]["unsupported_features"] == {
        "multi_row_variable_sets": {"present": True, "payload_fields_retrieved": False}
    }
    assert len(result["warnings"]) == 1
    assert "payload" in result["warnings"][0]
    assert result["pagination"] == {"offset": 4, "limit": 2, "total": 0}
    assert result["selection"] == {"mode": "submitted_answers", "truncated": False, "next_offset": None}


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("offset", "option_id", "expected_next_offset"),
    [
        pytest.param(0, "b" * 32, 1, id="full-answer-page"),
        pytest.param(1, "c" * 32, None, id="later-offset"),
    ],
)
@respx.mock
async def test_ritm_variables_mrvs_metadata_does_not_distort_answer_pagination(
    settings: Settings,
    auth_provider: BasicAuthProvider,
    offset: int,
    option_id: str,
    expected_next_offset: int | None,
) -> None:
    definition_id = "d" * 32
    respx.get(f"{BASE_URL}/api/now/table/sc_req_item/{SYS_ID}").mock(
        return_value=httpx.Response(200, json={"result": {"sys_id": SYS_ID}})
    )
    respx.get(f"{BASE_URL}/api/now/table/sc_multi_row_question_answer").mock(
        return_value=httpx.Response(200, json={"result": [{"sys_id": "e" * 32}]})
    )

    def links_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["sysparm_limit"] == "1"
        assert request.url.params["sysparm_offset"] == str(offset)
        return httpx.Response(
            200,
            json={"result": [{"sys_id": "f" * 32, "sc_item_option": option_id}]},
            headers={"X-Total-Count": "2"},
        )

    respx.get(f"{BASE_URL}/api/now/table/sc_item_option_mtom").mock(side_effect=links_handler)
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
                        "question_text": "Requested value",
                        "type": "string",
                        "reference": "",
                        "variable_set": "",
                    }
                ]
            },
        )
    )

    result = decode_response(
        await _tools(settings, auth_provider)["analysis"](
            action="ritm_variables",
            sys_id=SYS_ID,
            limit=1,
            offset=offset,
        )
    )
    assert len(result["data"]["entries"]) == 1
    assert result["data"]["entry_count"] == 1
    assert result["data"]["entries"][0]["answer_sys_id"] == option_id
    assert result["data"]["entries"][0]["status"] == "resolved"
    assert result["data"]["unsupported_features"] == {
        "multi_row_variable_sets": {"present": True, "payload_fields_retrieved": False}
    }
    assert result["pagination"] == {"offset": offset, "limit": 1, "total": 2}
    assert result["selection"] == {
        "mode": "submitted_answers",
        "truncated": expected_next_offset is not None,
        "next_offset": expected_next_offset,
    }
    assert len(result["warnings"]) == 1


@pytest.mark.asyncio()
@respx.mock
async def test_ritm_variables_deduplicates_mrvs_warnings(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    option_id = "b" * 32
    definition_id = "c" * 32
    respx.get(f"{BASE_URL}/api/now/table/sc_req_item/{SYS_ID}").mock(
        return_value=httpx.Response(200, json={"result": {"sys_id": SYS_ID}})
    )
    respx.get(f"{BASE_URL}/api/now/table/sc_multi_row_question_answer").mock(
        return_value=httpx.Response(200, json={"result": [{"sys_id": "d" * 32}]})
    )
    respx.get(f"{BASE_URL}/api/now/table/sc_item_option_mtom").mock(
        return_value=httpx.Response(
            200,
            json={"result": [{"sys_id": "e" * 32, "sc_item_option": option_id}]},
        )
    )
    respx.get(f"{BASE_URL}/api/now/table/sc_item_option").mock(
        return_value=httpx.Response(
            200,
            json={"result": [{"sys_id": option_id, "item_option_new": definition_id, "value": ""}]},
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
                        "type": "multi_row_variable_set",
                        "reference": "",
                        "variable_set": "f" * 32,
                    }
                ]
            },
        )
    )

    result = decode_response(await _tools(settings, auth_provider)["analysis"](action="ritm_variables", sys_id=SYS_ID))
    assert [entry["status"] for entry in result["data"]["entries"]] == ["unsupported_mrvs"]
    assert result["data"]["unsupported_features"] == {
        "multi_row_variable_sets": {"present": True, "payload_fields_retrieved": False}
    }
    assert len(result["warnings"]) == 2
    assert len(set(result["warnings"])) == 2


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("name", "question_text"),
    [
        (None, "Credential"),
        ("credential", None),
        ("", ""),
    ],
)
@respx.mock
async def test_ritm_variables_masks_incomplete_definition_metadata(
    settings: Settings,
    auth_provider: BasicAuthProvider,
    name: str | None,
    question_text: str | None,
) -> None:
    option_id = "b" * 32
    definition_id = "c" * 32
    secret = "synthetic-secret"
    respx.get(f"{BASE_URL}/api/now/table/sc_req_item/{SYS_ID}").mock(
        return_value=httpx.Response(200, json={"result": {"sys_id": SYS_ID}})
    )
    respx.get(f"{BASE_URL}/api/now/table/sc_multi_row_question_answer").mock(
        return_value=httpx.Response(200, json={"result": []})
    )
    respx.get(f"{BASE_URL}/api/now/table/sc_item_option_mtom").mock(
        return_value=httpx.Response(
            200,
            json={"result": [{"sys_id": "d" * 32, "sc_item_option": option_id}]},
        )
    )
    respx.get(f"{BASE_URL}/api/now/table/sc_item_option").mock(
        return_value=httpx.Response(
            200,
            json={"result": [{"sys_id": option_id, "item_option_new": definition_id, "value": secret}]},
        )
    )
    definition = {
        "sys_id": definition_id,
        "type": "string",
        "reference": "",
        "variable_set": "",
    }
    if name is not None:
        definition["name"] = name
    if question_text is not None:
        definition["question_text"] = question_text
    respx.get(f"{BASE_URL}/api/now/table/item_option_new").mock(
        return_value=httpx.Response(200, json={"result": [definition]})
    )

    result = decode_response(await _tools(settings, auth_provider)["analysis"](action="ritm_variables", sys_id=SYS_ID))
    entry = result["data"]["entries"][0]
    assert entry["status"] == "inaccessible_definition_metadata"
    assert entry["raw_value"] == "***MASKED***"
    assert entry["display_value"] == "***MASKED***"
    assert entry["masked"] is True
    assert secret not in str(result)
    assert result["warnings"] == [
        "One or more variable definitions had incomplete metadata; affected answers were conservatively masked."
    ]


@pytest.mark.asyncio()
@respx.mock
async def test_ritm_variables_deduplicates_incomplete_metadata_warning(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    option_ids = ["b" * 32, "c" * 32]
    definition_ids = ["d" * 32, "e" * 32]
    respx.get(f"{BASE_URL}/api/now/table/sc_req_item/{SYS_ID}").mock(
        return_value=httpx.Response(200, json={"result": {"sys_id": SYS_ID}})
    )
    respx.get(f"{BASE_URL}/api/now/table/sc_multi_row_question_answer").mock(
        return_value=httpx.Response(200, json={"result": []})
    )
    respx.get(f"{BASE_URL}/api/now/table/sc_item_option_mtom").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": [
                    {"sys_id": "f" * 32, "sc_item_option": option_ids[0]},
                    {"sys_id": "g" * 32, "sc_item_option": option_ids[1]},
                ]
            },
        )
    )
    respx.get(f"{BASE_URL}/api/now/table/sc_item_option").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": [
                    {"sys_id": option_ids[0], "item_option_new": definition_ids[0], "value": "synthetic-one"},
                    {"sys_id": option_ids[1], "item_option_new": definition_ids[1], "value": "synthetic-two"},
                ]
            },
        )
    )
    respx.get(f"{BASE_URL}/api/now/table/item_option_new").mock(
        return_value=httpx.Response(
            200,
            json={
                "result": [
                    {"sys_id": definition_ids[0], "name": "one", "type": "string", "reference": ""},
                    {"sys_id": definition_ids[1], "question_text": "Two", "type": "string", "reference": ""},
                ]
            },
        )
    )

    result = decode_response(await _tools(settings, auth_provider)["analysis"](action="ritm_variables", sys_id=SYS_ID))
    assert all(entry["status"] == "inaccessible_definition_metadata" for entry in result["data"]["entries"])
    assert len(result["warnings"]) == 1


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
