"""Tests for the unified ``flow`` tool group (Phase 3 - Flow Designer)."""

from __future__ import annotations

import base64
import gzip
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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


def _encode_trigger_inputs(entries: list[dict[str, Any]]) -> str:
    """Build a gzip+base64+json ``trigger_inputs`` blob for V2 trigger fixtures."""
    return _encode_values(entries)


def _record_trigger_inputs(table: str, condition: str, *, table_display: str = "") -> str:
    """Convenience: encode a record-trigger ``trigger_inputs`` blob with table+condition entries."""
    return _encode_trigger_inputs(
        [
            {"id": "i1", "name": "table", "value": table, "displayValue": table_display or table},
            {"id": "i2", "name": "condition", "value": condition, "displayValue": condition},
        ]
    )


def _ref(value: str, display: str = "") -> dict[str, str]:
    """Mimic a display_value=all reference field shape."""
    return {"value": value, "display_value": display or value}


SYS_ID_FLOW = "f" * 32


# ---------------------------------------------------------------------------
# describe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_describe_returns_all_action_keys(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """``describe`` advertises all five flow actions."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["flow"](action="describe")
    result = decode_response(raw)

    assert result["status"] == "success"
    actions = result["data"]["actions"]
    for name in ("inspect", "find_by_table", "decode_values", "list_triggers", "describe"):
        assert name in actions


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
        "get_action_type_definitions": [],
        "get_logic_definitions": [],
        "list_v1_variable_values": [],
    }


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
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW)
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
    logic_def_id = "if_def_001"

    actions_v2 = [
        {
            "sys_id": _ref("a1"),
            "ui_id": _ref("ui_a1"),
            "parent_ui_id": _ref(""),
            "order": _ref("100"),
            "comment": _ref(""),
            "action_type": _ref(action_type_id, "Create Record"),
            "values": _ref(_encode_values([{"k": "v"}])),
        },
    ]
    logic_v2 = [
        {
            "sys_id": _ref("l1"),
            "ui_id": _ref("ui_l1"),
            "parent_ui_id": _ref(""),
            "order": _ref("200"),
            "comment": _ref(""),
            "logic_definition": _ref(logic_def_id, "If"),
            "values": _ref(""),
        },
    ]
    triggers_v2 = [
        {
            "sys_id": _ref("t1"),
            "trigger_type": _ref("record_update"),
            "name": _ref("Record Updated"),
            "trigger_inputs": _ref(_record_trigger_inputs("incident", "active=true")),
        },
    ]
    action_types = [
        {
            "sys_id": _ref(action_type_id),
            "name": _ref("Create Record"),
            "internal_name": _ref("create_record"),
            "sys_scope": _ref("global", "Global"),
        },
    ]
    logic_defs = [{"sys_id": _ref(logic_def_id), "name": _ref("If")}]

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
        get_action_type_definitions=action_types,
        get_logic_definitions=logic_defs,
        list_v1_variable_values=[],
    )

    with _patch_client(client):
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW)
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

    # Labels derive from action_type / logic_definition lookups.
    by_kind = {node["kind"]: node for node in canvas}
    assert by_kind["action"]["label"] == "Create Record"
    assert by_kind["logic"]["label"] == "If"

    # Triggers carry the decoded trigger_inputs projection.
    triggers = data["triggers"]
    assert len(triggers) == 1
    trig = triggers[0]
    assert trig["version"] == "v2"
    assert trig["type"] == "record_update"
    assert trig["table"] == "incident"
    assert trig["condition"] == "active=true"

    # No V1 + V2 mix, no drift, no v1 logic, no spoke -> empty warnings
    assert data["warnings"] == []


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
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW)
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
            "ui_id": _ref("ui_a1"),
            "parent_ui_id": _ref(""),
            "order": _ref("100"),
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
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW)
    result = decode_response(raw)

    assert result["status"] == "success"
    canvas = result["data"]["canvas"]
    assert len(canvas) == 1
    assert "decode_error" in canvas[0]
    assert "base64" in canvas[0]["decode_error"]


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


