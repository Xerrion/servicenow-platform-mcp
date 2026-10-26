"""Common inline scripts are authored text, with native modes and pinned producers."""

import copy
import json
from typing import Any

import httpx2
import pytest
from mcp.server import MCPServer

from servicenow_mcp.auth import OAuthPKCEProvider
from tests._mock_transport import http_mock
from tests.helpers import decode_response, get_tool_functions, register_mutations
from tests.test_flow_data_edits import family_definitions
from tests.test_flow_decision_blocks import DECISION
from tests.test_flow_direct_logic_validation import _decision_flow
from tests.test_flow_editing import BASE, DEFS, FLOW, LOG, base_flow, fixtures, persist
from tests.test_flow_fields import plan
from tests.test_flow_utility_blocks import logic_step, workflow_inputs
from tests.test_flow_utility_blocks import setup as utility_setup

STUDIO = f"{BASE}/api/now/processflow"
SUBFLOW = "7" * 32
SNAPSHOT = "8" * 32
INPUT = "log_message"
ORIGINAL = "return 'original source';"
AUTHORED = "throw new Error('authoring only');\nreturn fd_data._1__log.status;"


def definition(*, mandatory: bool = False) -> dict[str, Any]:
    return {
        "id": LOG,
        "name": "Log",
        "internal_name": "log",
        "state": "published",
        "is_snapshot": True,
        "entitled": True,
        "userCanRead": True,
        "security": {"can_read": True},
        "parent_action": "6" * 32,
        "inputs": [
            {
                "id": "9" * 32,
                "name": INPUT,
                "type": "string",
                "mandatory": mandatory,
                "readonly": False,
                "maxsize": 255,
                "attributes": {"is_scriptable": "true"},
                "children": [],
            }
        ],
        "outputs": [
            {"name": "status", "type": "string", "children": []},
            {
                "name": "record",
                "type": "reference",
                "reference": "sc_req_item",
                "children": [],
            },
            {
                "name": "details",
                "type": "object",
                "children": [{"name": "message", "type": "string", "children": []}],
            },
        ],
    }


def mode_flow(declared: dict[str, Any]) -> dict[str, Any]:
    flow = copy.deepcopy(base_flow())
    flow["subFlowInstances"] = []
    for step in flow["actionInstances"]:
        step["actionType"] = copy.deepcopy(declared)
        row = step["inputs"][0]
        row["parameter"] = copy.deepcopy(declared["inputs"][0])
        row["scriptActive"] = False
        row["opaque"] = {"preserve": ["native metadata", {"flag": True}]}
    return flow


def scripted(
    row: dict[str, Any],
    code: str = ORIGINAL,
    *,
    active: bool = True,
    mirror: bool = True,
) -> None:
    row["script"] = {
        INPUT: {
            "script": code,
            "scriptActive": active,
            "showScript": active,
            "opaque": {"preserve": [1, "entry metadata"]},
        }
    }
    row["scriptActive"] = active
    if mirror:
        row["scriptAsJsonString"] = json.dumps(row["script"], ensure_ascii=False)


def routes(settings, flow, declared):
    coordinator, tools = fixtures(settings, flow)
    action_read = http_mock.get(f"{STUDIO}/action/action_types/{LOG}").mock(
        side_effect=lambda _: httpx2.Response(
            200, json={"result": copy.deepcopy(declared)}
        )
    )
    http_mock.get(f"{STUDIO}/flow_logic/types").respond(
        200, json={"result": copy.deepcopy(DEFS["flow_logic"])}
    )
    http_mock.get(f"{BASE}/api/now/trigger_api/trigger_categories").respond(
        200, json={"result": copy.deepcopy(DEFS["trigger_categories"])}
    )
    return coordinator, tools, action_read


async def preview(tools, edits):
    return decode_response(
        await tools["flow_preview"](
            action="edit", sys_id=FLOW, edits=copy.deepcopy(edits)
        )
    )


def mode_edit(
    code: str | None, *, inputs: dict[str, Any] | None = None, step: str = "ui-2"
) -> dict[str, Any]:
    return {
        "op": "update_inputs",
        "step": step,
        "inputs": {} if inputs is None else inputs,
        "input_scripts": {INPUT: code},
    }


def assert_read_only_rejection(result, coordinator):
    assert result["status"] == "error", result
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)
    assert not http_mock.unexpected


