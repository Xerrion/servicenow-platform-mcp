"""Tests for the unified ``flow`` tool group (Phase 3 - Flow Designer)."""

from __future__ import annotations

import base64
import gzip
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from tests.helpers import decode_response, get_tool_functions


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """BasicAuthProvider for the unified-tool test scope."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: Any | None = None,
) -> dict[str, Any]:
    """Register the unified ``flow`` tool on a fresh MCP and return callables."""
    from servicenow_mcp.tools.flow import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider, choices=choices)
    return get_tool_functions(mcp)


def _make_client_mock(**method_returns: Any) -> AsyncMock:
    """Build an AsyncMock client whose listed coroutine methods return the given values."""
    client = AsyncMock()
    for name, value in method_returns.items():
        getattr(client, name).return_value = value
    return client


def _patch_client(client: AsyncMock) -> Any:
    """Patch ``flow.ServiceNowClient`` to yield *client* from its async context."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=cm)
    return patch("servicenow_mcp.tools.flow.ServiceNowClient", factory)


def _encode_values(payload: Any) -> str:
    """Build a gzip+base64+json blob the way ServiceNow stores ``values``."""
    raw = json.dumps(payload).encode("utf-8")
    return base64.b64encode(gzip.compress(raw)).decode("ascii")


def _ref(value: str, display: str = "") -> dict[str, str]:
    """Mimic a display_value=all reference field shape."""
    return {"value": value, "display_value": display or value}


SYS_ID_FLOW = "f" * 32


# ---------------------------------------------------------------------------
# describe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_describe_returns_all_action_keys(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """``describe`` advertises all flow actions."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["flow"](action="describe")
    result = decode_response(raw)

    assert result["status"] == "success"
    actions = result["data"]["actions"]
    for name in ("contract", "inspect", "find_by_table", "decode_values", "list_triggers", "describe"):
        assert name in actions
    assert "sections" in actions["inspect"]["params"]
    assert "section_limit" in actions["contract"]["params"]


