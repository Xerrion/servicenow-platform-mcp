"""Tests for the unified ``record_read`` tool."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.config import Settings
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"
SYS_ID_BR = "a" * 32


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """BasicAuthProvider for the unified record_read test scope."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
) -> dict[str, Any]:
    """Register the unified ``record_read`` tool on a fresh MCP and return callables."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.unified.record_read import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider, choices=choices)
    return get_tool_functions(mcp)


class TestArgumentValidation:
    """Cross-argument validation runs before any HTTP call."""

    @pytest.mark.asyncio()
    async def test_unknown_artifact_type_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_read"](artifact_type="not_a_real_type", sys_id=SYS_ID_BR)
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "Unknown artifact_type" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_both_sys_id_and_name_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_read"](artifact_type="business_rule", sys_id=SYS_ID_BR, name="BR1")
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "exactly one of sys_id or name" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_neither_sys_id_nor_name_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_read"](artifact_type="business_rule")
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "exactly one of sys_id or name" in result["error"]["message"]


class TestSysIdLookup:
    """Happy/error paths when ``sys_id`` is supplied."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_sys_id_happy_path(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
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
        raw = await tools["record_read"](artifact_type="business_rule", sys_id=SYS_ID_BR)
        result = decode_response(raw)
        assert result["status"] == "success"

        data = result["data"]
        assert data["artifact_type"] == "business_rule"
        assert data["table"] == "sys_script"
        assert data["sys_id"] == SYS_ID_BR
        assert data["record"]["name"] == "BR1"
        assert data["script_fields"] == ["script", "condition"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_sensitive_field_masked(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
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
        raw = await tools["record_read"](artifact_type="business_rule", sys_id=SYS_ID_BR)
        result = decode_response(raw)
        assert result["status"] == "success"
        assert result["data"]["record"]["password"] == "***MASKED***"  # NOSONAR

    @pytest.mark.asyncio()
    async def test_invalid_sys_id_format_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_read"](artifact_type="business_rule", sys_id="not-a-real-id")
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "sys_id" in result["error"]["message"].lower()


class TestNameLookup:
    """Happy/error paths when ``name`` is supplied."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_name_happy_path(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        # First request: name=BR1 query returns exactly one record
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                200,
                json={"result": [{"sys_id": SYS_ID_BR, "name": "BR1"}]},
                headers={"X-Total-Count": "1"},
            ),
        )
        # Second request: get by sys_id
        respx.get(f"{BASE_URL}/api/now/table/sys_script/{SYS_ID_BR}").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": SYS_ID_BR, "name": "BR1", "script": "// x"}},
            ),
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_read"](artifact_type="business_rule", name="BR1")
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
        raw = await tools["record_read"](artifact_type="business_rule", name="missing")
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "No artifact found" in result["error"]["message"]

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
        raw = await tools["record_read"](artifact_type="business_rule", name="Dup")
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "Ambiguous" in result["error"]["message"]