@pytest.mark.parametrize("code", [AUTHORED, None, ""])
async def test_enable_basic_and_clear_preserve_native_map_fallback_and_metadata(
    settings, code
):
    declared = definition()
    flow = mode_flow(declared)
    scripted(flow["actionInstances"][1]["inputs"][0])
    before = copy.deepcopy(flow)
    _, tools, action_read = routes(settings, flow, declared)
    saved = {}

    def save(request):
        payload = json.loads(request.content)
        saved.update(copy.deepcopy(payload))
        persist(copy.deepcopy(payload))
        return httpx2.Response(200, json={"result": {"data": copy.deepcopy(payload)}})

    write = http_mock.route(method="PUT", url=f"{STUDIO}/flow").mock(side_effect=save)
    staged = await preview(tools, [mode_edit(code)])
    assert staged["status"] == "success", staged
    assert action_read.called
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=staged["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    assert write.call_count == 1
    row = saved["actionInstances"][1]["inputs"][0]
    original = before["actionInstances"][1]["inputs"][0]
    expected_map = copy.deepcopy(original["script"])
    expected_map[INPUT].update(
        script=ORIGINAL if code is None else code,
        scriptActive=bool(code),
        showScript=bool(code),
    )
    assert row["script"] == expected_map
    assert row["scriptActive"] is bool(code)
    assert json.loads(row["scriptAsJsonString"]) == expected_map
    assert row["value"] == original["value"] == "two"
    assert row["parameter"] == original["parameter"]
    assert row["opaque"] == original["opaque"]
    assert flow == before
    calls = [call.request for call in http_mock.calls]
    write_index = next(i for i, request in enumerate(calls) if request.method == "PUT")
    assert any(
        request.method == "GET"
        and request.url.path == f"/api/now/processflow/flow/{FLOW}"
        for request in calls[write_index + 1 :]
    )
    assert all(
        request.method == "GET"
        or (request.method == "PUT" and request.url.path == "/api/now/processflow/flow")
        for request in calls
    )
    assert not http_mock.unexpected


@pytest.mark.parametrize("fallback", ["", None])
async def test_active_script_allows_mandatory_blank_fallback_without_evaluating_code(
    settings, fallback
):
    declared = definition(mandatory=True)
    flow = mode_flow(declared)
    routes(settings, flow, declared)
    planned = await plan(
        settings,
        flow,
        [mode_edit(AUTHORED, inputs={INPUT: fallback})],
    )
    row = planned["payload"]["actionInstances"][1]["inputs"][0]
    assert row["value"] == fallback
    assert row["parameter"]["mandatory"] is True
    assert row["scriptActive"] is True
    assert row["script"][INPUT] == {
        "script": AUTHORED,
        "scriptActive": True,
        "showScript": True,
    }
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("code", [None, ""])
async def test_switching_mandatory_blank_fallback_to_basic_requires_value(
    settings, code
):
    declared = definition(mandatory=True)
    flow = mode_flow(declared)
    row = flow["actionInstances"][1]["inputs"][0]
    row["value"] = ""
    scripted(row)
    coordinator, tools, _ = routes(settings, flow, declared)
    rejected = await preview(tools, [mode_edit(code)])
    assert_read_only_rejection(rejected, coordinator)
    staged = await preview(tools, [mode_edit(code, inputs={INPUT: "basic fallback"})])
    assert staged["status"] == "success", staged


@pytest.mark.parametrize("kind", ["action", "subflow", "logic"])
async def test_insert_uses_common_script_modes_and_keeps_independent_input_schema(
    settings, kind
):
    declared = definition(mandatory=True)
    flow = mode_flow(declared)
    routes(settings, flow, declared)
    edit: dict[str, Any]
    subflow: dict[str, Any] | None = None
    if kind == "logic":
        edit = {
            "op": "insert_logic",
            "logic": "If",
            "after": "ui-1",
            "inputs": {"condition": "true", "condition_name": ""},
            "input_scripts": {"condition_name": "return 'native condition label';"},
        }
        collection, name = "flowLogicInstances", "condition_name"
        schema = copy.deepcopy(
            next(item for item in DEFS["flow_logic"] if item["name"] == "If")["inputs"][
                0
            ]
        )
    else:
        edit = {
            "op": f"insert_{kind}",
            "after": "ui-1",
            "inputs": {INPUT: ""},
            "input_scripts": {INPUT: "return 'native call input';"},
        }
        collection, name = (
            "actionInstances" if kind == "action" else "subFlowInstances",
            INPUT,
        )
        schema = copy.deepcopy(declared["inputs"][0])
        if kind == "action":
            edit["action_type_sys_id"] = LOG
        else:
            edit["subflow_sys_id"] = SUBFLOW
            subflow = {
                "id": SNAPSHOT,
                "parentFlow": SUBFLOW,
                "type": "subflow",
                "name": "Scripted subflow",
                "scope": "global",
                "snapshot": True,
                "masterSnapshot": True,
                "isFlowEntitled": True,
                "userCanRead": True,
                "security": {"can_read": True},
                "inputs": copy.deepcopy(declared["inputs"]),
                "outputs": [],
            }
            http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{SUBFLOW}").respond(
                200,
                json={
                    "result": {
                        "sys_id": SUBFLOW,
                        "type": "subflow",
                        "active": "true",
                        "master_snapshot": SNAPSHOT,
                        "sys_scope": "global",
                    }
                },
            )
            http_mock.get(f"{STUDIO}/flow/{SNAPSHOT}").respond(
                200, json={"result": {"data": copy.deepcopy(subflow)}}
            )
    planned = await plan(settings, flow, [edit])
    inserted = next(row for row in planned["payload"][collection] if not row.get("id"))
    row = next(item for item in inserted["inputs"] if item["name"] == name)
    assert row["value"] == ""
    assert row["parameter"] == schema
    assert row["scriptActive"] is True
    assert row["script"] == {
        name: {
            "script": edit["input_scripts"][name],
            "scriptActive": True,
            "showScript": True,
        }
    }
    if kind == "subflow":
        assert subflow is not None
        assert inserted["subflowSysId"] == SNAPSHOT
        assert inserted["subFlow"] == subflow
        for path in (
            f"/api/now/table/sys_hub_flow/{SUBFLOW}",
            f"/api/now/processflow/flow/{SNAPSHOT}",
        ):
            assert any(call.request.url.path == path for call in http_mock.calls)
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "modes",
    [
        [],
        "return 1;",
        {INPUT: True},
        {INPUT: 42},
        {INPUT: {}},
        {"": "return 1;"},
        {"missing_input": "return 1;"},
        {INPUT: "return fd_data['trigger'].current;"},
        {INPUT: "return fd_data._1__log[field];"},
        {INPUT: "const alias = fd_data; return alias._1__log.status;"},
        {INPUT: "return fd_data._1__log;"},
        {INPUT: "return fd_data._1__log.missing;"},
    ],
)
async def test_malformed_modes_computed_access_and_undeclared_paths_never_stage(
    settings, modes
):
    declared = definition()
    flow = mode_flow(declared)
    coordinator, tools, _ = routes(settings, flow, declared)
    edit = mode_edit(None)
    edit["input_scripts"] = copy.deepcopy(modes)
    result = await preview(tools, [edit])
    assert_read_only_rejection(result, coordinator)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("readonly", True),
        ("readonly", "true"),
        ("is_scriptable", False),
        ("is_scriptable", "false"),
    ],
)
async def test_readonly_and_non_scriptable_definition_metadata_reject_scripts(
    settings, field, value
):
    declared = definition()
    if field == "is_scriptable":
        declared["inputs"][0]["attributes"][field] = value
    else:
        declared["inputs"][0][field] = value
    flow = mode_flow(declared)
    coordinator, tools, _ = routes(settings, flow, declared)
    result = await preview(tools, [mode_edit("return 'not permitted';")])
    assert_read_only_rejection(result, coordinator)


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "parent_active",
        "entry_active",
        "show_script",
        "script_map",
        "code",
        "schema",
        "metadata",
        "mirror",
        "echo",
    ],
)
async def test_script_save_checks_independent_native_get_not_just_put_echo(
    settings, defect
):
    declared = definition()
    flow = mode_flow(declared)
    scripted(flow["actionInstances"][1]["inputs"][0])
    _, tools, _ = routes(settings, flow, declared)
    saved = {}

    def save(request):
        payload = json.loads(request.content)
        echo, actual = copy.deepcopy(payload), copy.deepcopy(payload)
        saved.update(copy.deepcopy(payload))
        row = actual["actionInstances"][1]["inputs"][0]
        if defect == "parent_active":
            row["scriptActive"] = False
        elif defect == "entry_active":
            row["script"][INPUT]["scriptActive"] = False
        elif defect == "show_script":
            row["script"][INPUT]["showScript"] = False
        elif defect == "script_map":
            row["script"] = {}
        elif defect == "code":
            row["script"][INPUT]["script"] += "\n// server changed source"
        elif defect == "schema":
            row["parameter"]["attributes"]["is_scriptable"] = "false"
        elif defect == "metadata":
            row["script"][INPUT]["opaque"]["preserve"] = ["server changed metadata"]
        elif defect == "mirror":
            row["scriptAsJsonString"] = "{}"
        elif defect == "echo":
            echo_row = echo["actionInstances"][1]["inputs"][0]
            echo_row["script"][INPUT]["script"] = "return 'echo drift';"
        persist(copy.deepcopy(actual))
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{STUDIO}/flow").mock(side_effect=save)
    staged = await preview(tools, [mode_edit(AUTHORED)])
    assert staged["status"] == "success", staged
    applied = decode_response(
        await tools["mutation_apply"](preview_token=staged["data"]["preview_token"])
    )
    if defect:
        assert applied["error"]["code"] == "UNKNOWN_OUTCOME", applied
    else:
        assert applied["status"] == "success", applied
    assert write.call_count == 1
    assert (
        saved["actionInstances"][1]["inputs"][0]["script"][INPUT]["script"] == AUTHORED
    )
    if defect != "echo":
        calls = [call.request for call in http_mock.calls]
        write_index = next(
            i for i, request in enumerate(calls) if request.method == "PUT"
        )
        assert any(
            request.method == "GET"
            and request.url.path == f"/api/now/processflow/flow/{FLOW}"
            for request in calls[write_index + 1 :]
        )


