"""Remaining native Array consumer contracts, using source-derived packed schemas."""

import copy
import json
from typing import Any

import httpx2
import pytest

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import declarations
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_editing import BASE, FLOW, persist
from tests.test_flow_fields import plan
from tests.test_flow_output_assignments import routes, subflow

CHOICES = [
    {"label": "Open", "value": "open"},
    {"label": "Closed", "value": "closed"},
]
DATES = ["2024-02-29 12:34:56", "2026-10-26 00:00:00"]
CONSUMERS = ["action", "subflow", "assignment"]
KINDS = ["array.choice", "array.datetime", "array.array"]


def packed_array(name: str, kind: str) -> dict[str, Any]:
    item_type = declarations.ARRAYS[kind]
    item_label = {
        "choice": "Choice",
        "glide_date_time": "Date/Time",
        "array": "Array",
        "string": "String",
    }[item_type]
    row: dict[str, Any] = {
        "name": name,
        "label": name.title(),
        "type": kind,
        "type_label": declarations.LABELS[kind],
        "parent": "",
        "uiUniqueId": name + "-root-ui",
        "attributes": {"uiUniqueId": name + "-root-ui", "max_rows": None},
        "children": [
            {
                "name": name + "." + name + "_child0",
                "label": name.title() + "_child0",
                "type": item_type,
                "type_label": item_label,
                "parent": name,
                "uiUniqueId": name + "-item-ui",
                "fieldFacetMap": {"uiUniqueId": name + "-item-ui"},
                "cannotBeDeleted": True,
                "hint": "",
                "maxsize": "",
                "children": [],
            }
        ],
    }
    if kind == "array.choice":
        row["choices"] = copy.deepcopy(CHOICES)
        row["children"][0]["choices"] = copy.deepcopy(CHOICES)
    return declarations.pack(row)


def prepared(settings, kind, consumer="assignment"):
    flow = copy.deepcopy(subflow())
    producer = flow["actionInstances"][0]
    producer["actionTypeSysId"] = "a" * 32
    producer["actionType"]["id"] = "a" * 32
    producer["actionType"]["outputs"].extend(
        [
            packed_array("source", kind),
            packed_array("strings", "array.string"),
            {
                "name": "status",
                "type": "choice",
                "choices": copy.deepcopy(CHOICES),
                "children": [],
            },
            {"name": "timestamp", "type": "glide_date_time", "children": []},
            {"name": "row", "type": "array", "children": []},
            {"name": "other_row", "type": "array", "children": []},
        ]
    )
    target = packed_array("items", kind)
    if consumer == "assignment":
        flow["outputs"] = [copy.deepcopy(target)]
    else:
        step = (
            flow["actionInstances"][1]
            if consumer == "action"
            else flow["subFlowInstances"][0]
        )
        step["inputs"].append(
            {
                "name": "items",
                "value": "",
                "children": [],
                "parameter": copy.deepcopy(target),
            }
        )
    # routes() uses family_definitions(), which deep-copies the shared DEFS.
    coordinator, tools = routes(settings, flow)
    current: dict[str, Any] = {
        **copy.deepcopy(producer["actionType"]),
        "id": "a" * 32,
        "security": {"can_read": True},
        "entitled": True,
        "state": "published",
        "is_snapshot": True,
    }
    http_mock.get(f"{BASE}/api/now/processflow/action/action_types/{'a' * 32}").respond(
        200, json={"result": copy.deepcopy(current)}
    )
    return flow, coordinator, tools, current


def edit_value(consumer, value):
    if consumer == "assignment":
        return {"op": "assign_outputs", "outputs": {"items": copy.deepcopy(value)}}
    return {
        "op": "update_inputs",
        "step": "ui-2" if consumer == "action" else "ui-4",
        "inputs": {"items": copy.deepcopy(value)},
    }


def binding(flow, consumer):
    if consumer == "assignment":
        return flow["flowLogicInstances"][0]["outputsToAssign"][0]
    if consumer == "action":
        return flow["actionInstances"][1]["inputs"][-1]
    return flow["subFlowInstances"][0]["inputs"][-1]


def target_schema(flow, consumer):
    if consumer == "assignment":
        return flow["outputs"][0]
    return binding(flow, consumer)["parameter"]


