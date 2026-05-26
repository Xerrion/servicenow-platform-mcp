"""Tests for the unified ``record_read`` tool."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.config import Settings
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"
SYS_ID_BR = "a" * 32
SYS_DICT_URL = f"{BASE_URL}/api/now/table/sys_dictionary"
SYS_DB_OBJECT_URL = f"{BASE_URL}/api/now/table/sys_db_object"


def _mock_dictionary_for_sys_script() -> None:
    """Mock sys_db_object + sys_dictionary so record_read can populate script_fields."""
    respx.get(SYS_DB_OBJECT_URL).mock(
        return_value=httpx.Response(200, json={"result": [{"super_class": ""}]}),
    )
    respx.get(SYS_DICT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": [
                    {"element": "script", "internal_type": "script", "attributes": ""},
                ]
            },
        ),
    )


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """BasicAuthProvider for the unified record_read test scope."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
) -> dict[str, Any]:
    """Register the unified ``record_read`` tool on a fresh MCP and return callables."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.record_read import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider, choices=choices, dictionary=dictionary)
    return get_tool_functions(mcp)


class TestArgumentValidation:
    """Cross-argument validation runs before any HTTP call."""

    @pytest.mark.asyncio()
    async def test_missing_table_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_read"](table="", sys_id=SYS_ID_BR)
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "table is required" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_both_sys_id_and_name_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_read"](table="sys_script", sys_id=SYS_ID_BR, name="BR1")
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "exactly one of sys_id or name" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_neither_sys_id_nor_name_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_read"](table="sys_script")
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "exactly one of sys_id or name" in result["error"]["message"]


class TestSysIdLookup:
    """Happy/error paths when ``sys_id`` is supplied."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_sys_id_happy_path(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        _mock_dictionary_for_sys_script()
        respx.get(f"{BASE_URL}/api/now/table/sys_script/{SYS_ID_BR}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": SYS_ID_BR,
                        "name": "BR1",
                        "script": "gs.info('hi');",
                    }
                },
            ),
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_read"](table="sys_script", sys_id=SYS_ID_BR)
        result = decode_response(raw)
        assert result["status"] == "success"

        data = result["data"]
        assert data["table"] == "sys_script"
        assert data["sys_id"] == SYS_ID_BR
        assert data["record"]["name"] == "BR1"
        names = [f["name"] for f in data["script_fields"]]
        assert names == ["script"]
        assert data["script_fields"][0]["internal_type"] == "script"
        assert data["script_fields"][0]["via_heuristic"] is False

    @pytest.mark.asyncio()
    @respx.mock
    async def test_sensitive_field_masked(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        _mock_dictionary_for_sys_script()
        respx.get(f"{BASE_URL}/api/now/table/sys_script/{SYS_ID_BR}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": SYS_ID_BR,
                        "name": "BR1",
                        "password": "s3cret",  # NOSONAR - test-only dummy
                    }
                },
            ),
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_read"](table="sys_script", sys_id=SYS_ID_BR)
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["record"]["password"] == "***MASKED***"  # NOSONAR

    @pytest.mark.asyncio()
    async def test_invalid_sys_id_format_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_read"](table="sys_script", sys_id="not-a-real-id")
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "sys_id" in result["error"]["message"].lower()


class TestNameLookup:
    """Happy/error paths when ``name`` is supplied."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_name_happy_path(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        _mock_dictionary_for_sys_script()
        # name=BR1 query returns exactly one record
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                200,
                json={"result": [{"sys_id": SYS_ID_BR, "name": "BR1"}]},
                headers={"X-Total-Count": "1"},
            ),
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_script/{SYS_ID_BR}").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": SYS_ID_BR, "name": "BR1", "script": "// x"}},
            ),
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_read"](table="sys_script", name="BR1")
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["sys_id"] == SYS_ID_BR
        assert result["data"]["record"]["name"] == "BR1"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_name_no_match_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"}),
        )
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_read"](table="sys_script", name="missing")
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "No record found" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_name_ambiguous_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {"sys_id": "a" * 32, "name": "Dup"},
                        {"sys_id": "b" * 32, "name": "Dup"},
                    ]
                },
                headers={"X-Total-Count": "2"},
            ),
        )
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_read"](table="sys_script", name="Dup")
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "Ambiguous" in result["error"]["message"]