@pytest.mark.parametrize("when", ["preview", "apply"])
@pytest.mark.parametrize("authored", [False, True])
async def test_independent_definition_drift_invalidates_authored_or_present_scripts(
    settings, when, authored
):
    declared = definition()
    flow = mode_flow(declared)
    if not authored:
        scripted(flow["actionInstances"][1]["inputs"][0])
    coordinator, tools, action_read = routes(settings, flow, declared)
    write = http_mock.route(method="PUT", url=f"{STUDIO}/flow")
    edits = (
        [mode_edit(AUTHORED)]
        if authored
        else [{"op": "move_step", "step": "ui-3", "before": "ui-2"}]
    )
    if when == "preview":
        declared["inputs"][0]["maxsize"] = 254
        result = await preview(tools, edits)
        assert_read_only_rejection(result, coordinator)
    else:
        staged = await preview(tools, edits)
        assert staged["status"] == "success", staged
        declared["inputs"][0]["maxsize"] = 254
        result = decode_response(
            await tools["mutation_apply"](preview_token=staged["data"]["preview_token"])
        )
        assert result["error"]["code"] == "STALE_PREVIEW", result
        assert len(coordinator.store) == 0
    assert action_read.called
    assert not write.called
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("operation", ["insert", "move"])
@pytest.mark.parametrize("mirror", [False, True])
async def test_structural_edit_remaps_only_executable_producer_tokens_by_identity(
    settings, operation, mirror
):
    declared = definition()
    flow = mode_flow(declared)
    source = (
        "// fd_data._1__log.status\r\n"
        'const literal = "fd_data._1__log.status";\n'
        "const single = 'fd_data._1__log.status';\n"
        "/* fd_data._1__log.status */\n"
        r"const pattern = /fd_data\._1__log\.status/g;"
        "\n"
        "return fd_data /* producer */ . _1__log .status + fd_data._1__log.status;"
    )
    scripted(flow["actionInstances"][1]["inputs"][0], source, mirror=mirror)
    before = copy.deepcopy(flow)
    routes(settings, flow, declared)
    edits = (
        [
            {
                "op": "insert_action",
                "action_type_sys_id": LOG,
                "before": "ui-1",
                "inputs": {INPUT: "inserted"},
            }
        ]
        if operation == "insert"
        else [{"op": "move_step", "step": "ui-3", "before": "ui-1"}]
    )
    planned = await plan(settings, flow, edits)
    actions = planned["payload"]["actionInstances"]
    producer = next(step for step in actions if step.get("id") == "1" * 32)
    consumer = next(step for step in actions if step.get("id") == "2" * 32)
    assert producer["order"] == 2
    row = consumer["inputs"][0]
    expected = source.replace(
        "return fd_data /* producer */ . _1__log .status + fd_data._1__log.status;",
        "return fd_data /* producer */ . _2__log .status + fd_data._2__log.status;",
    )
    assert row["script"][INPUT]["script"] == expected
    original = before["actionInstances"][1]["inputs"][0]
    assert row["script"][INPUT]["opaque"] == original["script"][INPUT]["opaque"]
    assert row["scriptActive"] is True
    if mirror:
        assert json.loads(row["scriptAsJsonString"]) == row["script"]
    else:
        assert "scriptAsJsonString" not in row
    assert flow == before
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("code", [None, ""])
async def test_deletion_requires_clearing_source_not_merely_disabling_script(
    settings, code
):
    declared = definition()
    flow = mode_flow(declared)
    scripted(flow["actionInstances"][1]["inputs"][0], "return fd_data._1__log.status;")
    coordinator, tools, _ = routes(settings, flow, declared)
    rejected = await preview(tools, [{"op": "delete_step", "step": "ui-1"}])
    assert_read_only_rejection(rejected, coordinator)
    edits = [mode_edit(code), {"op": "delete_step", "step": "ui-1"}]
    if code is None:
        rejected = await preview(tools, edits)
        assert_read_only_rejection(rejected, coordinator)
    else:
        planned = await plan(settings, flow, edits)
        actions = planned["payload"]["actionInstances"]
        assert {step["id"] for step in actions} == {"2" * 32, "3" * 32}
        consumer = next(step for step in actions if step["id"] == "2" * 32)
        row = consumer["inputs"][0]
        assert row["scriptActive"] is False
        assert row["script"][INPUT]["script"] == ""
        assert row["script"][INPUT]["scriptActive"] is False
        assert row["script"][INPUT]["showScript"] is False
        assert row["value"] == "two"
        assert json.loads(row["scriptAsJsonString"]) == row["script"]