@pytest.mark.parametrize("consumer", CONSUMERS)
@pytest.mark.parametrize("values", [[], ["open", "{{ui-1.status}}", "closed"]])
async def test_choice_arrays_serialize_literals_and_typed_scalar_pills(
    settings, consumer, values
):
    flow, _, _, _ = prepared(settings, "array.choice", consumer)
    result = await plan(settings, flow, [edit_value(consumer, values)])
    row = binding(result["payload"], consumer)
    assert row["value"] == row["displayValue"] == ""
    assert [child["value"] for child in row["children"]] == values
    assert all(child["type"] == "choice" for child in row["children"])
    if consumer == "assignment":
        assert result["payload"]["flowLogicInstances"][0]["data"]["items"] == values
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("consumer", CONSUMERS)
@pytest.mark.parametrize("kind", KINDS)
async def test_remaining_whole_array_pills_use_exact_native_types(
    settings, consumer, kind
):
    flow, _, _, _ = prepared(settings, kind, consumer)
    before = copy.deepcopy(target_schema(flow, consumer))
    result = await plan(settings, flow, [edit_value(consumer, "{{ui-1.source}}")])
    row = binding(result["payload"], consumer)
    assert row["value"] == "{{ui-1.source}}"
    assert row["children"] == []
    assert target_schema(result["payload"], consumer) == before
    assert result["payload"]["label_cache"][0]["name"] == "ui-1.source"
    reads = [call.request.url.path for call in http_mock.calls]
    assert f"/api/now/processflow/action/action_types/{'a' * 32}" in reads


@pytest.mark.parametrize("consumer", CONSUMERS)
@pytest.mark.parametrize(
    "values",
    [["unknown"], [1], [True], [{"value": "open"}], ["{{ui-1.count}}"]],
)
async def test_choice_arrays_reject_disallowed_literals_and_wrong_pill_types(
    settings, consumer, values
):
    flow, _, _, _ = prepared(settings, "array.choice", consumer)
    with pytest.raises(CapabilityError):
        await plan(settings, flow, [edit_value(consumer, values)])
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("consumer", CONSUMERS)
@pytest.mark.parametrize(
    "defect",
    ["choice_added", "choice_removed", "missing_choices", "table_choices"],
)
async def test_whole_choice_arrays_read_independent_producer_choice_sets(
    settings, consumer, defect
):
    flow, coordinator, tools, current = prepared(settings, "array.choice", consumer)
    independent = copy.deepcopy(current)
    source = next(row for row in independent["outputs"] if row["name"] == "source")
    if defect == "choice_added":
        source["choices"].append({"label": "Queued", "value": "queued"})
    elif defect == "choice_removed":
        source["choices"].pop()
    elif defect == "missing_choices":
        source.pop("choices")
    else:
        source.update(choiceType="2", table="task", columnName="state")
    http_mock.get(f"{BASE}/api/now/processflow/action/action_types/{'a' * 32}").respond(
        200, json={"result": independent}
    )
    before = copy.deepcopy(flow)
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[edit_value(consumer, "{{ui-1.source}}")],
        )
    )
    assert preview["status"] == "error", preview
    assert len(coordinator.store) == 0
    assert flow == before
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("consumer", CONSUMERS)
async def test_choice_array_whole_pill_rejects_target_choice_set_drift(
    settings, consumer
):
    flow, _, _, _ = prepared(settings, "array.choice", consumer)
    target_schema(flow, consumer)["choices"] = [copy.deepcopy(CHOICES[0])]
    with pytest.raises(CapabilityError, match="choice"):
        await plan(settings, flow, [edit_value(consumer, "{{ui-1.source}}")])


@pytest.mark.parametrize("consumer", CONSUMERS)
async def test_datetime_arrays_keep_native_literals_and_typed_pills(settings, consumer):
    flow, _, _, _ = prepared(settings, "array.datetime", consumer)
    values = [DATES[0], "{{ui-1.timestamp}}", DATES[1]]
    result = await plan(settings, flow, [edit_value(consumer, values)])
    row = binding(result["payload"], consumer)
    assert row["value"] == row["displayValue"] == ""
    assert [child["value"] for child in row["children"]] == values
    assert all(child["type"] == "glide_date_time" for child in row["children"])
    assert [item["name"] for item in result["payload"]["label_cache"]] == [
        "ui-1.timestamp"
    ]


