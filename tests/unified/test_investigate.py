"""Tests for the unified ``investigate`` tool (Phase 3a)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from tests.helpers import decode_response, get_tool_functions


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """BasicAuthProvider for the unified-tool test scope."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict[str, Any]:
    """Register the unified ``investigate`` tool on a fresh MCP and return callables."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.unified.investigate import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return get_tool_functions(mcp)


# ---------------------------------------------------------------------------
# action validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_unknown_action_returns_error(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Any action other than 'run' or 'explain' returns a friendly error envelope."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["investigate"](action="frobnicate")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "frobnicate" in result["error"]["message"]
    assert "run" in result["error"]["message"]
    assert "explain" in result["error"]["message"]


# ---------------------------------------------------------------------------
# action='run'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_run_missing_name_returns_error(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """action='run' without a name produces a clear 'name required' error."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["investigate"](action="run")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "name" in result["error"]["message"].lower()
    assert "run" in result["error"]["message"]


@pytest.mark.asyncio()
async def test_run_unknown_investigation_returns_error_with_valid_names(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    """An unknown name lists every available investigation in the error message."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["investigate"](action="run", name="nonexistent")
    result = decode_response(raw)

    assert result["status"] == "error"
    message = result["error"]["message"]
    assert "nonexistent" in message
    for valid_name in (
        "stale_automations",
        "deprecated_apis",
        "table_health",
        "acl_conflicts",
        "error_analysis",
        "slow_transactions",
        "performance_bottlenecks",
    ):
        assert valid_name in message


@pytest.mark.asyncio()
async def test_run_dispatches_to_module_run(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """The parsed params dict reaches ``module.run`` verbatim, and its result flows back."""
    stub_module = type("StubModule", (), {})()
    stub_module.run = AsyncMock(return_value={"finding_count": 0, "findings": [], "marker": "ok"})
    stub_module.explain = AsyncMock()

    fake_registry = {"my_stub": stub_module}
    with patch("servicenow_mcp.tools.unified.investigate.INVESTIGATION_REGISTRY", fake_registry):
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate"](
            action="run",
            name="my_stub",
            params='{"stale_days": 30, "limit": 5}',
        )
    result = decode_response(raw)

    assert result["status"] == "success"
    assert result["data"]["marker"] == "ok"

    stub_module.run.assert_awaited_once()
    _client_arg, params_arg = stub_module.run.await_args.args
    assert params_arg == {"stale_days": 30, "limit": 5}


@pytest.mark.asyncio()
async def test_run_invalid_params_json_returns_error(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Malformed JSON in ``params`` is rejected before any module runs."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["investigate"](
        action="run",
        name="stale_automations",
        params="{not valid json",
    )
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "params" in result["error"]["message"]


# ---------------------------------------------------------------------------
# action='explain'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_explain_missing_element_id_returns_error(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """action='explain' without an element_id is rejected immediately."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["investigate"](action="explain")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "element_id" in result["error"]["message"]


@pytest.mark.asyncio()
async def test_explain_invalid_element_id_format_returns_error(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    """An element_id without a colon fails the 'table:sys_id' format guard."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["investigate"](action="explain", element_id="no_colon_here")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "table:sys_id" in result["error"]["message"]


@pytest.mark.asyncio()
async def test_explain_dispatches_to_module_explain(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """A registry stub's ``explain`` is called with the raw element_id."""
    stub_module = type("StubModule", (), {})()
    stub_module.run = AsyncMock()
    stub_module.explain = AsyncMock(
        return_value={
            "element": "flow_context:fc001",
            "explanation": "stub says hi",
            "record": {"sys_id": "fc001"},
        }
    )

    fake_registry = {"my_stub": stub_module}
    with patch("servicenow_mcp.tools.unified.investigate.INVESTIGATION_REGISTRY", fake_registry):
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate"](
            action="explain",
            element_id="flow_context:fc001",
        )
    result = decode_response(raw)

    assert result["status"] == "success"
    assert result["data"]["explanation"] == "stub says hi"

    stub_module.explain.assert_awaited_once()
    _client_arg, element_arg = stub_module.explain.await_args.args
    assert element_arg == "flow_context:fc001"