@pytest.mark.parametrize("defect", ["later_authored", "later_moved", "non_dominating"])
async def test_later_and_non_dominating_script_producers_reject_before_write(
    settings, defect
):
    declared = definition()
    flow = mode_flow(declared)
    if defect == "later_authored":
        edits = [mode_edit("return fd_data._3__log.status;")]
    else:
        scripted(
            flow["actionInstances"][1]["inputs"][0], "return fd_data._1__log.status;"
        )
        if defect == "later_moved":
            edits = [{"op": "move_step", "step": "ui-1", "after": "ui-2"}]
        else:
            edits = [
                {
                    "op": "insert_logic",
                    "logic": "If",
                    "before": "ui-1",
                    "ref": "if",
                    "inputs": {"condition": "true"},
                },
                {"op": "insert_logic", "logic": "Else", "after": "if", "ref": "else"},
                {"op": "move_step", "step": "ui-1", "parent": "if"},
                {"op": "move_step", "step": "ui-2", "parent": "else"},
            ]
    coordinator, tools, _ = routes(settings, flow, declared)
    result = await preview(tools, edits)
    assert_read_only_rejection(result, coordinator)


@pytest.mark.parametrize(
    ("reference", "allowed"),
    [
        ("fd_data._1__log.status", True),
        ("fd_data._1__log.details.message", True),
        ("fd_data._1__log.record.state", True),
        ("fd_data._1__log.details.undeclared", False),
        ("fd_data._1__log.record.undeclared", False),
        ("fd_data._1__Log.status", False),
        ("fd_data._99__log.status", False),
        ("fd_data.trigger.request_item.state", False),
        ("fd_data.flow_var.undeclared", False),
        ("fd_data.subflow_inputs.undeclared", False),
    ],
)
async def test_script_paths_use_declared_output_and_dictionary_boundaries(
    settings, reference, allowed
):
    declared = definition()
    flow = mode_flow(declared)
    coordinator, tools, _ = routes(settings, flow, declared)
    result = await preview(tools, [mode_edit(f"return {reference};")])
    assert (result["status"] == "success") is allowed, result
    if not allowed:
        assert_read_only_rejection(result, coordinator)
    if reference.endswith("record.state"):
        assert any(
            call.request.url.path == "/api/now/table/sys_dictionary"
            and "sc_req_item" in call.request.url.params["sysparm_query"]
            and "element=state" in call.request.url.params["sysparm_query"]
            for call in http_mock.calls
        )
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_script_output_reference_cannot_bypass_denied_table_policy(settings):
    declared = definition()
    declared["outputs"][1]["reference"] = "oauth_credential"
    flow = mode_flow(declared)
    coordinator, tools, _ = routes(settings, flow, declared)
    result = await preview(tools, [mode_edit("return fd_data._1__log.record.sys_id;")])
    assert_read_only_rejection(result, coordinator)
    assert "denied" in result["error"]["message"].lower()
    assert not any(
        call.request.url.path == "/api/now/table/oauth_credential"
        for call in http_mock.calls
    )