@pytest.mark.parametrize("consumer", CONSUMERS)
@pytest.mark.parametrize(
    "value",
    [
        "2025-02-29 12:00:00",
        "2024-13-01 00:00:00",
        "2024-01-01 24:00:00",
        "2024-01-01T00:00:00Z",
        "2024-01-01",
        1700000000,
        "{{ui-1.status}}",
    ],
)
async def test_datetime_arrays_reject_invalid_dates_and_untyped_text_pills(
    settings, consumer, value
):
    flow, _, _, _ = prepared(settings, "array.datetime", consumer)
    with pytest.raises(CapabilityError):
        await plan(settings, flow, [edit_value(consumer, [value])])
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("consumer", CONSUMERS)
async def test_array_arrays_preserve_individual_exact_generic_array_pills(
    settings, consumer
):
    flow, _, _, _ = prepared(settings, "array.array", consumer)
    values = ["{{ui-1.row}}", "{{ui-1.other_row}}"]
    result = await plan(settings, flow, [edit_value(consumer, values)])
    row = binding(result["payload"], consumer)
    assert row["value"] == row["displayValue"] == ""
    assert [child["value"] for child in row["children"]] == values
    assert all(child["type"] == "array" for child in row["children"])
    assert all(child["children"] == [] for child in row["children"])
    assert {item["name"] for item in result["payload"]["label_cache"]} == {
        "ui-1.row",
        "ui-1.other_row",
    }


@pytest.mark.parametrize("consumer", CONSUMERS)
@pytest.mark.parametrize(
    "value",
    [
        [[]],
        [["literal"]],
        [0],
        ["literal"],
        ["prefix {{ui-1.row}}"],
        ["{{ui-1.row}}{{ui-1.other_row}}"],
        ["{{ui-1.row}"],
        ["{{ui-1.strings}}"],
        ["{{ui-1.count}}"],
        "prefix {{ui-1.source}}",
        "{{ui-1.strings}}",
    ],
)
async def test_array_arrays_reject_nested_literals_and_nonexact_array_pills(
    settings, consumer, value
):
    flow, _, _, _ = prepared(settings, "array.array", consumer)
    with pytest.raises(CapabilityError):
        await plan(settings, flow, [edit_value(consumer, value)])
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "defect", ["missing_item", "wrong_item_type", "extra_children", "dynamic"]
)
async def test_remaining_array_consumers_reject_incomplete_or_dynamic_schemas(
    settings, kind, defect
):
    flow, coordinator, tools, _ = prepared(settings, kind)
    target = target_schema(flow, "assignment")
    if defect == "missing_item":
        target["attributes"].pop("child_uiUniqueId")
    elif defect == "wrong_item_type":
        target["attributes"]["child_type"] = "integer"
    elif defect == "extra_children":
        target["children"] = [{"name": "unexpected", "type": "string"}]
    else:
        target["data_structure"] = "unresolved-provider"
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[edit_value("assignment", "{{ui-1.source}}")],
        )
    )
    assert preview["status"] == "error", preview
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "side,defect",
    [
        (None, None),
        ("echo", "value"),
        ("readback", "value"),
        ("echo", "item_type"),
        ("readback", "item_type"),
        ("echo", "packed_schema"),
        ("readback", "packed_schema"),
    ],
)
async def test_remaining_array_save_checks_echo_and_independent_readback(
    settings, kind, side, defect
):
    _, coordinator, tools, _ = prepared(settings, kind)
    values = {
        "array.choice": ["open", "closed"],
        "array.datetime": copy.deepcopy(DATES),
        "array.array": ["{{ui-1.row}}", "{{ui-1.other_row}}"],
    }[kind]
    changed_value = {
        "array.choice": "closed",
        "array.datetime": "2024-03-01 12:34:56",
        "array.array": "{{ui-1.other_row}}",
    }[kind]

    def save(request):
        body = json.loads(request.content)
        persist(body)
        echo = copy.deepcopy(body)
        actual = copy.deepcopy(body)
        altered = echo if side == "echo" else actual
        if defect == "value":
            binding(altered, "assignment")["children"][0]["value"] = changed_value
        elif defect == "item_type":
            binding(altered, "assignment")["children"][0]["type"] = "string"
        elif defect == "packed_schema":
            altered["outputs"][0]["attributes"]["child_type"] = "integer"
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit", sys_id=FLOW, edits=[edit_value("assignment", values)]
        )
    )
    assert preview["status"] == "success", preview
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    if defect:
        assert applied["error"]["code"] == "UNKNOWN_OUTCOME", applied
    else:
        assert applied["status"] == "success", applied
    assert write.call_count == 1
    assert len(coordinator.store) == 0
    if side != "echo":
        requests = [call.request for call in http_mock.calls]
        put = next(i for i, request in enumerate(requests) if request.method == "PUT")
        assert any(
            request.method == "GET"
            and request.url.path == f"/api/now/processflow/flow/{FLOW}"
            for request in requests[put + 1 :]
        )
    replay = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert replay["status"] == "error"
    assert write.call_count == 1
