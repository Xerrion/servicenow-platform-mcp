"""Native template field modes through authored flow payloads and saved readback."""

import copy
import json
from typing import Any

import httpx2
import pytest

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import template_modes, tree, widgets
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_editing import FLOW, persist
from tests.test_flow_fields import plan
from tests.test_flow_input_modes import STUDIO, definition, mode_flow, preview, routes

INPUT = "record_fields"
COLUMN = "short_description"
SELECTOR = INPUT + "." + COLUMN
CODE = "// authored only; no action runs\nreturn fd_data._1__log.status;"
OLD_CODE = "return 'saved source';"
BASIC = "saved^basic=value"
ENCODED = "saved^^basic=value"
TABLE = "sc_req_item"


def column(name: str = COLUMN, kind: str = "string", **metadata: Any) -> dict[str, Any]:
    return {
        "name": name,
        "label": name.replace("_", " ").title(),
        "type": kind,
        "read_only": False,
        "can_read": True,
        "can_write": True,
        "security": {"can_read": True, "can_write": True},
        "attributes": {"is_scriptable": True},
        "reference": "",
        **metadata,
    }


def declared_template(**metadata: Any) -> dict[str, Any]:
    declared = definition()
    declared["inputs"] = [
        {
            "id": "9" * 32,
            "name": INPUT,
            "label": "Record fields",
            "type": "template_value",
            "reference": TABLE,
            "mandatory": False,
            "readonly": False,
            "children": [],
            "attributes": {"is_scriptable": True, "upsert_action": True},
            **metadata,
        }
    ]
    declared["outputs"].append(
        {
            "name": "template",
            "type": "template_value",
            "reference": TABLE,
            "children": [],
        }
    )
    return declared


def template_flow(
    declared: dict[str, Any], *, scripted: bool = False
) -> dict[str, Any]:
    flow = mode_flow(declared)
    for action in flow["actionInstances"]:
        row = action["inputs"][0]
        row.update(copy.deepcopy(declared["inputs"][0]))
        row["parameter"] = copy.deepcopy(declared["inputs"][0])
        row["value"] = f"{COLUMN}={ENCODED}^state=2"
        row["displayValue"] = row["value"]
        row["scriptActive"] = False
    if scripted:
        row = flow["actionInstances"][1]["inputs"][0]
        row["value"] = f"{COLUMN}=fd-scripted^state=2"
        row["displayValue"] = row["value"]
        row["script"] = {
            COLUMN: {
                "script": OLD_CODE,
                "scriptActive": True,
                "showScript": True,
                "savedValue": BASIC,
                "opaque": {"preserve": [1, "native entry"]},
            }
        }
        row["scriptActive"] = True
        row["scriptAsJsonString"] = json.dumps(row["script"], ensure_ascii=False)
    return flow


def template_routes(settings, flow, declared, columns=None):
    configured = routes(settings, flow, declared)
    columns = [column(), column("state", "integer")] if columns is None else columns
    schema = http_mock.get(f"{STUDIO}/table/{TABLE}/schema").respond(
        200,
        json={
            "result": {
                "data": {
                    "columns": [
                        {"label": item["label"], "definition": copy.deepcopy(item)}
                        for item in columns
                    ]
                }
            }
        },
    )
    return (*configured, schema)


def edit(code: str | None, *, value: str | None = None, selector: str = SELECTOR):
    return {
        "op": "update_inputs",
        "step": "ui-2",
        "inputs": {} if value is None else {INPUT: value},
        "input_scripts": {selector: code},
    }


def assert_read_only(coordinator, result):
    assert result["status"] == "error", result
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)
    assert not http_mock.unexpected


