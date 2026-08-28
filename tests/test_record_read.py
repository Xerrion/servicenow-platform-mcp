"""Tests for the unified ``record_read`` tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.config import Settings
from servicenow_mcp.tools._dictionary import DictionaryField, DictionaryRegistry, ScriptField
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"
SYS_ID_BR = "a" * 32
SYS_DICT_URL = f"{BASE_URL}/api/now/table/sys_dictionary"
SYS_DB_OBJECT_URL = f"{BASE_URL}/api/now/table/sys_db_object"


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


def _stub_dictionary(
    settings: Settings,
    auth_provider: BasicAuthProvider,
    field_names: list[str],
    script_names: list[str] | None = None,
) -> DictionaryRegistry:
    """Return a dictionary registry with deterministic field discovery."""
    dictionary = DictionaryRegistry(settings, auth_provider)
    all_fields = [
        DictionaryField(name=name, internal_type="string", attributes="", inherited_from=None) for name in field_names
    ]
    scripts = [
        ScriptField(name=name, internal_type="script", inherited_from=None, via_heuristic=False)
        for name in (script_names or [])
    ]
    dictionary.get_all_fields = AsyncMock(return_value=all_fields)  # type: ignore[method-assign]
    dictionary.get_script_fields = AsyncMock(return_value=scripts)  # type: ignore[method-assign]
    return dictionary


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
        dictionary = _stub_dictionary(
            settings,
            auth_provider,
            ["sys_id", "name", "sys_updated_on", "script"],
            ["script"],
        )
        route = respx.get(f"{BASE_URL}/api/now/table/sys_script/{SYS_ID_BR}").mock(
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

        tools = _register_and_get_tools(settings, auth_provider, dictionary=dictionary)
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
        assert route.calls.last.request.url.params["sysparm_fields"] == "sys_id,name,sys_updated_on,script"
        assert data["record"] == {"sys_id": SYS_ID_BR, "name": "BR1", "script": "gs.info('hi');"}
        assert result["selection"]["mode"] == "compact"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_sensitive_field_masked(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        dictionary = _stub_dictionary(
            settings,
            auth_provider,
            ["sys_id", "password", "script"],
            ["script"],
        )
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

        tools = _register_and_get_tools(settings, auth_provider, dictionary=dictionary)
        raw = await tools["record_read"](table="sys_script", sys_id=SYS_ID_BR, fields="password")
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["record"]["password"] == "***MASKED***"  # NOSONAR
        assert result["selection"]["sys_id_added"] is True

    @pytest.mark.asyncio()
    @respx.mock
    async def test_star_returns_full_masked_record(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        dictionary = _stub_dictionary(settings, auth_provider, ["sys_id", "name", "password"])
        route = respx.get(f"{BASE_URL}/api/now/table/sys_script/{SYS_ID_BR}").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": SYS_ID_BR, "name": "BR1", "password": "secret"}},
            )
        )
        tools = _register_and_get_tools(settings, auth_provider, dictionary=dictionary)
        result = decode_response(await tools["record_read"](table="sys_script", sys_id=SYS_ID_BR, fields="*"))

        assert result["status"] == "success"
        assert result["data"]["record"]["password"] == "***MASKED***"
        assert "sysparm_fields" not in route.calls.last.request.url.params
        assert result["selection"]["mode"] == "all"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_unknown_field_returns_error_before_record_io(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        dictionary = _stub_dictionary(settings, auth_provider, ["sys_id", "name"])
        tools = _register_and_get_tools(settings, auth_provider, dictionary=dictionary)
        result = decode_response(await tools["record_read"](table="sys_script", sys_id=SYS_ID_BR, fields="missing"))

        assert result["status"] == "error"
        assert "Unknown field" in result["error"]["message"]
        assert not respx.calls

    @pytest.mark.asyncio()
    @respx.mock
    async def test_invalid_field_returns_error_before_dictionary_io(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["record_read"](table="sys_script", sys_id=SYS_ID_BR, fields="bad-field"))

        assert result["status"] == "error"
        assert "Invalid field projection" in result["error"]["message"]
        assert not respx.calls

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
        dictionary = _stub_dictionary(
            settings,
            auth_provider,
            ["sys_id", "name", "sys_updated_on", "script"],
            ["script"],
        )
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

        tools = _register_and_get_tools(settings, auth_provider, dictionary=dictionary)
        raw = await tools["record_read"](table="sys_script", name="BR1")
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["sys_id"] == SYS_ID_BR
        assert result["data"]["record"]["name"] == "BR1"
        list_call = next(call for call in respx.calls if call.request.url.path.endswith("/sys_script"))
        assert list_call.request.url.params["sysparm_fields"] == "sys_id"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_name_no_match_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        dictionary = _stub_dictionary(settings, auth_provider, ["sys_id", "name"])
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"}),
        )
        tools = _register_and_get_tools(settings, auth_provider, dictionary=dictionary)
        raw = await tools["record_read"](table="sys_script", name="missing")
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "No record found" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_name_ambiguous_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        dictionary = _stub_dictionary(settings, auth_provider, ["sys_id", "name"])
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
        tools = _register_and_get_tools(settings, auth_provider, dictionary=dictionary)
        raw = await tools["record_read"](table="sys_script", name="Dup")
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "Ambiguous" in result["error"]["message"]
