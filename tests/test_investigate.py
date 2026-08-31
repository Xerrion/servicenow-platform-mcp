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
    from mcp.server import MCPServer

    from servicenow_mcp.tools.investigate import register_tools

    mcp = MCPServer("test")
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
    assert "describe" in result["error"]["message"]


# ---------------------------------------------------------------------------
# Section: run action
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
    with patch("servicenow_mcp.tools.investigate.INVESTIGATION_REGISTRY", fake_registry):
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate"](
            action="run",
            name="my_stub",
            params='{"stale_days": 30, "limit": 5}',
        )
    result = decode_response(raw)

    assert result["status"] == "success"
    assert result["data"]["marker"] == "ok"
    assert result["data"]["provenance"] == {"investigation": "my_stub"}

    stub_module.run.assert_awaited_once()
    _client_arg, params_arg = stub_module.run.await_args.args
    assert params_arg == {"stale_days": 30, "limit": 5}


@pytest.mark.asyncio()
async def test_run_findings_contain_registered_provenance(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """A real investigation result identifies its registered source on each finding."""
    tools = _register_and_get_tools(settings, auth_provider)
    client = AsyncMock()
    client.query_records.side_effect = [
        {"records": [{"sys_id": "flow1", "name": "Flow"}]},
        {"records": []},
        {"records": []},
        {"records": []},
    ]
    cm = AsyncMock()
    cm.__aenter__.return_value = client
    cm.__aexit__.return_value = False

    with patch("servicenow_mcp.tools.investigate.ServiceNowClient", return_value=cm):
        raw = await tools["investigate"](action="run", name="stale_automations")
    result = decode_response(raw)

    assert result["data"]["findings"][0]["provenance"] == {"investigation": "stale_automations"}


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
# Section: explain action
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
            "element": "sys_flow_context:fc001",
            "explanation": "stub says hi",
            "record": {"sys_id": "fc001"},
        }
    )

    fake_registry = {"my_stub": stub_module}
    with patch("servicenow_mcp.tools.investigate.INVESTIGATION_REGISTRY", fake_registry):
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate"](
            action="explain",
            element_id="sys_flow_context:fc001",
        )
    result = decode_response(raw)

    assert result["status"] == "success"
    assert result["data"]["explanation"] == "stub says hi"

    stub_module.explain.assert_awaited_once()
    _client_arg, element_arg = stub_module.explain.await_args.args
    assert element_arg == "sys_flow_context:fc001"
    assert result["selection"]["dispatch"] == {
        "mode": "trial",
        "investigation": "my_stub",
        "attempted": ["my_stub"],
    }


@pytest.mark.asyncio()
async def test_explain_direct_dispatch_invokes_only_named_module(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    """A supplied provenance name dispatches once and does not fall through on decline."""
    selected = type("StubModule", (), {})()
    selected.explain = AsyncMock(return_value={"error": "selected module declined"})
    other = type("StubModule", (), {})()
    other.explain = AsyncMock(return_value={"explanation": "must not run"})
    fake_registry = {"selected": selected, "other": other}

    with patch("servicenow_mcp.tools.investigate.INVESTIGATION_REGISTRY", fake_registry):
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate"](
            action="explain",
            name="selected",
            element_id="sys_flow_context:fc001",
        )
    result = decode_response(raw)

    assert result["data"] == {"error": "selected module declined"}
    selected.explain.assert_awaited_once()
    other.explain.assert_not_awaited()
    assert result["selection"]["dispatch"] == {
        "mode": "direct",
        "investigation": "selected",
        "attempted": ["selected"],
    }


@pytest.mark.asyncio()
async def test_explain_unknown_direct_name_fails_before_io(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    """An invalid direct selector is rejected before a client context is created."""
    with patch("servicenow_mcp.tools.investigate.ServiceNowClient") as client_factory:
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate"](
            action="explain",
            name="unknown",
            element_id="sys_flow_context:fc001",
        )
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "Unknown investigation 'unknown'" in result["error"]["message"]
    client_factory.assert_not_called()


@pytest.mark.asyncio()
async def test_explain_legacy_trial_dispatch_continues_after_decline(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    """Without a selector, explain keeps trial dispatch and reports all attempts."""
    first = type("StubModule", (), {})()
    first.explain = AsyncMock(return_value={"error": "declined"})
    second = type("StubModule", (), {})()
    second.explain = AsyncMock(return_value={"explanation": "accepted"})
    fake_registry = {"first": first, "second": second}

    with patch("servicenow_mcp.tools.investigate.INVESTIGATION_REGISTRY", fake_registry):
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["investigate"](action="explain", element_id="syslog:log1")
    result = decode_response(raw)

    first.explain.assert_awaited_once()
    second.explain.assert_awaited_once()
    assert result["selection"]["dispatch"] == {
        "mode": "trial",
        "investigation": "second",
        "attempted": ["first", "second"],
    }


# ---------------------------------------------------------------------------
# Section: describe action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_describe_with_no_name_returns_directory(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """action='describe' without a name returns a sorted directory of investigations."""
    from servicenow_mcp.investigations import INVESTIGATION_REGISTRY

    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["investigate"](action="describe")
    result = decode_response(raw)

    assert result["status"] == "success"
    assert result["data"]["investigations"] == sorted(INVESTIGATION_REGISTRY.keys())
    assert result["data"]["actions"]["explain"]["params"]["name"].startswith("registered investigation name")
    for expected in (
        "stale_automations",
        "deprecated_apis",
        "table_health",
        "acl_conflicts",
        "error_analysis",
        "slow_transactions",
        "performance_bottlenecks",
    ):
        assert expected in result["data"]["investigations"]


@pytest.mark.asyncio()
async def test_describe_with_unknown_name_returns_error_with_valid_names(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    """An unknown describe name lists every available investigation in the error message."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["investigate"](action="describe", name="nonexistent")
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
async def test_describe_returns_params_schema(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """describe(name=...) returns the module's PARAMS schema and a one-line description."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["investigate"](action="describe", name="stale_automations")
    result = decode_response(raw)

    assert result["status"] == "success"
    data = result["data"]
    assert data["name"] == "stale_automations"
    assert isinstance(data["description"], str)
    assert data["description"]

    params = data["params"]
    assert isinstance(params, dict)
    for key in ("stale_days", "limit"):
        assert key in params
        entry = params[key]
        assert "type" in entry
        assert "default" in entry
        assert "description" in entry


@pytest.mark.asyncio()
async def test_describe_for_required_param_marks_required(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """A required param is flagged as required; an optional one is not."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["investigate"](action="describe", name="table_health")
    result = decode_response(raw)

    assert result["status"] == "success"
    params = result["data"]["params"]
    assert params["table"]["required"] is True
    assert params["hours"].get("required", False) is False