@pytest.mark.parametrize("code", [CODE, None, ""])
async def test_template_field_modes_save_exact_map_marker_and_restored_basic(
    settings, code
):
    declared = declared_template()
    flow = template_flow(declared, scripted=True)
    before = copy.deepcopy(flow)
    _, tools, action_read, schema_read = template_routes(settings, flow, declared)
    saved = {}

    def save(request):
        payload = json.loads(request.content)
        saved.update(copy.deepcopy(payload))
        persist(copy.deepcopy(payload))
        return httpx2.Response(200, json={"result": {"data": copy.deepcopy(payload)}})

    write = http_mock.route(method="PUT", url=f"{STUDIO}/flow").mock(side_effect=save)
    staged = await preview(tools, [edit(code)])
    assert staged["status"] == "success", staged
    assert action_read.called and schema_read.called and not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=staged["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    assert write.call_count == 1
    row = saved["actionInstances"][1]["inputs"][0]
    original = before["actionInstances"][1]["inputs"][0]
    expected = copy.deepcopy(original["script"])
    expected[COLUMN].update(
        script=OLD_CODE if code is None else code,
        scriptActive=bool(code),
        showScript=bool(code),
        savedValue="" if code == "" else BASIC,
    )
    expected_basic = "fd-scripted" if code else "" if code == "" else ENCODED
    assert row["value"] == f"{COLUMN}={expected_basic}^state=2"
    assert row["script"] == expected
    assert row["scriptActive"] is bool(code)
    assert json.loads(row["scriptAsJsonString"]) == expected
    assert row["parameter"] == original["parameter"]
    assert row["opaque"] == original["opaque"]
    assert flow == before
    requests = [call.request for call in http_mock.calls]
    put_index = next(
        index for index, request in enumerate(requests) if request.method == "PUT"
    )
    assert any(
        request.method == "GET"
        and request.url.path == f"/api/now/processflow/flow/{FLOW}"
        for request in requests[put_index + 1 :]
    )
    assert all(
        request.method == "GET"
        or request.method == "PUT"
        and request.url.path == "/api/now/processflow/flow"
        for request in requests
    )
    assert not http_mock.unexpected


async def test_new_script_saves_decoded_basic_without_modifying_other_fields(settings):
    declared = declared_template()
    flow = template_flow(declared)
    template_routes(settings, flow, declared)
    planned = await plan(settings, flow, [edit(CODE)])
    row = planned["payload"]["actionInstances"][1]["inputs"][0]
    assert row["value"] == f"{COLUMN}=fd-scripted^state=2"
    assert row["script"] == {
        COLUMN: {
            "script": CODE,
            "scriptActive": True,
            "showScript": True,
            "savedValue": BASIC,
        }
    }
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("code", [CODE, None, ""])
async def test_explicit_basic_update_wins_over_older_saved_value(settings, code):
    declared = declared_template()
    flow = template_flow(declared, scripted=True)
    template_routes(settings, flow, declared)
    replacement = f"{COLUMN}=new^^basic=value^state=3^coalesce_fields={COLUMN}"
    planned = await plan(settings, flow, [edit(code, value=replacement)])
    row = planned["payload"]["actionInstances"][1]["inputs"][0]
    expected = "fd-scripted" if code else "" if code == "" else "new^^basic=value"
    assert row["value"] == f"{COLUMN}={expected}^state=3^coalesce_fields={COLUMN}"
    assert row["script"][COLUMN]["savedValue"] == (
        "" if code == "" else "new^basic=value"
    )
    assert row["script"][COLUMN]["script"] == (OLD_CODE if code is None else code)


async def test_enable_then_disable_restores_exact_basic_in_same_edit_sequence(settings):
    declared = declared_template()
    flow = template_flow(declared)
    template_routes(settings, flow, declared)
    planned = await plan(settings, flow, [edit(CODE), edit(None)])
    row = planned["payload"]["actionInstances"][1]["inputs"][0]
    assert row["value"] == f"{COLUMN}={ENCODED}^state=2"
    assert row["script"][COLUMN] == {
        "script": CODE,
        "scriptActive": False,
        "showScript": False,
        "savedValue": BASIC,
    }
    assert row["scriptActive"] is False


async def test_disabling_one_field_keeps_other_native_column_script_active(settings):
    declared = declared_template()
    flow = template_flow(declared, scripted=True)
    row = flow["actionInstances"][1]["inputs"][0]
    row["value"] = f"{COLUMN}=fd-scripted^state=fd-scripted"
    row["script"]["state"] = {
        "script": "return 3;",
        "scriptActive": True,
        "showScript": False,
        "savedValue": "2",
    }
    row["scriptAsJsonString"] = json.dumps(row["script"])
    template_routes(settings, flow, declared)
    planned = await plan(settings, flow, [edit(None)])
    actual = planned["payload"]["actionInstances"][1]["inputs"][0]
    assert actual["value"] == f"{COLUMN}={ENCODED}^state=fd-scripted"
    assert actual["scriptActive"] is True
    assert actual["script"]["state"]["script"] == "return 3;"
    assert actual["script"]["state"]["showScript"] is False


@pytest.mark.parametrize(
    "metadata",
    [
        {"read_only": True},
        {"can_read": False},
        {"security": {"can_read": False, "can_write": True}},
        {"can_write": False},
        {"attributes": {"is_scriptable": False}},
    ],
)
async def test_independent_column_metadata_rejects_scriptability_and_access_boundaries(
    settings, metadata
):
    declared = declared_template()
    flow = template_flow(declared)
    coordinator, tools, _, _ = template_routes(
        settings, flow, declared, [column(**metadata), column("state", "integer")]
    )
    assert_read_only(coordinator, await preview(tools, [edit(CODE)]))


@pytest.mark.parametrize(
    "selector",
    [
        "missing.short_description",
        INPUT + ".unknown",
        INPUT + ".state.child",
        INPUT + ".coalesce_fields",
    ],
)
async def test_unknown_or_malformed_template_column_selector_cannot_author_scripts(
    settings, selector
):
    declared = declared_template()
    flow = template_flow(declared)
    coordinator, tools, _, _ = template_routes(settings, flow, declared)
    assert_read_only(coordinator, await preview(tools, [edit(CODE, selector=selector)]))


async def test_encoded_selected_column_cannot_define_its_own_native_schema(settings):
    declared = declared_template()
    flow = template_flow(declared)
    coordinator, tools, _, _ = template_routes(
        settings, flow, declared, [column("state", "integer")]
    )
    assert_read_only(coordinator, await preview(tools, [edit(CODE)]))


async def test_native_table_schema_read_failure_blocks_template_mutation(settings):
    declared = declared_template()
    flow = template_flow(declared)
    coordinator, tools, _, schema = template_routes(settings, flow, declared)
    schema.respond(403, json={"error": {"message": "No readable table schema"}})
    assert_read_only(coordinator, await preview(tools, [edit(CODE)]))


@pytest.mark.parametrize(
    "metadata",
    [
        {"readonly": True},
        {"attributes": {"is_scriptable": False}},
        {"reference": "oauth_credential"},
    ],
)
async def test_template_parent_readonly_scriptability_and_table_policy_are_enforced(
    settings, metadata
):
    declared = declared_template(**metadata)
    flow = template_flow(declared)
    coordinator, tools, _, _ = template_routes(settings, flow, declared)
    assert_read_only(coordinator, await preview(tools, [edit(CODE)]))
    assert not any(
        "oauth_credential/schema" in call.request.url.path for call in http_mock.calls
    )


@pytest.mark.parametrize(
    "code",
    [
        "return fd_data._1__log.missing;",
        "return fd_data._2__log.status;",
        "return fd_data._3__log.status;",
        "return fd_data[name].status;",
    ],
)
async def test_template_scripts_share_native_reference_scope_and_lexical_boundaries(
    settings, code
):
    declared = declared_template()
    flow = template_flow(declared)
    coordinator, tools, _, _ = template_routes(settings, flow, declared)
    assert_read_only(coordinator, await preview(tools, [edit(code)]))


@pytest.mark.parametrize(
    "mutation",
    [
        "orphan_marker",
        "inactive_marker",
        "active_basic",
        "orphan_map",
        "parent_status",
        "saved_marker",
        "mirror",
    ],
)
async def test_saved_template_scripts_require_full_bidirectional_binding(
    settings, mutation
):
    declared = declared_template()
    flow = template_flow(declared, scripted=True)
    row = flow["actionInstances"][1]["inputs"][0]
    if mutation == "orphan_marker":
        row.pop("script")
        row.pop("scriptAsJsonString")
        row["scriptActive"] = False
    elif mutation == "inactive_marker":
        row["script"][COLUMN]["scriptActive"] = False
        row["scriptActive"] = False
    elif mutation == "active_basic":
        row["value"] = f"{COLUMN}=literal^state=2"
    elif mutation == "orphan_map":
        row["script"]["absent"] = row["script"].pop(COLUMN)
    elif mutation == "parent_status":
        row["scriptActive"] = False
    elif mutation == "saved_marker":
        row["script"][COLUMN]["savedValue"] = "fd-scripted"
    else:
        row["scriptAsJsonString"] = "{}"
    if mutation not in {"orphan_marker", "mirror"}:
        row["scriptAsJsonString"] = json.dumps(row["script"])
    coordinator, tools, _, _ = template_routes(settings, flow, declared)
    assert_read_only(
        coordinator,
        await preview(
            tools,
            [
                {
                    "op": "update_inputs",
                    "step": "ui-3",
                    "inputs": {INPUT: f"{COLUMN}=unrelated^state=2"},
                }
            ],
        ),
    )


@pytest.mark.parametrize("coalesce", [COLUMN, COLUMN + ",state", ""])
async def test_source_native_coalesce_configuration_preserves_encoded_selection(
    settings, coalesce
):
    declared = declared_template()
    flow = template_flow(declared)
    template_routes(settings, flow, declared)
    value = f"{COLUMN}=text^state=2^coalesce_fields={coalesce}"
    planned = await plan(
        settings,
        flow,
        [{"op": "update_inputs", "step": "ui-2", "inputs": {INPUT: value}}],
    )
    actual = planned["payload"]["actionInstances"][1]["inputs"][0]
    assert actual["value"] == value
    assert actual["parameter"] == declared["inputs"][0]
    assert not actual.get("scriptActive")


@pytest.mark.parametrize(
    "coalesce", ["unknown", COLUMN + "," + COLUMN, "state.child", COLUMN + ",unknown"]
)
async def test_coalesce_requires_selected_declared_columns(settings, coalesce):
    declared = declared_template()
    flow = template_flow(declared)
    coordinator, tools, _, _ = template_routes(settings, flow, declared)
    result = await preview(
        tools,
        [
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {INPUT: f"{COLUMN}=text^state=2^coalesce_fields={coalesce}"},
            }
        ],
    )
    assert_read_only(coordinator, result)