# ---------------------------------------------------------------------------
# decode_values
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_decode_values_action_success(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """A real gzip+base64 blob is decoded back to the original structure."""
    tools = _register_and_get_tools(settings, auth_provider)
    blob = _encode_values([{"name": "x", "value": "y"}])

    raw = await tools["flow"](action="decode_values", value=blob)
    result = decode_response(raw)

    assert result["status"] == "success"
    assert result["data"]["decoded"] == [{"name": "x", "value": "y"}]
    assert result["data"]["encoding"] == "gzip+base64+json"


@pytest.mark.asyncio()
async def test_decode_values_action_garbage_returns_error(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Malformed input surfaces a structured error envelope, not an exception."""
    tools = _register_and_get_tools(settings, auth_provider)

    raw = await tools["flow"](action="decode_values", value="H4sIAnope!")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert result["data"] is None
    assert "not valid" in result["error"]["message"]
    assert "base64" in result["error"]["message"]


@pytest.mark.asyncio()
async def test_decode_values_action_missing_value_returns_error(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    """An empty ``value`` argument is rejected before any decode attempt."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["flow"](action="decode_values")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "value" in result["error"]["message"]


# ---------------------------------------------------------------------------
# inspect: validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_inspect_rejects_both_sys_id_and_name(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """``inspect`` requires exactly one of sys_id / name."""
    tools = _register_and_get_tools(settings, auth_provider)
    client = _make_client_mock()
    with _patch_client(client):
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW, name="Some Flow")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "exactly one" in result["error"]["message"].lower()


@pytest.mark.asyncio()
async def test_inspect_rejects_neither_sys_id_nor_name(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """``inspect`` with no identifier is rejected."""
    tools = _register_and_get_tools(settings, auth_provider)
    client = _make_client_mock()
    with _patch_client(client):
        raw = await tools["flow"](action="inspect")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "required" in result["error"]["message"].lower()


# ---------------------------------------------------------------------------
# inspect: name resolution
# ---------------------------------------------------------------------------


def _minimal_flow_header(sys_id: str = SYS_ID_FLOW) -> dict[str, Any]:
    """Build a minimal ``sys_hub_flow`` header row."""
    return {
        "sys_id": _ref(sys_id),
        "name": _ref(sys_id, "My Flow"),
        "internal_name": _ref("my_flow"),
        "type": _ref("flow"),
        "active": _ref("true"),
        "description": _ref(""),
        "sys_scope": _ref("global", "Global"),
        "master_snapshot": _ref("snap1"),
        "latest_snapshot": _ref("snap1"),
    }


def _empty_inspect_kwargs(header_sys_id: str = SYS_ID_FLOW) -> dict[str, Any]:
    """Default async-method return values for an inspect call with no body."""
    return {
        "get_flow_by_sys_id": _minimal_flow_header(header_sys_id),
        "list_flow_inputs": [],
        "list_flow_outputs": [],
        "list_flow_variables": [],
        "list_action_instances_v2": [],
        "list_action_instances_v1": [],
        "list_logic_instances_v2": [],
        "list_logic_instances_v1": [],
        "list_trigger_instances_v2": [],
        "list_trigger_instances_v1": [],
        "list_record_triggers": [],
        "get_action_type_definitions": [],
        "list_action_input_definitions": [],
        "list_action_output_definitions": [],
        "list_v1_variable_values": [],
    }


@pytest.mark.asyncio()
async def test_inspect_compact_default_omits_optional_detail_requests(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    """The compact default fetches only the header and six bounded structural datasets."""
    tools = _register_and_get_tools(settings, auth_provider)
    client = _make_client_mock(**_empty_inspect_kwargs())

    with _patch_client(client):
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW)
    result = decode_response(raw)

    assert result["status"] == "success"
    assert list(result["data"]) == ["flow", "published_state", "structural_summary", "warnings"]
    assert result["selection"]["mode"] == "compact"
    for method_name in (
        "list_flow_inputs",
        "list_flow_outputs",
        "list_flow_variables",
        "list_record_triggers",
        "get_action_type_definitions",
        "list_action_input_definitions",
        "list_action_output_definitions",
        "list_v1_variable_values",
    ):
        getattr(client, method_name).assert_not_awaited()


@pytest.mark.asyncio()
async def test_inspect_invalid_section_fails_before_service_now_io(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    """An invalid selector is rejected before a ServiceNow client is opened."""
    tools = _register_and_get_tools(settings, auth_provider)
    with patch("servicenow_mcp.tools.flow.ServiceNowClient") as client_factory:
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW, sections="unknown")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "Unknown section(s): unknown" in result["error"]["message"]
    client_factory.assert_not_called()


@pytest.mark.asyncio()
async def test_inspect_explicit_sections_fetch_dependencies_once_and_only_when_needed(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    """Overlapping section dependencies are fetched once and unrelated tables are omitted."""
    tools = _register_and_get_tools(settings, auth_provider)
    client = _make_client_mock(**_empty_inspect_kwargs())

    with _patch_client(client):
        raw = await tools["flow"](
            action="inspect",
            sys_id=SYS_ID_FLOW,
            sections="structural_summary,canvas",
        )
    result = decode_response(raw)

    assert result["status"] == "success"
    for method_name in (
        "list_action_instances_v2",
        "list_logic_instances_v2",
        "list_trigger_instances_v2",
        "list_trigger_instances_v1",
    ):
        getattr(client, method_name).assert_awaited_once_with(SYS_ID_FLOW, 101)
    client.list_action_instances_v1.assert_awaited_once_with(SYS_ID_FLOW, 101)
    client.list_logic_instances_v1.assert_awaited_once_with(SYS_ID_FLOW, 101)
    client.list_flow_inputs.assert_not_awaited()
    client.list_flow_outputs.assert_not_awaited()
    client.list_flow_variables.assert_not_awaited()


@pytest.mark.asyncio()
async def test_inspect_section_limit_discloses_truncation_and_continuation(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    """A selected large section is bounded and reports how to request more."""
    tools = _register_and_get_tools(settings, auth_provider)
    actions = [
        {
            "sys_id": _ref(f"a{index}"),
            "ui_uuid": _ref(f"ui_a{index}"),
            "parent_ui_uuid": _ref(""),
            "order": _ref(str(index)),
            "label": _ref(f"Action {index}"),
            "action_type": _ref("atype", "Action"),
            "values": _ref(""),
        }
        for index in range(3)
    ]
    kwargs = _empty_inspect_kwargs()
    kwargs["list_action_instances_v2"] = actions
    client = _make_client_mock(**kwargs)

    with _patch_client(client):
        raw = await tools["flow"](
            action="inspect",
            sys_id=SYS_ID_FLOW,
            sections="canvas",
            section_limit=2,
        )
    result = decode_response(raw)

    assert len(result["data"]["canvas"]) == 2
    assert result["selection"]["truncated"] is True
    assert result["selection"]["truncation"]["canvas"] == {
        "returned": 2,
        "observed_at_least": 3,
        "omitted_at_least": 1,
        "continuation": "Re-run with section_limit greater than 2.",
    }


@pytest.mark.asyncio()
async def test_inspect_canvas_only_decodes_nodes_without_warning_dependencies(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    """Canvas alone fetches and decodes V2 nodes without trigger or V1 warning reads."""
    tools = _register_and_get_tools(settings, auth_provider)
    kwargs = _empty_inspect_kwargs()
    kwargs["list_action_instances_v2"] = [
        {
            "sys_id": _ref("a1"),
            "ui_uuid": _ref("ui_a1"),
            "parent_ui_uuid": _ref(""),
            "order": _ref("1"),
            "label": _ref("Action"),
            "action_type": _ref("atype", "Action"),
            "values": _ref(_encode_values([{"name": "x", "value": "y"}])),
        }
    ]
    client = _make_client_mock(**kwargs)

    with _patch_client(client):
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW, sections="canvas")
    result = decode_response(raw)

    assert result["data"]["canvas"][0]["values_decoded"] == [{"name": "x", "value": "y"}]
    client.list_action_instances_v2.assert_awaited_once()
    client.list_logic_instances_v2.assert_awaited_once()
    client.list_action_instances_v1.assert_not_awaited()
    client.list_logic_instances_v1.assert_not_awaited()
    client.list_trigger_instances_v2.assert_not_awaited()
    client.list_trigger_instances_v1.assert_not_awaited()


@pytest.mark.asyncio()
async def test_inspect_resolves_unique_name(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """A name resolving to exactly one flow uses that sys_id."""
    tools = _register_and_get_tools(settings, auth_provider)
    client = _make_client_mock(
        find_flows_by_name=[{"sys_id": _ref(SYS_ID_FLOW), "name": _ref("My Flow")}],
        **_empty_inspect_kwargs(),
    )
    with _patch_client(client):
        raw = await tools["flow"](action="inspect", name="My Flow")
    result = decode_response(raw)

    assert result["status"] == "success"
    client.get_flow_by_sys_id.assert_awaited_once_with(SYS_ID_FLOW)


@pytest.mark.asyncio()
async def test_inspect_ambiguous_name_returns_error(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Multiple matches on a name are rejected with a useful error."""
    tools = _register_and_get_tools(settings, auth_provider)
    client = _make_client_mock(
        find_flows_by_name=[
            {"sys_id": _ref("a" * 32), "name": _ref("Dup")},
            {"sys_id": _ref("b" * 32), "name": _ref("Dup")},
        ],
    )
    with _patch_client(client):
        raw = await tools["flow"](action="inspect", name="Dup")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "ambiguous" in result["error"]["message"].lower()


@pytest.mark.asyncio()
async def test_inspect_unknown_name_returns_error(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """No matches on a name surfaces a 'not found' error."""
    tools = _register_and_get_tools(settings, auth_provider)
    client = _make_client_mock(find_flows_by_name=[])
    with _patch_client(client):
        raw = await tools["flow"](action="inspect", name="ghost")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "no flow" in result["error"]["message"].lower()


@pytest.mark.asyncio()
async def test_inspect_missing_flow_returns_error(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """A 404 from ``get_flow_by_sys_id`` becomes a structured error."""
    tools = _register_and_get_tools(settings, auth_provider)
    kwargs = _empty_inspect_kwargs()
    kwargs["get_flow_by_sys_id"] = None
    client = _make_client_mock(**kwargs)
    with _patch_client(client):
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW, sections="*")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "not found" in result["error"]["message"].lower()


# ---------------------------------------------------------------------------
# inspect: happy path + canvas assembly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_inspect_happy_path_assembles_canvas(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """A flow with one V2 trigger, one V2 action, one V2 logic block builds correctly."""
    tools = _register_and_get_tools(settings, auth_provider)

    action_type_id = "atype_001"
    trigger_remote_id = "rt_001"

    actions_v2 = [
        {
            "sys_id": _ref("a1"),
            "ui_uuid": _ref("ui_a1"),
            "parent_ui_uuid": _ref(""),
            "order": _ref("100"),
            "label": _ref("Create incident"),
            "name": _ref(""),
            "comment": _ref(""),
            "action_type": _ref(action_type_id, "Create Record"),
            "values": _ref(_encode_values([{"k": "v"}])),
        },
    ]
    logic_v2 = [
        {
            "sys_id": _ref("l1"),
            "ui_uuid": _ref("ui_l1"),
            "parent_ui_uuid": _ref(""),
            "order": _ref("200"),
            "label": _ref("If"),
            "name": _ref(""),
            "comment": _ref(""),
            "logic_definition": _ref("if_def", "If"),
            "values": _ref(""),
        },
    ]
    triggers_v2 = [
        {
            "sys_id": _ref("t1"),
            "type": _ref("record_update"),
            "active": _ref("true"),
            "table": _ref("incident"),
            "remote_trigger_id": _ref(trigger_remote_id),
            "values": _ref(""),
        },
    ]
    record_triggers = [
        {"sys_id": _ref(trigger_remote_id), "condition": _ref("active=true")},
    ]
    action_types = [
        {
            "sys_id": _ref(action_type_id),
            "name": _ref("Create Record"),
            "internal_name": _ref("create_record"),
            "sys_scope": _ref("global", "Global"),
        },
    ]
    client = _make_client_mock(
        get_flow_by_sys_id=_minimal_flow_header(),
        list_flow_inputs=[],
        list_flow_outputs=[],
        list_flow_variables=[],
        list_action_instances_v2=actions_v2,
        list_action_instances_v1=[],
        list_logic_instances_v2=logic_v2,
        list_logic_instances_v1=[],
        list_trigger_instances_v2=triggers_v2,
        list_trigger_instances_v1=[],
        list_record_triggers=record_triggers,
        get_action_type_definitions=action_types,
        list_v1_variable_values=[],
    )

    with _patch_client(client):
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW, sections="*")
    result = decode_response(raw)

    assert result["status"] == "success"
    data = result["data"]

    # published_state shape
    pub = data["published_state"]
    assert pub["master_snapshot"] == "snap1"
    assert pub["latest_snapshot"] == "snap1"
    assert pub["drift"] is False

    # canvas: two roots (action + logic), each with empty parent_ui_id
    canvas = data["canvas"]
    assert len(canvas) == 2
    parent_ids = {node["parent_ui_id"] for node in canvas}
    assert parent_ids == {""}

    # Triggers stitched with record-trigger condition
    triggers = data["triggers"]
    assert len(triggers) == 1
    assert triggers[0]["version"] == "v2"
    assert triggers[0]["condition"] == "active=true"

    # No V1 + V2 mix, no drift, no v1 logic, no spoke -> empty warnings
    assert data["warnings"] == []
    assert result["selection"]["mode"] == "all"
    assert result["selection"]["omitted_sections"] == []
    assert set(result["selection"]["returned_sections"]) == {
        "flow",
        "published_state",
        "structural_summary",
        "inputs",
        "outputs",
        "variables",
        "triggers",
        "canvas",
        "v1_actions",
        "v1_variable_values",
        "warnings",
    }


@pytest.mark.asyncio()
async def test_inspect_snapshot_drift_emits_warning(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Diverging master/latest snapshots set ``drift=True`` and emit a warning."""
    tools = _register_and_get_tools(settings, auth_provider)
    header = _minimal_flow_header()
    header["master_snapshot"] = _ref("snapA")
    header["latest_snapshot"] = _ref("snapB")
    kwargs = _empty_inspect_kwargs()
    kwargs["get_flow_by_sys_id"] = header
    client = _make_client_mock(**kwargs)

    with _patch_client(client):
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW, sections="*")
    result = decode_response(raw)

    assert result["status"] == "success"
    assert result["data"]["published_state"]["drift"] is True
    assert any("snapshot drift" in w for w in result["data"]["warnings"])


@pytest.mark.asyncio()
async def test_inspect_decode_failure_resilient(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """A malformed ``values`` blob attaches ``decode_error`` but keeps overall success."""
    tools = _register_and_get_tools(settings, auth_provider)
    actions_v2 = [
        {
            "sys_id": _ref("a1"),
            "ui_uuid": _ref("ui_a1"),
            "parent_ui_uuid": _ref(""),
            "order": _ref("100"),
            "label": _ref("Bad"),
            "name": _ref(""),
            "comment": _ref(""),
            "action_type": _ref("atype_x"),
            # Looks compressed but isn't valid base64
            "values": _ref("H4sIA!!!not-base64!!!"),
        },
    ]
    kwargs = _empty_inspect_kwargs()
    kwargs["list_action_instances_v2"] = actions_v2
    client = _make_client_mock(**kwargs)

    with _patch_client(client):
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW, sections="*")
    result = decode_response(raw)

    assert result["status"] == "success"
    canvas = result["data"]["canvas"]
    assert len(canvas) == 1
    assert "decode_error" in canvas[0]
    assert "base64" in canvas[0]["decode_error"]


@pytest.mark.asyncio()
async def test_contract_returns_concise_configured_bindings(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    """``contract`` retains configured values and data pills without raw Flow Designer metadata."""
    tools = _register_and_get_tools(settings, auth_provider)
    actions_v2 = [
        {
            "sys_id": _ref("a1"),
            "ui_uuid": _ref("ui_a1"),
            "parent_ui_uuid": _ref(""),
            "order": _ref("100"),
            "label": _ref("Look up user"),
            "name": _ref(""),
            "comment": _ref(""),
            "action_type": _ref("atype_lookup", "Look up User"),
            "values": _ref(
                _encode_values(
                    [
                        {
                            "name": "user_principal_name",
                            "value": "{{subflow.user}}",
                            "parameter": {
                                "label": "User Principal Name",
                                "type": "string",
                                "mandatory": True,
                            },
                        },
                    ],
                ),
            ),
        },
    ]
    logic_v2 = [
        {
            "sys_id": _ref("l1"),
            "ui_uuid": _ref("ui_l1"),
            "parent_ui_uuid": _ref(""),
            "order": _ref("200"),
            "label": _ref("Unable to look up user"),
            "name": _ref(""),
            "comment": _ref(""),
            "logic_definition": _ref("if_def", "If"),
            "values": _ref(
                _encode_values(
                    {
                        "inputs": [
                            {
                                "name": "condition",
                                "value": "{{lookup.__action_status__.code}}>0",
                                "parameter": {"label": "Condition", "type": "string", "mandatory": True},
                            },
                        ],
                        "outputsToAssign": [
                            {
                                "name": "message",
                                "value": "Unable to look up {{subflow.user}}",
                                "parameter": {"label": "message", "type": "string", "mandatory": False},
                            },
                        ],
                    },
                ),
            ),
        },
    ]
    inputs = [
        {
            "element": _ref("user"),
            "label": _ref("User"),
            "internal_type": _ref("string"),
            "mandatory": _ref("true"),
            "default_value": _ref(""),
            "reference": _ref(""),
        },
    ]
    outputs = [
        {
            "element": _ref("message"),
            "label": _ref("Message"),
            "internal_type": _ref("string"),
            "mandatory": _ref("false"),
            "default_value": _ref(""),
            "reference": _ref(""),
        },
    ]
    action_types = [
        {
            "sys_id": _ref("atype_lookup"),
            "name": _ref("Look up User"),
            "internal_name": _ref("look_up_user"),
            "sys_scope": _ref("global", "Global"),
            "category": _ref("User Management"),
        },
    ]
    action_inputs = [
        {
            "action_type": _ref("atype_lookup", "Look up User"),
            "name": _ref("user_principal_name"),
            "label": _ref("User Principal Name"),
            "element_prototype": _ref("prototype_string", "String"),
            "mandatory": _ref("true"),
            "default_value": _ref("guest@example.com"),
            "reference": _ref(""),
        },
    ]
    action_outputs = [
        {
            "action_type": _ref("atype_lookup", "Look up User"),
            "name": _ref("user"),
            "label": _ref("User"),
            "element_prototype": _ref("prototype_reference", "Reference"),
            "mandatory": _ref("false"),
            "reference": _ref("sys_user"),
        },
        {
            "action_type": _ref("atype_lookup", "Look up User"),
            "name": _ref("status"),
            "label": _ref("Status"),
            "element_prototype": "prototype_without_display_label",
            "mandatory": _ref("false"),
            "reference": _ref(""),
        },
    ]
    kwargs = _empty_inspect_kwargs()
    kwargs.update(
        list_flow_inputs=inputs,
        list_flow_outputs=outputs,
        list_action_instances_v2=actions_v2,
        list_logic_instances_v2=logic_v2,
        get_action_type_definitions=action_types,
        list_action_input_definitions=action_inputs,
        list_action_output_definitions=action_outputs,
    )
    client = _make_client_mock(**kwargs)

    with _patch_client(client):
        raw = await tools["flow"](action="contract", sys_id=SYS_ID_FLOW, sections="*")
    result = decode_response(raw)

    assert result["status"] == "success"
    data = result["data"]
    assert data["inputs"] == [{"name": "user", "label": "User", "type": "string", "required": True}]
    assert data["outputs"] == [{"name": "message", "label": "Message", "type": "string", "required": False}]
    assert "canvas" not in data
    assert data["steps"] == [
        {
            "step": "1",
            "kind": "action",
            "order": "100",
            "label": "Look up user",
            "action": {
                "name": "Look up User",
                "internal_name": "look_up_user",
                "category": "User Management",
                "scope": "Global",
            },
            "definition": {
                "inputs": [
                    {
                        "name": "user_principal_name",
                        "label": "User Principal Name",
                        "required": True,
                        "type": "String",
                        "default": "guest@example.com",
                    },
                ],
                "outputs": [
                    {
                        "name": "user",
                        "label": "User",
                        "required": False,
                        "type": "Reference",
                        "reference_table": "sys_user",
                    },
                    {
                        "name": "status",
                        "label": "Status",
                        "required": False,
                    },
                ],
            },
            "inputs": [
                {
                    "name": "user_principal_name",
                    "label": "User Principal Name",
                    "type": "string",
                    "required": True,
                    "value": "{{subflow.user}}",
                    "data_pills": ["subflow.user"],
                },
            ],
        },
        {
            "step": "2",
            "kind": "logic",
            "order": "200",
            "label": "Unable to look up user",
            "logic": {"name": "If"},
            "conditions": [
                {
                    "name": "condition",
                    "label": "Condition",
                    "type": "string",
                    "required": True,
                    "value": "{{lookup.__action_status__.code}}>0",
                    "data_pills": ["lookup.__action_status__.code"],
                },
            ],
            "output_assignments": [
                {
                    "name": "message",
                    "label": "message",
                    "type": "string",
                    "required": False,
                    "value": "Unable to look up {{subflow.user}}",
                    "data_pills": ["subflow.user"],
                },
            ],
        },
    ]


@pytest.mark.parametrize(
    ("lookup_method", "lookup_label", "failure"),
    [
        (
            "list_action_input_definitions",
            "input",
            httpx.ConnectError("definition table connection failed"),
        ),
        (
            "list_action_input_definitions",
            "input",
            json.JSONDecodeError("invalid definition response", "", 0),
        ),
        (
            "list_action_output_definitions",
            "output",
            httpx.ConnectError("definition table connection failed"),
        ),
        (
            "list_action_output_definitions",
            "output",
            json.JSONDecodeError("invalid definition response", "", 0),
        ),
    ],
)
@pytest.mark.asyncio()
async def test_contract_limits_action_definition_lookup_failures_to_schema_warning(
    settings: Settings,
    auth_provider: BasicAuthProvider,
    lookup_method: str,
    lookup_label: str,
    failure: Exception,
) -> None:
    """Transport and parsing failures leave configured action bindings usable."""
    actions_v2 = [
        {
            "sys_id": _ref("a1"),
            "ui_uuid": _ref("ui_a1"),
            "parent_ui_uuid": _ref(""),
            "order": _ref("100"),
            "label": _ref("Action"),
            "name": _ref(""),
            "comment": _ref(""),
            "action_type": _ref("atype_x", "Action X"),
            "values": _ref(
                _encode_values(
                    [
                        {
                            "name": "record",
                            "value": "{{flow.record}}",
                            "parameter": {"label": "Record", "type": "reference", "mandatory": True},
                        },
                    ]
                )
            ),
        },
    ]
    kwargs = _empty_inspect_kwargs()
    kwargs.update(
        list_action_instances_v2=actions_v2,
        get_action_type_definitions=[{"sys_id": _ref("atype_x"), "name": _ref("Action X")}],
    )
    client = _make_client_mock(**kwargs)
    getattr(client, lookup_method).side_effect = failure
    tools = _register_and_get_tools(settings, auth_provider)

    with _patch_client(client):
        raw = await tools["flow"](action="contract", sys_id=SYS_ID_FLOW, sections="*")
    result = decode_response(raw)

    assert result["status"] == "success"
    step = result["data"]["steps"][0]
    assert step["inputs"] == [
        {
            "name": "record",
            "label": "Record",
            "type": "reference",
            "required": True,
            "value": "{{flow.record}}",
            "data_pills": ["flow.record"],
        }
    ]
    definition = step["definition"]
    assert definition["inputs"] == []
    assert definition["outputs"] == []
    assert definition["limitations"] == [f"Action {lookup_label} definitions are unavailable: {failure}"]
    assert result["data"]["warnings"][-1] == definition["limitations"][0]


@pytest.mark.asyncio()
async def test_inspect_does_not_fetch_action_field_definitions(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    """The existing inspect action keeps its prior lookup and response behavior."""
    kwargs = _empty_inspect_kwargs()
    client = _make_client_mock(**kwargs)
    tools = _register_and_get_tools(settings, auth_provider)

    with _patch_client(client):
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW, sections="*")

    assert decode_response(raw)["status"] == "success"
    client.list_action_input_definitions.assert_not_awaited()
    client.list_action_output_definitions.assert_not_awaited()


@pytest.mark.asyncio()
async def test_contract_warns_when_v1_actions_cannot_be_reconstructed(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    """V1 actions must not be silently omitted from a contract's ordered V2 steps."""
    tools = _register_and_get_tools(settings, auth_provider)
    kwargs = _empty_inspect_kwargs()
    kwargs.update(
        list_action_instances_v1=[{"sys_id": _ref("v1_action")}],
        list_v1_variable_values=[{"document": _ref("v1_action")}],
    )
    client = _make_client_mock(**kwargs)

    with _patch_client(client):
        raw = await tools["flow"](action="contract", sys_id=SYS_ID_FLOW, sections="*")
    result = decode_response(raw)

    assert result["status"] == "success"
    assert result["data"]["steps"] == []
    assert any("V1 action instance" in warning for warning in result["data"]["warnings"])


# ---------------------------------------------------------------------------
# find_by_table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_find_by_table_happy_path(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """find_by_table merges V1 + V2 trigger rows and returns deduplicated flow count."""
    tools = _register_and_get_tools(settings, auth_provider)

    flow_v2_id = "v2flow"
    flow_v1_id = "v1flow"
    record_trig_id = "rt_001"

    record_triggers = [{"sys_id": _ref(record_trig_id), "condition": _ref("active=true")}]
    v1_triggers = [
        {
            "sys_id": _ref("t_v1"),
            "type": _ref("record_update"),
            "active": _ref("true"),
            "table": _ref("incident"),
            "condition": _ref("priority=1"),
            "flow": _ref(flow_v1_id, "V1 Flow"),
        },
    ]
    v2_triggers = [
        {
            "sys_id": _ref("t_v2"),
            "type": _ref("record_update"),
            "active": _ref("true"),
            "table": _ref(""),
            "remote_trigger_id": _ref(record_trig_id),
            "flow": _ref(flow_v2_id, "V2 Flow"),
        },
    ]
    flows_meta = [
        {
            "sys_id": _ref(flow_v1_id),
            "name": _ref("V1 Flow"),
            "type": _ref("flow"),
            "active": _ref("true"),
            "sys_scope": _ref("global", "Global"),
        },
        {
            "sys_id": _ref(flow_v2_id),
            "name": _ref("V2 Flow"),
            "type": _ref("flow"),
            "active": _ref("true"),
            "sys_scope": _ref("global", "Global"),
        },
    ]

    client = _make_client_mock(
        find_record_triggers_by_table=record_triggers,
        list_v1_triggers_by_table=v1_triggers,
        list_v2_triggers_by_remote_ids=v2_triggers,
        get_flows_bulk=flows_meta,
    )

    with _patch_client(client):
        raw = await tools["flow"](action="find_by_table", table="incident")
    result = decode_response(raw)

    assert result["status"] == "success"
    data = result["data"]
    assert data["table"] == "incident"
    assert data["v1_count"] == 1
    assert data["v2_count"] == 1
    assert data["total"] == 2
    versions = sorted(f["version"] for f in data["flows"])
    assert versions == ["v1", "v2"]


@pytest.mark.asyncio()
async def test_find_by_table_missing_table_returns_error(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """``find_by_table`` requires a table name."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["flow"](action="find_by_table")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "table" in result["error"]["message"].lower()


# ---------------------------------------------------------------------------
# list_triggers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_list_triggers_happy_path(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """``list_triggers`` returns combined V2+V1 with flow names resolved."""
    tools = _register_and_get_tools(settings, auth_provider)

    flow_v2_id = "v2flow"
    flow_v1_id = "v1flow"

    filtered = {
        "v2": [
            {
                "sys_id": _ref("t_v2"),
                "type": _ref("record_update"),
                "active": _ref("true"),
                "table": _ref("incident"),
                "flow": _ref(flow_v2_id, "V2 Flow"),
                "values": _ref(""),
            },
        ],
        "v1": [
            {
                "sys_id": _ref("t_v1"),
                "type": _ref("record_update"),
                "active": _ref("true"),
                "table": _ref("incident"),
                "condition": _ref(""),
                "flow": _ref(flow_v1_id, "V1 Flow"),
            },
        ],
    }
    flows_meta = [
        {"sys_id": _ref(flow_v2_id), "name": _ref("V2 Flow")},
        {"sys_id": _ref(flow_v1_id), "name": _ref("V1 Flow")},
    ]

    client = _make_client_mock(
        list_triggers_filtered=filtered,
        get_flows_bulk=flows_meta,
    )

    with _patch_client(client):
        raw = await tools["flow"](action="list_triggers", trigger_type="record_update")
    result = decode_response(raw)

    assert result["status"] == "success"
    data = result["data"]
    assert data["v1_count"] == 1
    assert data["v2_count"] == 1
    triggers = data["triggers"]
    assert {t["flow"]["name"] for t in triggers} == {"V1 Flow", "V2 Flow"}
    assert result["pagination"]["limit"] == 100  # default


@pytest.mark.asyncio()
async def test_list_triggers_invalid_active_returns_error(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """``active`` must be 'true' or 'false' when supplied."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["flow"](action="list_triggers", active="maybe")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "active" in result["error"]["message"]


# ---------------------------------------------------------------------------
# Unknown action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_unknown_action_returns_error(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["flow"](action="bogus")
    result = decode_response(raw)

    assert result["status"] == "error"
    assert "bogus" in result["error"]["message"]


@pytest.mark.asyncio()
async def test_inspect_rejects_invalid_sys_id_without_io(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Malformed sys_id on action='inspect' returns a structured error WITHOUT any HTTP I/O."""
    client = AsyncMock()
    tools = _register_and_get_tools(settings, auth_provider)

    with _patch_client(client):
        raw = await tools["flow"](action="inspect", sys_id="not-a-sys-id")

    result = decode_response(raw)
    assert result["status"] == "error"
    assert "Invalid sys_id" in result["error"]["message"]
    # Validation must happen BEFORE opening the client; no flow methods should be called.
    client.get_flow_by_sys_id.assert_not_called()
    client.find_flows_by_name.assert_not_called()