# ---------------------------------------------------------------------------
# inspect: payload trim (boilerplate calculation, empty v1 omission)
# ---------------------------------------------------------------------------


_CALC_BOILERPLATE = (
    "(function calculatedFieldValue(current) {\n\n\t// Add your code here"
    "\n\treturn '';  // return the calculated value\n\n})(current);"
)


@pytest.mark.asyncio()
async def test_inspect_strips_boilerplate_calculation(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Verbatim ``calculation`` boilerplate is dropped; custom calculation is preserved."""
    tools = _register_and_get_tools(settings, auth_provider)
    inputs = [
        {"sys_id": _ref("in1"), "name": _ref("a"), "calculation": _ref(_CALC_BOILERPLATE)},
        {"sys_id": _ref("in2"), "name": _ref("b"), "calculation": _ref("return current.number;")},
    ]
    kwargs = _empty_inspect_kwargs()
    kwargs["list_flow_inputs"] = inputs
    client = _make_client_mock(**kwargs)

    with _patch_client(client):
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW)
    data = decode_response(raw)["data"]

    assert "calculation" not in data["inputs"][0]
    assert data["inputs"][1]["calculation"] == "return current.number;"


@pytest.mark.asyncio()
async def test_inspect_omits_empty_v1_arrays(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """``v1_actions`` / ``v1_variable_values`` are dropped from the response when both are empty."""
    tools = _register_and_get_tools(settings, auth_provider)
    client = _make_client_mock(**_empty_inspect_kwargs())

    with _patch_client(client):
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW)
    data = decode_response(raw)["data"]

    assert "v1_actions" not in data
    assert "v1_variable_values" not in data


@pytest.mark.asyncio()
async def test_inspect_includes_v1_when_present(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """``v1_actions`` is surfaced when the flow has at least one V1 action."""
    tools = _register_and_get_tools(settings, auth_provider)
    kwargs = _empty_inspect_kwargs()
    kwargs["list_action_instances_v1"] = [{"sys_id": _ref("av1"), "name": _ref("legacy")}]
    client = _make_client_mock(**kwargs)

    with _patch_client(client):
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW)
    data = decode_response(raw)["data"]

    assert data["v1_actions"][0]["sys_id"] == "av1"


# ---------------------------------------------------------------------------
# inspect: datapill ref resolution
# ---------------------------------------------------------------------------


_UUID_A = "11111111-1111-1111-1111-111111111111"
_UUID_B = "22222222-2222-2222-2222-222222222222"
_UUID_GHOST = "ffffffff-ffff-ffff-ffff-ffffffffffff"


def _action_row(
    *,
    sys_id: str,
    ui_id: str,
    order: str,
    values: Any = "",
    parent_ui_id: str = "",
    action_type: str = "atype_x",
    action_type_label: str = "Create Record",
) -> dict[str, Any]:
    """Build a minimal V2 action-instance row for tests.

    V2 rows expose ``ui_id``/``parent_ui_id`` (not ``ui_uuid``) and have
    no ``name``/``label``/``active`` columns - the visible label comes
    from the action-type lookup.
    """
    return {
        "sys_id": _ref(sys_id),
        "ui_id": _ref(ui_id),
        "parent_ui_id": _ref(parent_ui_id),
        "order": _ref(order),
        "comment": _ref(""),
        "action_type": _ref(action_type, action_type_label),
        "values": _ref(values),
    }


@pytest.mark.asyncio()
async def test_inspect_attaches_resolved_datapill_refs(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """A consumer node's decoded payload referencing a producer's ui_id yields a resolved ref."""
    tools = _register_and_get_tools(settings, auth_provider)
    producer_blob = _encode_values([{"k": "static"}])
    consumer_blob = _encode_values([{"target": f"{{{{{_UUID_A}.number}}}}"}])
    actions_v2 = [
        _action_row(sys_id="a1", ui_id=_UUID_A, order="100", values=producer_blob, action_type_label="Producer"),
        _action_row(sys_id="a2", ui_id=_UUID_B, order="200", values=consumer_blob, action_type_label="Consumer"),
    ]
    kwargs = _empty_inspect_kwargs()
    kwargs["list_action_instances_v2"] = actions_v2
    client = _make_client_mock(**kwargs)

    with _patch_client(client):
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW)
    data = decode_response(raw)["data"]

    consumer = next(n for n in data["canvas"] if n["sys_id"] == "a2")
    refs = consumer["datapill_refs"]
    assert len(refs) == 1
    assert refs[0]["resolved"] is True
    assert refs[0]["producer_ui_id"] == _UUID_A
    assert refs[0]["producer_sys_id"] == "a1"
    assert refs[0]["producer_name"] == "Producer"
    assert refs[0]["field"] == "number"


@pytest.mark.asyncio()
async def test_inspect_resolves_datapill_ref_case_insensitively(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    """An uppercase-UUID reference resolves against a lowercase canvas ``ui_id`` without warning."""
    tools = _register_and_get_tools(settings, auth_provider)
    upper_ref = _UUID_A.upper()
    producer_blob = _encode_values([{"k": "static"}])
    consumer_blob = _encode_values([{"target": f"{{{{{upper_ref}.number}}}}"}])
    actions_v2 = [
        _action_row(sys_id="a1", ui_id=_UUID_A, order="100", values=producer_blob, action_type_label="Producer"),
        _action_row(sys_id="a2", ui_id=_UUID_B, order="200", values=consumer_blob, action_type_label="Consumer"),
    ]
    kwargs = _empty_inspect_kwargs()
    kwargs["list_action_instances_v2"] = actions_v2
    client = _make_client_mock(**kwargs)

    with _patch_client(client):
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW)
    data = decode_response(raw)["data"]

    consumer = next(n for n in data["canvas"] if n["sys_id"] == "a2")
    refs = consumer["datapill_refs"]
    assert len(refs) == 1
    assert refs[0]["resolved"] is True
    assert refs[0]["producer_sys_id"] == "a1"
    # Original-case literal is preserved in the returned ``ref`` field.
    assert upper_ref in refs[0]["ref"]
    assert not any("unresolved_datapill_ref" in w for w in data["warnings"])


@pytest.mark.asyncio()
async def test_inspect_unresolved_datapill_emits_warning(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """A reference to a producer not on the canvas emits one ``unresolved_datapill_ref`` warning."""
    tools = _register_and_get_tools(settings, auth_provider)
    consumer_blob = _encode_values([{"target": f"{{{{{_UUID_GHOST}.foo}}}}"}])
    actions_v2 = [_action_row(sys_id="a2", ui_id=_UUID_B, order="100", values=consumer_blob)]
    kwargs = _empty_inspect_kwargs()
    kwargs["list_action_instances_v2"] = actions_v2
    client = _make_client_mock(**kwargs)

    with _patch_client(client):
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW)
    data = decode_response(raw)["data"]

    consumer = data["canvas"][0]
    assert consumer["datapill_refs"][0]["resolved"] is False
    matches = [w for w in data["warnings"] if "unresolved_datapill_ref" in w]
    assert len(matches) == 1
    assert _UUID_GHOST in matches[0]


# ---------------------------------------------------------------------------
# inspect: warnings (kept after the V2-schema repair)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_inspect_step_decode_failure_warning(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """At least one node with ``decode_error`` aggregates into a ``step_decode_failure`` warning."""
    tools = _register_and_get_tools(settings, auth_provider)
    actions_v2 = [_action_row(sys_id="a1", ui_id="u1", order="100", values="H4sIA!!!bad!!!")]
    kwargs = _empty_inspect_kwargs()
    kwargs["list_action_instances_v2"] = actions_v2
    client = _make_client_mock(**kwargs)

    with _patch_client(client):
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW)
    data = decode_response(raw)["data"]
    assert any("step_decode_failure: 1" in w for w in data["warnings"])


# ---------------------------------------------------------------------------
# inspect: label resolution from action_type / logic_definition lookups
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_inspect_labels_from_action_type_and_logic_definition_lookups(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    """Action labels come from ``sys_hub_action_type_base``; logic labels from ``sys_hub_flow_logic_definition``."""
    tools = _register_and_get_tools(settings, auth_provider)
    actions_v2 = [
        {
            "sys_id": _ref("a1"),
            "ui_id": _ref("ui_a1"),
            "parent_ui_id": _ref(""),
            "order": _ref("100"),
            "comment": _ref(""),
            "action_type": _ref("atype_ar"),
            "values": _ref(""),
        }
    ]
    logic_v2 = [
        {
            "sys_id": _ref("l1"),
            "ui_id": _ref("ui_l1"),
            "parent_ui_id": _ref(""),
            "order": _ref("200"),
            "comment": _ref(""),
            "logic_definition": _ref("ldef_foreach"),
            "values": _ref(""),
        }
    ]
    kwargs = _empty_inspect_kwargs()
    kwargs["list_action_instances_v2"] = actions_v2
    kwargs["list_logic_instances_v2"] = logic_v2
    kwargs["get_action_type_definitions"] = [{"sys_id": _ref("atype_ar"), "name": _ref("Ask For Approval")}]
    kwargs["get_logic_definitions"] = [{"sys_id": _ref("ldef_foreach"), "name": _ref("For Each")}]
    client = _make_client_mock(**kwargs)

    with _patch_client(client):
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW)
    data = decode_response(raw)["data"]

    by_kind = {n["kind"]: n for n in data["canvas"]}
    assert by_kind["action"]["label"] == "Ask For Approval"
    assert by_kind["action"]["action_type"]["name"] == "Ask For Approval"
    assert by_kind["logic"]["label"] == "For Each"
    assert by_kind["logic"]["logic_definition"]["name"] == "For Each"


# ---------------------------------------------------------------------------
# inspect: V2 trigger projection from decoded trigger_inputs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_inspect_v2_trigger_projects_table_and_condition_from_inputs(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    """A record-trigger row projects ``table``/``condition`` from the decoded ``trigger_inputs`` blob."""
    tools = _register_and_get_tools(settings, auth_provider)
    kwargs = _empty_inspect_kwargs()
    kwargs["list_trigger_instances_v2"] = [
        {
            "sys_id": _ref("t1"),
            "trigger_type": _ref("record_create"),
            "name": _ref("Record Created"),
            "trigger_inputs": _ref(_record_trigger_inputs("incident", "state=1", table_display="Incident")),
        }
    ]
    client = _make_client_mock(**kwargs)

    with _patch_client(client):
        raw = await tools["flow"](action="inspect", sys_id=SYS_ID_FLOW)
    data = decode_response(raw)["data"]

    trig = data["triggers"][0]
    assert trig["type"] == "record_create"
    assert trig["name"] == "Record Created"
    assert trig["table"] == "incident"
    assert trig["table_display"] == "Incident"
    assert trig["condition"] == "state=1"
    assert trig["schedule"] == {}


@pytest.mark.asyncio()
async def test_summary_trigger_projects_record_trigger_inputs(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    """``summary`` flattens the V2 trigger to a single object with table/condition/schedule fields."""
    tools = _register_and_get_tools(settings, auth_provider)
    kwargs = _empty_inspect_kwargs()
    kwargs["list_trigger_instances_v2"] = [
        {
            "sys_id": _ref("t1"),
            "trigger_type": _ref("record_update"),
            "name": _ref("Record Updated"),
            "trigger_inputs": _ref(_record_trigger_inputs("incident", "active=true")),
        }
    ]
    client = _make_client_mock(**kwargs)

    with _patch_client(client):
        raw = await tools["flow"](action="summary", sys_id=SYS_ID_FLOW)
    data = decode_response(raw)["data"]

    assert data["trigger"] == {
        "type": "record_update",
        "name": "Record Updated",
        "table": "incident",
        "table_display": "incident",
        "condition": "active=true",
        "schedule": {},
    }


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_summary_happy_path_compact_projection(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """``summary`` returns compact projection with single trigger, steps, branches, counts."""
    tools = _register_and_get_tools(settings, auth_provider)
    actions_v2 = [
        _action_row(sys_id="a1", ui_id=_UUID_A, order="100", action_type_label="Producer"),
        _action_row(sys_id="a2", ui_id=_UUID_B, order="200", action_type_label="Consumer"),
    ]
    triggers_v2 = [
        {
            "sys_id": _ref("t1"),
            "trigger_type": _ref("record_update"),
            "name": _ref("Record Updated"),
            "trigger_inputs": _ref(_record_trigger_inputs("incident", "active=true")),
        }
    ]
    kwargs = _empty_inspect_kwargs()
    kwargs["list_action_instances_v2"] = actions_v2
    kwargs["list_trigger_instances_v2"] = triggers_v2
    client = _make_client_mock(**kwargs)

    with _patch_client(client):
        raw = await tools["flow"](action="summary", sys_id=SYS_ID_FLOW)
    data = decode_response(raw)["data"]

    # trigger is a single object, not a list
    assert data["trigger"]["type"] == "record_update"
    assert data["trigger"]["table"] == "incident"
    assert data["trigger"]["condition"] == "active=true"
    # steps are flat, ordered, no values_decoded
    assert [s["sys_id"] for s in data["steps"]] == ["a1", "a2"]
    assert all("values_decoded" not in s for s in data["steps"])
    # branches keep structure only
    assert {n["sys_id"] for n in data["branches"]} == {"a1", "a2"}
    assert all(set(n.keys()) == {"ui_id", "sys_id", "order", "label", "children"} for n in data["branches"])
    # counts match
    assert data["counts"]["steps"] == 2
    assert data["counts"]["actions"] == 2
    assert data["counts"]["triggers"] == 1


@pytest.mark.asyncio()
async def test_summary_datapill_graph_emits_resolved_and_unresolved(
    settings: Settings, auth_provider: BasicAuthProvider
) -> None:
    """``datapill_graph`` lists one edge per consumer ref, with producer fields populated when resolved."""
    tools = _register_and_get_tools(settings, auth_provider)
    consumer_blob = _encode_values(
        [
            {"a": f"{{{{{_UUID_A}.number}}}}", "b": f"{{{{{_UUID_GHOST}.foo}}}}"},
        ]
    )
    actions_v2 = [
        _action_row(sys_id="a1", ui_id=_UUID_A, order="100", action_type_label="Producer"),
        _action_row(sys_id="a2", ui_id=_UUID_B, order="200", values=consumer_blob),
    ]
    kwargs = _empty_inspect_kwargs()
    kwargs["list_action_instances_v2"] = actions_v2
    client = _make_client_mock(**kwargs)

    with _patch_client(client):
        raw = await tools["flow"](action="summary", sys_id=SYS_ID_FLOW)
    data = decode_response(raw)["data"]

    graph = data["datapill_graph"]
    by_uid = {edge["producer_ui_id"]: edge for edge in graph}
    assert by_uid[_UUID_A]["producer_sys_id_if_resolved"] == "a1"
    assert by_uid[_UUID_A]["consumer_step_sys_id"] == "a2"
    assert by_uid[_UUID_GHOST]["producer_sys_id_if_resolved"] == ""


@pytest.mark.asyncio()
async def test_summary_rejects_neither_sys_id_nor_name(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """``summary`` validates the identifier contract the same way as ``inspect``."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["flow"](action="summary")
    result = decode_response(raw)
    assert result["status"] == "error"
    assert "required" in result["error"]["message"].lower()


@pytest.mark.asyncio()
async def test_describe_now_includes_summary(settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """``describe`` advertises the new ``summary`` action."""
    tools = _register_and_get_tools(settings, auth_provider)
    raw = await tools["flow"](action="describe")
    assert "summary" in decode_response(raw)["data"]["actions"]