async def test_coalesce_rejects_non_upsert_templates(settings):
    declared = declared_template(attributes={"is_scriptable": True})
    flow = template_flow(declared)
    coordinator, tools, _, _ = template_routes(settings, flow, declared)
    assert_read_only(
        coordinator,
        await preview(
            tools,
            [
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {
                        INPUT: f"{COLUMN}=text^state=2^coalesce_fields={COLUMN}"
                    },
                }
            ],
        ),
    )


async def test_password_columns_cannot_be_native_coalesce_fields(settings):
    declared = declared_template()
    flow = template_flow(declared)
    coordinator, tools, _, _ = template_routes(
        settings, flow, declared, [column(kind="password2"), column("state", "integer")]
    )
    result = await preview(
        tools,
        [
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {INPUT: f"{COLUMN}=secret^state=2^coalesce_fields={COLUMN}"},
            }
        ],
    )
    assert_read_only(coordinator, result)


async def test_saved_basic_pill_is_validated_even_when_column_script_is_active(
    settings,
):
    declared = declared_template()
    flow = template_flow(declared, scripted=True)
    row = flow["actionInstances"][1]["inputs"][0]
    row["script"][COLUMN]["savedValue"] = "{{ui-3.status}}"
    row["scriptAsJsonString"] = json.dumps(row["script"])
    coordinator, tools, _, _ = template_routes(settings, flow, declared)
    assert_read_only(coordinator, await preview(tools, [edit(CODE)]))