@pytest.mark.parametrize("producer", ["trigger", "flow_var", "subflow_inputs"])
async def test_declared_trigger_variable_and_subflow_input_script_roots(
    settings, producer
):
    declared = definition()
    flow = mode_flow(declared)
    edits = []
    if producer == "trigger":
        edits.append({"op": "set_trigger", "type": "service_catalog"})
        path = "request_item.state"
    else:
        collection = "flowVariables" if producer == "flow_var" else "inputs"
        if collection == "inputs":
            flow["type"] = "subflow"
        edits.append(
            {
                "op": "create_field",
                "collection": collection,
                "name": "amount",
                "label": "Amount",
                "type": "integer",
            }
        )
        path = "amount"
    routes(settings, flow, declared)
    code = f"return fd_data.{producer}.{path};"
    edits.append(mode_edit(code))
    planned = await plan(settings, flow, edits)
    row = planned["payload"]["actionInstances"][1]["inputs"][0]
    assert row["script"][INPUT]["script"] == code
    assert row["scriptActive"] is True
    assert row["value"] == "two"
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_unedited_native_script_and_metadata_survive_independent_get(settings):
    declared = definition()
    flow = mode_flow(declared)
    scripted(
        flow["actionInstances"][1]["inputs"][0],
        "// exact source\r\nreturn 'café';\n",
    )
    row = flow["actionInstances"][1]["inputs"][0]
    row["scriptAsJsonString"] = json.dumps(row["script"], ensure_ascii=False, indent=4)
    original = copy.deepcopy(row)
    _, tools, action_read = routes(settings, flow, declared)

    def save(request):
        payload = json.loads(request.content)
        assert payload["actionInstances"][1]["inputs"][0] == original
        persist(copy.deepcopy(payload))
        return httpx2.Response(200, json={"result": {"data": copy.deepcopy(payload)}})

    write = http_mock.route(method="PUT", url=f"{STUDIO}/flow").mock(side_effect=save)
    staged = await preview(
        tools,
        [{"op": "update_inputs", "step": "ui-3", "inputs": {INPUT: "unrelated edit"}}],
    )
    assert staged["status"] == "success", staged
    assert action_read.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=staged["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    assert write.call_count == 1
    calls = [call.request for call in http_mock.calls]
    write_index = next(i for i, request in enumerate(calls) if request.method == "PUT")
    assert any(
        request.method == "GET"
        and request.url.path == f"/api/now/processflow/flow/{FLOW}"
        for request in calls[write_index + 1 :]
    )


@pytest.mark.parametrize("authored", [False, True])
async def test_full_call_definition_revision_stales_script_preview_before_write(
    settings, authored
):
    declared = definition()
    declared["sys_updated_on"] = "2026-10-01 01:00:00"
    flow = mode_flow(declared)
    if not authored:
        scripted(flow["actionInstances"][1]["inputs"][0])
    coordinator, tools, action_read = routes(settings, flow, declared)
    write = http_mock.route(method="PUT", url=f"{STUDIO}/flow")
    edits = (
        [mode_edit(AUTHORED)]
        if authored
        else [{"op": "update_inputs", "step": "ui-3", "inputs": {INPUT: "unrelated"}}]
    )
    staged = await preview(tools, edits)
    assert staged["status"] == "success", staged
    declared["sys_updated_on"] = "2026-10-01 02:00:00"
    applied = decode_response(
        await tools["mutation_apply"](preview_token=staged["data"]["preview_token"])
    )
    assert applied["error"]["code"] == "STALE_PREVIEW", applied
    assert len(coordinator.store) == 0
    assert action_read.call_count >= 2
    assert not write.called
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("collection", ["workflowInputs", "decisionTableInputs"])
async def test_script_target_names_are_unique_across_native_input_collections(
    settings, collection
):
    declared = definition()
    flow = mode_flow(declared)
    step = flow["actionInstances"][1]
    step[collection] = copy.deepcopy(step["inputs"])
    coordinator, tools, _ = routes(settings, flow, declared)
    result = await preview(tools, [mode_edit("return 'ambiguous target';")])
    assert_read_only_rejection(result, coordinator)
    assert "ambiguous" in result["error"]["message"].lower()


async def decision_mode_tools(settings, *, existing):
    flow, _, _, _, _ = copy.deepcopy(await _decision_flow(settings))
    if not existing:
        flow["flowLogicInstances"] = []
    server = MCPServer("flow-input-modes-decision")
    coordinator = register_mutations(
        server, settings, OAuthPKCEProvider(settings), frozenset({"flows"})
    )
    http_mock.get(f"{STUDIO}/flow/{FLOW}").mock(
        side_effect=lambda _: httpx2.Response(
            200, json={"result": {"data": copy.deepcopy(flow)}}
        )
    )
    return flow, coordinator, get_tool_functions(server)


@pytest.mark.parametrize("operation", ["insert", "update"])
async def test_decision_input_script_routes_to_native_metadata_binding_and_readback(
    settings, operation
):
    flow, _, tools = await decision_mode_tools(settings, existing=operation == "update")
    before = copy.deepcopy(flow)
    code = "throw new Error('authoring only'); return 42;"
    edit: dict[str, Any] = {
        "op": "insert_logic" if operation == "insert" else "update_inputs",
        "inputs": {},
        "decision_inputs": {"age": ""},
        "input_scripts": {"age": code},
    }
    if operation == "insert":
        edit.update(
            logic="DECISION",
            inputs={"decision_table": DECISION, "has_branches": False},
        )
    else:
        edit["step"] = "decision-ui"
    saved: dict[str, Any] = {}

    def save(request):
        payload = json.loads(request.content)
        actual = copy.deepcopy(payload)
        for index, row in enumerate(actual["flowLogicInstances"], start=10):
            if not row.get("id"):
                row["id"] = f"{index:032x}"
        saved.update(copy.deepcopy(actual))
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": copy.deepcopy(actual)}})

    write = http_mock.route(method="PUT", url=f"{STUDIO}/flow").mock(side_effect=save)
    staged = await preview(tools, [edit])
    assert staged["status"] == "success", staged
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=staged["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    assert write.call_count == 1
    step = next(
        row
        for row in saved["flowLogicInstances"]
        if row["flowLogicDefinition"]["type"] == "DECISION"
    )
    age = next(row for row in step["decisionTableInputs"] if row["name"] == "age")
    assert age["value"] == ""
    assert age["parameter"]["type"] == "integer"
    assert age["parameter"]["mandatory"] is True
    assert age["scriptActive"] is True
    assert age["script"] == {
        "age": {"script": code, "scriptActive": True, "showScript": True}
    }
    assert age["parameter"]["attributes"]["fixture_native_schema"] == {
        "preserve": [False, 0, "æøå"]
    }
    assert not any(row["name"] == "age" for row in step["inputs"])
    assert flow == before
    calls = [call.request for call in http_mock.calls]
    write_index = next(i for i, request in enumerate(calls) if request.method == "PUT")
    assert any(
        request.method == "GET"
        and request.url.path == f"/api/now/processflow/flow/{FLOW}"
        for request in calls[write_index + 1 :]
    )
    assert not http_mock.unexpected


@pytest.mark.parametrize("code", [None, ""])
async def test_workflow_input_scripts_survive_schema_hydration_and_basic_mode_switch(
    settings, code
):
    model, _, tools = utility_setup(settings, existing=True)
    model.flow = copy.deepcopy(model.flow)
    before = copy.deepcopy(model.flow)
    inputs: dict[str, Any] = copy.deepcopy(workflow_inputs())
    inputs["workflow_inputs"]["message"] = ""
    source = "throw new Error('authoring only'); return 'workflow value';"
    staged = await preview(
        tools,
        [
            {
                "op": "insert_logic",
                "logic": "RUNWORKFLOW",
                "inputs": inputs,
                "input_scripts": {"message": source},
            }
        ],
    )
    assert staged["status"] == "success", staged
    assert model.flow == before
    assert not model.bodies
    applied = decode_response(
        await tools["mutation_apply"](preview_token=staged["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    step = logic_step(model, "RUNWORKFLOW")
    row = next(item for item in step["workflowInputs"] if item["name"] == "message")
    assert row["value"] == ""
    assert row["parameter"]["type"] == "string"
    assert row["parameter"]["mandatory"] is True
    assert row["scriptActive"] is True
    assert row["script"] == {
        "message": {"script": source, "scriptActive": True, "showScript": True}
    }
    schema = copy.deepcopy(row["parameter"])
    uid = step["uiUniqueIdentifier"]
    staged = await preview(
        tools,
        [
            {
                "op": "update_inputs",
                "step": uid,
                "inputs": {"workflow_inputs": {"message": "basic workflow fallback"}},
                "input_scripts": {"message": code},
            }
        ],
    )
    assert staged["status"] == "success", staged
    applied = decode_response(
        await tools["mutation_apply"](preview_token=staged["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    step = logic_step(model, "RUNWORKFLOW")
    row = next(item for item in step["workflowInputs"] if item["name"] == "message")
    assert row["value"] == "basic workflow fallback"
    assert row["parameter"] == schema
    assert row["scriptActive"] is False
    assert row["script"] == {
        "message": {
            "script": source if code is None else "",
            "scriptActive": False,
            "showScript": False,
        }
    }
    assert len(model.bodies) == 2
    assert all(
        call.request.method == "GET"
        or (
            call.request.method == "PUT"
            and call.request.url.path == "/api/now/processflow/flow"
        )
        for call in http_mock.calls
    )
    assert not http_mock.unexpected


DEPENDENT_ACTION = "e" * 32


def dependent_definition(kind: str, *, readonly_table: bool) -> dict[str, Any]:
    declared = definition(mandatory=True)
    declared.update(
        id=DEPENDENT_ACTION, name="Dependent target", internal_name="dependent_target"
    )
    declared["inputs"] = [
        {
            "name": "table",
            "type": "table_name",
            "defaultValue": "sc_req_item",
            "readonly": readonly_table,
            "children": [],
        },
        {
            "name": "target",
            "type": kind,
            "reference": "",
            "dependent_on": "inputs.table",
            "mandatory": True,
            "maxsize": 255,
            "attributes": {"is_scriptable": "true"},
            "children": [],
        },
    ]
    declared["outputs"] = []
    return declared


@pytest.mark.parametrize("kind", ["field_name", "document_id"])
@pytest.mark.parametrize("readonly_table", [False, True])
async def test_scripted_dependent_target_preserves_static_table_metadata_on_save(
    settings, kind, readonly_table
):
    source = definition()
    flow = mode_flow(source)
    before = copy.deepcopy(flow)
    _, tools, _ = routes(settings, flow, source)
    declared = dependent_definition(kind, readonly_table=readonly_table)
    expected_schema = copy.deepcopy(declared["inputs"][1])
    http_mock.get(f"{STUDIO}/action/action_types/{DEPENDENT_ACTION}").mock(
        side_effect=lambda _: httpx2.Response(
            200, json={"result": copy.deepcopy(declared)}
        )
    )
    fallback = "saved_basic_field" if kind == "field_name" else ""
    source_code = (
        "return 'state';" if kind == "field_name" else "return fd_data._1__log.record;"
    )
    saved: dict[str, Any] = {}

    def save(request):
        payload = json.loads(request.content)
        for step in payload["actionInstances"]:
            if step["actionTypeSysId"] == DEPENDENT_ACTION:
                step["id"] = "c" * 32
        saved.update(copy.deepcopy(payload))
        persist(copy.deepcopy(payload))
        return httpx2.Response(200, json={"result": {"data": copy.deepcopy(payload)}})

    write = http_mock.route(method="PUT", url=f"{STUDIO}/flow").mock(side_effect=save)
    staged = await preview(
        tools,
        [
            {
                "op": "insert_action",
                "action_type_sys_id": DEPENDENT_ACTION,
                "after": "ui-1",
                "inputs": {"target": fallback},
                "input_scripts": {"target": source_code},
            }
        ],
    )
    assert staged["status"] == "success", staged
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=staged["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    assert write.call_count == 1
    step = next(
        item
        for item in saved["actionInstances"]
        if item["actionTypeSysId"] == DEPENDENT_ACTION
    )
    target = next(item for item in step["inputs"] if item["name"] == "target")
    table = next(item for item in step["inputs"] if item["name"] == "table")
    assert target["value"] == fallback
    assert target["parameter"] == expected_schema
    assert target["scriptActive"] is True
    assert target["script"] == {
        "target": {"script": source_code, "scriptActive": True, "showScript": True}
    }
    assert table["value"] == "sc_req_item"
    assert table["parameter"]["readonly"] is readonly_table
    assert table["scriptActive"] is False
    assert {key: step["actionType"][key] for key in declared} == declared
    assert flow == before
    assert not any(
        call.request.url.path == "/api/now/table/sys_dictionary"
        and "element=saved_basic_field" in call.request.url.params["sysparm_query"]
        for call in http_mock.calls
    )
    calls = [call.request for call in http_mock.calls]
    write_index = next(i for i, request in enumerate(calls) if request.method == "PUT")
    assert any(
        request.method == "GET"
        and request.url.path == f"/api/now/processflow/flow/{FLOW}"
        for request in calls[write_index + 1 :]
    )
    assert not http_mock.unexpected


@pytest.mark.parametrize("kind", ["field_name", "document_id"])
@pytest.mark.parametrize("table_mode", ["pill", "script"])
async def test_scripted_dependent_target_requires_basic_static_table_sibling(
    settings, kind, table_mode
):
    source = definition()
    source["outputs"].append({"name": "table", "type": "table_name", "children": []})
    flow = mode_flow(source)
    coordinator, tools, _ = routes(settings, flow, source)
    declared = dependent_definition(kind, readonly_table=False)
    http_mock.get(f"{STUDIO}/action/action_types/{DEPENDENT_ACTION}").respond(
        200, json={"result": copy.deepcopy(declared)}
    )
    inputs = {"table": "{{ui-1.table}}" if table_mode == "pill" else "sc_req_item"}
    modes = {"target": "return 'scripted dependent value';"}
    if table_mode == "script":
        modes["table"] = "return 'sc_req_item';"
    result = await preview(
        tools,
        [
            {
                "op": "insert_action",
                "action_type_sys_id": DEPENDENT_ACTION,
                "after": "ui-1",
                "inputs": inputs,
                "input_scripts": modes,
            }
        ],
    )
    assert_read_only_rejection(result, coordinator)


@pytest.mark.parametrize("basic_mode", [None, ""])
async def test_set_variables_script_preserves_zero_and_native_assignment_mirror(
    settings, basic_mode
):
    declared = definition()
    flow = mode_flow(declared)
    flow["flowVariables"] = [
        {
            "id": "6" * 32,
            "name": "counter",
            "label": "Counter",
            "type": "integer",
            "value": 0,
            "children": [],
        }
    ]
    before = copy.deepcopy(flow)
    _, tools, _ = routes(settings, flow, declared)
    http_mock.get(f"{STUDIO}/flow_logic/types").respond(
        200, json={"result": copy.deepcopy(family_definitions())}
    )
    saved: list[dict[str, Any]] = []

    def save(request):
        payload = json.loads(request.content)
        for step in payload["flowLogicInstances"]:
            step.setdefault("id", "d" * 32)
        saved.append(copy.deepcopy(payload))
        persist(copy.deepcopy(payload))
        return httpx2.Response(200, json={"result": {"data": copy.deepcopy(payload)}})

    write = http_mock.route(method="PUT", url=f"{STUDIO}/flow").mock(side_effect=save)
    staged = await preview(
        tools,
        [
            {
                "op": "set_variables",
                "after": "ui-1",
                "inputs": {"counter": 0},
                "input_scripts": {"counter": "return 1;"},
            }
        ],
    )
    assert staged["status"] == "success", staged
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=staged["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    assert write.call_count == 1
    assignment = next(
        step
        for step in saved[0]["flowLogicInstances"]
        if step["flowLogicDefinition"]["type"] == "SETFLOWVARIABLES"
    )
    assert assignment["inputs"] == assignment["flowVariables"]
    row = assignment["inputs"][0]
    assert row["name"] == "counter"
    assert row["value"] == 0 and type(row["value"]) is int
    assert row["parameter"] == before["flowVariables"][0]
    assert row["scriptActive"] is True
    assert row["script"] == {
        "counter": {"script": "return 1;", "scriptActive": True, "showScript": True}
    }
    assert saved[0]["flowVariables"] == before["flowVariables"]
    staged = await preview(
        tools,
        [
            {
                "op": "update_inputs",
                "step": assignment["uiUniqueIdentifier"],
                "inputs": {"counter": 0},
                "input_scripts": {"counter": basic_mode},
            }
        ],
    )
    assert staged["status"] == "success", staged
    applied = decode_response(
        await tools["mutation_apply"](preview_token=staged["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    assert write.call_count == 2
    assignment = next(
        step
        for step in saved[1]["flowLogicInstances"]
        if step["flowLogicDefinition"]["type"] == "SETFLOWVARIABLES"
    )
    assert assignment["inputs"] == assignment["flowVariables"]
    row = assignment["inputs"][0]
    assert row["value"] == 0 and type(row["value"]) is int
    assert row["parameter"] == before["flowVariables"][0]
    assert row["scriptActive"] is False
    assert row["script"] == {
        "counter": {
            "script": "return 1;" if basic_mode is None else "",
            "scriptActive": False,
            "showScript": False,
        }
    }
    assert saved[1]["flowVariables"] == before["flowVariables"]
    assert flow == before
    assert all(
        call.request.method == "GET"
        or (
            call.request.method == "PUT"
            and call.request.url.path == "/api/now/processflow/flow"
        )
        for call in http_mock.calls
    )
    assert not http_mock.unexpected