async def test_script_reference_policy_blocks_denied_producer_tables(settings):
    declared = declared_template()
    declared["outputs"][1]["reference"] = "oauth_credential"
    flow = template_flow(declared)
    coordinator, tools, _, _ = template_routes(settings, flow, declared)
    result = await preview(tools, [edit("return fd_data._1__log.record.sys_id;")])
    assert_read_only(coordinator, result)
    assert "denied" in result["error"]["message"].lower()
    assert not any(
        "/table/oauth_credential" in call.request.url.path for call in http_mock.calls
    )


@pytest.mark.parametrize("change", ["selected_readonly", "unselected_choice"])
async def test_full_native_template_schema_drift_stales_preview_before_write(
    settings, change
):
    declared = declared_template()
    flow = template_flow(declared)
    coordinator, tools, _, schema_read = template_routes(settings, flow, declared)
    native_columns = [
        column(),
        column("state", "integer"),
        column("unselected", "choice", choices=[{"label": "First", "value": "first"}]),
    ]

    def schema_response(_request):
        return httpx2.Response(
            200,
            json={
                "result": {
                    "data": {
                        "columns": [
                            {"label": item["label"], "definition": copy.deepcopy(item)}
                            for item in native_columns
                        ]
                    }
                }
            },
        )

    schema_read.mock(side_effect=schema_response)
    write = http_mock.route(method="PUT", url=f"{STUDIO}/flow")
    staged = await preview(tools, [edit(CODE)])
    assert staged["status"] == "success", staged
    if change == "selected_readonly":
        native_columns[0]["read_only"] = True
    else:
        native_columns[2]["choices"][0]["value"] = "changed"
    applied = decode_response(
        await tools["mutation_apply"](preview_token=staged["data"]["preview_token"])
    )
    assert applied["status"] == "error", applied
    assert len(coordinator.store) == 0 and not write.called
    assert schema_read.call_count >= 2
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("drift", ["script", "marker", "savedValue"])
async def test_independent_saved_readback_detects_template_mode_drift(settings, drift):
    declared = declared_template()
    flow = template_flow(declared)
    _, tools, _, _ = template_routes(settings, flow, declared)

    def save(request):
        payload = json.loads(request.content)
        stored = copy.deepcopy(payload)
        row = stored["actionInstances"][1]["inputs"][0]
        if drift == "script":
            row["script"][COLUMN]["script"] = "return 'different';"
        elif drift == "marker":
            row["value"] = f"{COLUMN}=different^state=2"
        else:
            row["script"][COLUMN]["savedValue"] = "different basic"
        persist(stored)
        return httpx2.Response(200, json={"result": {"data": payload}})

    write = http_mock.route(method="PUT", url=f"{STUDIO}/flow").mock(side_effect=save)
    staged = await preview(tools, [edit(CODE)])
    assert staged["status"] == "success", staged
    applied = decode_response(
        await tools["mutation_apply"](preview_token=staged["data"]["preview_token"])
    )
    assert applied["status"] == "error", applied
    assert write.call_count == 1


async def test_native_subflow_template_pill_coexists_with_explicit_fields(settings):
    declared = declared_template()
    flow = template_flow(declared)
    flow["type"] = "subflow"
    template_routes(settings, flow, declared)
    value = "{{ui-1.template}}^" + COLUMN + "=text^state=2"
    planned = await plan(
        settings,
        flow,
        [{"op": "update_inputs", "step": "ui-2", "inputs": {INPUT: value}}],
    )
    assert planned["payload"]["actionInstances"][1]["inputs"][0]["value"] == value


async def test_native_flow_widget_blocks_whole_template_pills(settings):
    declared = declared_template()
    flow = template_flow(declared)
    coordinator, tools, _, _ = template_routes(settings, flow, declared)
    result = await preview(
        tools,
        [
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {INPUT: "{{ui-1.template}}^" + COLUMN + "=text^state=2"},
            }
        ],
    )
    assert_read_only(coordinator, result)


def test_selector_resolution_requires_one_actual_template_input():
    declared = declared_template()
    raw = template_flow(declared)["actionInstances"][1]
    step = tree.Step(raw, "action")
    assert template_modes.parse_selector(step, INPUT) is None
    assert template_modes.parse_selector(step, SELECTOR) == (raw["inputs"][0], COLUMN)
    raw["dynamicInputs"] = [copy.deepcopy(raw["inputs"][0])]
    with pytest.raises(CapabilityError):
        template_modes.parse_selector(step, SELECTOR)


def test_native_template_parser_preserves_escaped_delimiters_and_equals():
    assert widgets.template_fields(
        "{{producer.template}}^" + COLUMN + "=a^^^state=left=right"
    ) == [(COLUMN, "a^"), ("state", "left=right")]


@pytest.mark.parametrize("retain_active", [False, True])
async def test_basic_template_column_removal_prunes_only_removed_script_entries(
    settings, retain_active
):
    declared = declared_template()
    flow = template_flow(declared, scripted=True)
    row = flow["actionInstances"][1]["inputs"][0]
    if retain_active:
        row["value"] = f"{COLUMN}=fd-scripted^state=fd-scripted"
        row["script"]["state"] = {
            "script": "return 3;",
            "scriptActive": True,
            "showScript": False,
            "savedValue": "2",
        }
        row["scriptAsJsonString"] = json.dumps(row["script"])
    template_routes(settings, flow, declared)
    value = "state=fd-scripted" if retain_active else "state=2"
    planned = await plan(
        settings,
        flow,
        [{"op": "update_inputs", "step": "ui-2", "inputs": {INPUT: value}}],
    )
    actual = planned["payload"]["actionInstances"][1]["inputs"][0]
    expected = {"state": row["script"]["state"]} if retain_active else {}
    assert actual["value"] == value
    assert actual["script"] == expected
    assert actual["scriptActive"] is retain_active
    assert json.loads(actual["scriptAsJsonString"]) == expected


def test_basic_preparation_preserves_unchanged_script_sidecars_and_original():
    row = template_flow(declared_template(), scripted=True)["actionInstances"][1][
        "inputs"
    ][0]
    row["scriptAsJsonString"] = json.dumps(row["script"], indent=4)
    before = copy.deepcopy(row)
    prepared = template_modes.prepare_basic(row, f"{COLUMN}=fd-scripted^state=3")
    assert row == before
    assert prepared["value"] == f"{COLUMN}=fd-scripted^state=3"
    assert prepared["script"] == before["script"]
    assert prepared["scriptAsJsonString"] == before["scriptAsJsonString"]


def test_basic_preparation_failure_never_changes_native_binding():
    row = template_flow(declared_template(), scripted=True)["actionInstances"][1][
        "inputs"
    ][0]
    before = copy.deepcopy(row)
    with pytest.raises(CapabilityError):
        template_modes.prepare_basic(
            row, f"{COLUMN}=ordinary-without-explicit-mode^state=2"
        )
    assert row == before
