"""Consumer regressions for Studio declaration editing, using source-native DTOs."""

import copy
import json
from typing import Any

import httpx2
import pytest

from servicenow_mcp.capabilities.flows import semantics
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_editing import BASE, FLOW, fixtures, persist
from tests.test_flow_fields import plan
from tests.test_flow_nested_fields import subflow

LABELS = {
    "string": "String",
    "boolean": "True/False",
    "integer": "Integer",
    "number": "Number",
    "choice": "Choice",
    "glide_date_time": "Date/Time",
    "object": "Object",
    "array.string": "Array.String",
    "array.integer": "Array.Integer",
    "array.boolean": "Array.True/False",
    "array.object": "Array.Object",
}


def saved_field(
    name: str, kind: str, identity: int, *, parent: str = "", parent_id: str = ""
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": f"{identity:032x}",
        "uiUniqueId": f"declaration-{identity}",
        "name": name,
        "label": name.rsplit(".", 1)[-1].replace("_", " ").title(),
        "type": kind,
        "type_label": LABELS.get(kind, kind),
        "parent": parent,
        "nameToBeEdited": True,
        "attributes": {
            "uiUniqueId": f"declaration-{identity}",
            "vendor": {"preserve": [False, 0, ""]},
        },
        "opaque": {"owner": identity, "retain": False},
    }
    if parent:
        row["parentId"] = parent_id
        row["fieldFacetMap"] = {
            "uiUniqueId": f"declaration-{identity}",
            "vendor": {"preserve": [False, 0, ""]},
        }
    return row


def saved_array(
    name: str = "items",
    kind: str = "array.string",
    identity: int = 20,
    *,
    parent: str = "",
    parent_id: str = "",
) -> dict[str, Any]:
    row = saved_field(name, kind, identity, parent=parent, parent_id=parent_id)
    item_type = kind.split(".", 1)[1]
    local = name.rsplit(".", 1)[-1]
    row["fieldFacetMap" if parent else "attributes"].update(
        max_rows=7,
        child_name=local + "_child0",
        child_label=local.title() + "_child0",
        child_type=item_type,
        child_type_label=LABELS[item_type],
        child_hint="Existing item hint",
        child_maxsize=64,
        child_uiUniqueId=f"item-{identity}",
    )
    row["children"] = []
    return row


def saved_tree(collection="inputs"):
    flow = subflow()
    root = saved_field("payload", "object", 10)
    count = saved_field(
        "payload.count", "integer", 11, parent="payload", parent_id="declaration-10"
    )
    details = saved_field(
        "payload.details", "object", 12, parent="payload", parent_id="declaration-10"
    )
    message = saved_field(
        "payload.details.message",
        "string",
        13,
        parent="payload.details",
        parent_id="declaration-12",
    )
    details["children"] = [message]
    tags = saved_array("payload.tags", parent="payload", parent_id="declaration-10")
    root["children"] = [count, details, tags]
    flow[collection] = [root]
    flow["vendor"] = {"preserve": [False, 0, ""]}
    return flow


async def rejected(settings, flow, edits, *, message=None, code=None):
    before = copy.deepcopy(flow)
    coordinator, tools = fixtures(settings, flow)
    result = decode_response(
        await tools["flow_preview"](
            action="edit", sys_id=FLOW, edits=copy.deepcopy(edits)
        )
    )
    assert result["status"] == "error", result
    if message is not None:
        assert message in result["error"]["message"], result
    if code is not None:
        assert result["error"]["code"] == code, result
    assert flow == before
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)
    return result


def created_child(
    name: str,
    kind: str,
    uid: str,
    parent_uid: str,
    *,
    default: Any,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": "payload." + name,
        "label": "Value",
        "type": kind,
        "type_label": LABELS[kind],
        "order": 1,
        "path": 0,
        "parent": "payload",
        "mandatory": False,
        "hint": "",
        "uiUniqueId": uid,
        "attributes": {
            "uiUniqueId": uid,
            "sourceUiUniqueId": "",
            "sourceType": "",
            "sourceId": "",
        },
        "sourceUiUniqueId": "",
        "sourceType": "",
        "sourceId": "",
        "parentId": parent_uid,
        "fieldFacetMap": {"uiUniqueId": uid},
        "defaultValue": default,
        **(extra or {}),
    }


@pytest.mark.parametrize("collection", ["inputs", "outputs"])
@pytest.mark.parametrize(
    "kind,value,extra",
    [
        ("string", "", {}),
        ("boolean", False, {}),
        ("integer", 0, {}),
        ("number", 0, {}),
        ("glide_date_time", "", {}),
        ("glide_date_time", "2026-10-26 14:30:00", {}),
        ("choice", "", {"choices": [{"label": "Blank", "value": ""}]}),
    ],
)
async def test_object_scalar_child_defaults_keep_native_false_zero_and_empty(
    settings, collection, kind, value, extra
):
    flow = subflow()
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": collection,
                "name": "payload",
                "label": "Payload",
                "type": "object",
            },
            {
                "op": "create_field",
                "collection": collection,
                "parent": "payload",
                "name": "value",
                "label": "Value",
                "type": kind,
                "default_value": value,
                **copy.deepcopy(extra),
            },
        ],
    )
    root = planned["payload"][collection][0]
    child = root["children"][0]
    assert child == created_child(
        "value",
        kind,
        child["uiUniqueId"],
        root["uiUniqueId"],
        default=value,
        extra=extra,
    )
    assert type(child["defaultValue"]) is type(value)
    assert "defaultValue" not in root and "value" not in root
    fixtures(settings, planned["payload"])
    updated = await plan(
        settings,
        planned["payload"],
        [
            {
                "op": "update_field",
                "collection": collection,
                "name": "payload.value",
                "hint": "Preserve default",
            }
        ],
    )
    assert updated["payload"][collection][0]["children"][0] == {
        **child,
        "hint": "Preserve default",
    }


@pytest.mark.parametrize(
    "kind,item_type,value,extra",
    [
        ("array.string", "string", "", {}),
        ("array.boolean", "boolean", False, {}),
        ("array.integer", "integer", 0, {}),
        ("array.datetime", "glide_date_time", "", {}),
        ("array.choice", "choice", "", {"choices": [{"label": "Blank", "value": ""}]}),
    ],
)
async def test_generated_scalar_array_item_default_is_packed_not_a_whole_default(
    settings, kind, item_type, value, extra
):
    flow = subflow()
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "inputs",
                "name": "items",
                "label": "Items",
                "type": kind,
                **copy.deepcopy(extra),
            },
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "items.items_child0",
                "default_value": value,
            },
        ],
    )
    row = planned["payload"]["inputs"][0]
    metadata = row["attributes"]
    assert metadata == {
        "uiUniqueId": row["uiUniqueId"],
        "sourceUiUniqueId": "",
        "sourceType": "",
        "sourceId": "",
        "max_rows": None,
        "child_name": "items_child0",
        "child_label": "Items_child0",
        "child_type": item_type,
        "child_type_label": LABELS[item_type],
        "child_hint": "",
        "child_maxsize": "",
        "child_uiUniqueId": metadata["child_uiUniqueId"],
        "child_defaultValue": value,
    }
    assert type(metadata["child_defaultValue"]) is type(value)
    assert row["children"] == []
    if kind != "array.choice":
        assert "defaultValue" not in row


@pytest.mark.parametrize("collection", ["inputs", "outputs"])
@pytest.mark.parametrize(
    "kind",
    [
        "object",
        "array.string",
        "array.integer",
        "array.boolean",
        "array.object",
        "array.reference",
        "array.datetime",
        "array.array",
    ],
)
@pytest.mark.parametrize("default", [None, "", {}])
async def test_whole_complex_declaration_default_reports_native_constraint(
    settings, collection, kind, default
):
    flow = subflow()
    await rejected(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": collection,
                "name": "payload",
                "label": "Payload",
                "type": kind,
                "default_value": copy.deepcopy(default),
                **({"reference": "task"} if kind == "array.reference" else {}),
            }
        ],
        message="not whole Object or Array declarations",
        code="UNAVAILABLE",
    )


@pytest.mark.parametrize("collection", ["flowVariables", "inputs", "outputs"])
async def test_new_unused_declaration_name_is_editable_in_its_multi_edit(
    settings, collection
):
    flow = subflow()
    if collection == "flowVariables":
        flow["flowVariableNames"] = ["counter"]
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": collection,
                "name": "temporary",
                "label": "Temporary",
                "type": "integer",
            },
            {
                "op": "update_field",
                "collection": collection,
                "name": "temporary",
                "new_name": "renamed",
                "hint": "Keep the identity",
            },
        ],
    )
    row = planned["payload"][collection][-1]
    uid = row["uiUniqueId"]
    assert row == {
        "name": "renamed",
        "label": "Temporary",
        "type": "integer",
        "type_label": "Integer",
        "order": 1,
        "path": 1 if collection == "flowVariables" else 0,
        "parent": "",
        "mandatory": False,
        "hint": "Keep the identity",
        "uiUniqueId": uid,
        "attributes": {
            "uiUniqueId": uid,
            "sourceUiUniqueId": "",
            "sourceType": "",
            "sourceId": "",
        },
        "sourceUiUniqueId": "",
        "sourceType": "",
        "sourceId": "",
    }
    assert planned["created_fields"] == [
        {"collection": collection, "name": "renamed", "uiUniqueId": uid}
    ]
    if collection == "flowVariables":
        assert planned["payload"]["flowVariableNames"] == ["counter", "renamed"]


@pytest.mark.parametrize("collection", ["flowVariables", "inputs", "outputs"])
@pytest.mark.parametrize("flag", [None, False, True, "true"])
async def test_persisted_names_require_the_native_interface_editor(
    settings, collection, flag
):
    flow = subflow()
    row = saved_field("amount", "integer", 10)
    if flag is None:
        row.pop("nameToBeEdited")
    else:
        row["nameToBeEdited"] = flag
    flow[collection] = [row]
    edit = {
        "op": "update_field",
        "collection": collection,
        "name": "amount",
        "new_name": "total",
    }
    if collection != "flowVariables" and flag is True:
        fixtures(settings, flow)
        result = await plan(settings, flow, [edit])
        assert result["payload"][collection] == [{**row, "name": "total"}]
    else:
        await rejected(
            settings,
            flow,
            [edit],
            code="UNAVAILABLE",
            message="saved declaration names read-only",
        )


@pytest.mark.parametrize("collection", ["inputs", "outputs"])
@pytest.mark.parametrize(
    "defect", [None, "identity", "schema", "parent", "opaque", "facet"]
)
async def test_recursive_rename_save_and_independent_readback_preserve_native_ownership(
    settings, collection, defect
):
    flow = saved_tree(collection)
    before = copy.deepcopy(flow)
    expected = copy.deepcopy(flow[collection][0])
    expected["name"] = "renamed"
    expected["children"][0].update(name="renamed.count", parent="renamed")
    expected["children"][1].update(name="renamed.details", parent="renamed")
    expected["children"][1]["children"][0].update(
        name="renamed.details.message", parent="renamed.details"
    )
    expected["children"][2].update(name="renamed.tags", parent="renamed")
    _, tools = fixtures(settings, flow)

    def save(request):
        body = json.loads(request.content)
        assert body[collection] == [expected]
        assert body["vendor"] == before["vendor"]
        assert body["flowVariables"] == before["flowVariables"]
        echo = copy.deepcopy(body)
        actual = copy.deepcopy(body)
        root = actual[collection][0]
        if defect == "identity":
            root["children"][1]["children"][0]["id"] = "f" * 32
        elif defect == "schema":
            root["children"][0]["type"] = "string"
        elif defect == "parent":
            root["children"][1]["children"][0]["parent"] = "payload.details"
        elif defect == "opaque":
            root["children"][1]["opaque"]["retain"] = True
        elif defect == "facet":
            root["children"][2]["fieldFacetMap"]["child_uiUniqueId"] = "different"
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": echo}})

    route = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_field",
                    "collection": collection,
                    "name": "payload",
                    "new_name": "renamed",
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    assert flow == before and not route.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    if defect is None:
        assert applied["status"] == "success", applied
    else:
        assert applied["error"]["code"] == "UNKNOWN_OUTCOME", applied
    assert route.call_count == 1
    assert flow == before


async def test_nested_object_rename_changes_paths_not_ids_or_opaque_facets(settings):
    flow = saved_tree()
    original = copy.deepcopy(flow["inputs"][0])
    expected = copy.deepcopy(original)
    expected["children"][1]["name"] = "payload.info"
    expected["children"][1]["children"][0].update(
        name="payload.info.message", parent="payload.info"
    )
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "payload.details",
                "new_name": "info",
            },
        ],
    )
    assert planned["payload"]["inputs"] == [expected]
    assert planned["created_fields"] == [] and planned["removed_fields"] == []


async def test_new_generated_array_item_and_nested_names_follow_root_rename(settings):
    flow = subflow()
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "inputs",
                "name": "rows",
                "label": "Rows",
                "type": "array.object",
            },
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "rows.rows_child0",
                "new_name": "entry",
            },
            {
                "op": "create_field",
                "collection": "inputs",
                "parent": "rows.entry",
                "name": "count",
                "label": "Value",
                "type": "integer",
                "default_value": 0,
            },
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "rows.entry.count",
                "new_name": "total",
            },
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "rows",
                "new_name": "records",
            },
        ],
    )
    root = planned["payload"]["inputs"][0]
    item_uid = root["attributes"]["child_uiUniqueId"]
    child = root["children"][0]
    assert root["name"] == "records" and root["attributes"]["child_name"] == "entry"
    assert root["attributes"]["child_type"] == "object"
    expected_child = created_child(
        "entry.total", "integer", child["uiUniqueId"], item_uid, default=0
    )
    expected_child["name"] = "records.entry.total"
    expected_child["parent"] = "records.entry"
    assert child == expected_child
    assert planned["created_fields"] == [
        {"collection": "inputs", "name": "records", "uiUniqueId": root["uiUniqueId"]},
        {
            "collection": "inputs",
            "name": "records.entry.total",
            "uiUniqueId": child["uiUniqueId"],
        },
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"type": "integer"},
        {"type": "object", "reset_structure": True},
        {"op": "delete_field"},
        {"max_rows": 3},
    ],
)
async def test_generated_array_item_cannot_change_fixed_type_or_parent_settings(
    settings, change
):
    await rejected(
        settings,
        subflow(),
        [
            {
                "op": "create_field",
                "collection": "inputs",
                "name": "items",
                "label": "Items",
                "type": "array.string",
            },
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "items.items_child0",
                **change,
            },
        ],
    )


def add_consumer(flow, mode):
    binding = flow["actionInstances"][1]["inputs"][0]
    if mode == "pill":
        binding["value"] = "{{subflow.payload.details.message}}"
    elif mode == "whole":
        binding["parameter"] = {"name": "log_message", "type": "object", "children": []}
        binding["value"] = "{{subflow.payload}}"
    elif mode == "script":
        binding.update(
            scriptActive=True,
            script={
                "log_message": {
                    "script": "return fd_data.subflow_inputs.payload.details.message;",
                    "scriptActive": True,
                    "showScript": True,
                }
            },
        )
    elif mode == "default":
        flow["outputs"] = [
            {
                "name": "text",
                "type": "string",
                "defaultValue": "{{subflow.payload.details.message}}",
            }
        ]
    elif mode == "source_id":
        binding["sourceId"] = f"{13:032x}"
    elif mode == "source_ui":
        binding["sourceUiUniqueId"] = "declaration-13"
    elif mode == "dependent_on":
        flow["outputs"] = [
            {
                "name": "text",
                "type": "string",
                "dependent_on": "payload.details.message",
            }
        ]
    elif mode == "internal_link":
        binding["internalLink"] = "payload.details.message"
    else:
        flow["pharmacy"] = {
            "prescriptions": [{"value": "{{subflow.payload.details.message}}"}]
        }


@pytest.mark.parametrize(
    "mode",
    [
        "pill",
        "whole",
        "script",
        "default",
        "source_id",
        "source_ui",
        "dependent_on",
        "internal_link",
        "transform",
    ],
)
@pytest.mark.parametrize(
    "change",
    [{"new_name": "renamed"}, {"type": "array.string", "reset_structure": True}],
)
async def test_rename_and_parent_shape_changes_do_not_clear_or_remap_consumers(
    settings, mode, change
):
    flow = saved_tree()
    add_consumer(flow, mode)
    await rejected(
        settings,
        flow,
        [
            {"op": "update_field", "collection": "inputs", "name": "payload", **change},
        ],
        message="in use",
    )


async def test_explicit_consumer_replacement_allows_rename_without_touching_other_values(
    settings,
):
    flow = saved_tree()
    add_consumer(flow, "pill")
    original = copy.deepcopy(flow)
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"log_message": "explicit replacement"},
            },
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "payload",
                "new_name": "renamed",
            },
        ],
    )
    assert planned["payload"]["inputs"][0]["name"] == "renamed"
    assert (
        planned["payload"]["actionInstances"][1]["inputs"][0]["value"]
        == "explicit replacement"
    )
    assert (
        planned["payload"]["actionInstances"][1]["inputs"][1]
        == original["actionInstances"][1]["inputs"][1]
    )
    assert (
        planned["payload"]["actionInstances"][0]["inputs"]
        == original["actionInstances"][0]["inputs"]
    )
    assert (
        planned["payload"]["actionInstances"][0]["actionType"]
        == original["actionInstances"][0]["actionType"]
    )
    assert planned["payload"]["vendor"] == original["vendor"]


@pytest.mark.parametrize(
    "start,end",
    [
        ("integer", "object"),
        ("integer", "array.string"),
        ("object", "integer"),
        ("object", "array.string"),
        ("array.string", "object"),
        ("array.string", "integer"),
        ("array.string", "array.integer"),
        ("array.object", "array.string"),
    ],
)
@pytest.mark.parametrize("collection", ["inputs", "outputs"])
async def test_actual_parent_shape_transitions_preserve_root_identity_and_unknown_metadata(
    settings, start, end, collection
):
    flow = subflow()
    if start.startswith("array."):
        row = saved_array("payload", start)
        if start == "array.object":
            row["children"] = [
                saved_field(
                    "payload.payload_child0.count",
                    "integer",
                    21,
                    parent="payload.payload_child0",
                    parent_id="item-20",
                )
            ]
    elif start == "object":
        row = copy.deepcopy(saved_tree()["inputs"][0])
    else:
        row = saved_field("payload", start, 20)
    flow[collection] = [row]
    fixtures(settings, flow)
    edit = {
        "op": "update_field",
        "collection": collection,
        "name": "payload",
        "type": end,
    }
    if start != "integer":
        edit["reset_structure"] = True
    result = await plan(settings, flow, [edit])
    actual = result["payload"][collection][0]
    expected = copy.deepcopy(row)
    expected.update(type=end, type_label=LABELS[end])
    expected.pop("children", None)
    for storage in ("attributes", "fieldFacetMap"):
        if storage in expected:
            expected[storage] = {
                key: value
                for key, value in expected[storage].items()
                if not key.startswith("child_") and key != "max_rows"
            }
    if end == "object":
        expected["children"] = []
    elif end.startswith("array."):
        item_type = end.split(".", 1)[1]
        expected["children"] = []
        expected["attributes"].update(
            max_rows=None,
            child_name="payload_child0",
            child_label=row["label"] + "_child0",
            child_type=item_type,
            child_type_label=LABELS[item_type],
            child_hint="",
            child_maxsize="",
            child_uiUniqueId=actual["attributes"]["child_uiUniqueId"],
        )
        assert actual["attributes"]["child_uiUniqueId"] != actual["uiUniqueId"]
        if start.startswith("array."):
            assert (
                actual["attributes"]["child_uiUniqueId"]
                != row["attributes"]["child_uiUniqueId"]
            )
    assert actual == expected
    assert result["payload"]["flowVariables"] == flow["flowVariables"]
    other = "outputs" if collection == "inputs" else "inputs"
    assert result["payload"][other] == flow[other]


@pytest.mark.parametrize("reset", [None, False])
@pytest.mark.parametrize("kind", ["object", "array.string", "array.object"])
async def test_populated_structures_require_explicit_reset(settings, kind, reset):
    flow = saved_tree() if kind == "object" else subflow()
    if kind != "object":
        flow["inputs"] = [saved_array("payload", kind)]
    edit = {
        "op": "update_field",
        "collection": "inputs",
        "name": "payload",
        "type": "integer",
    }
    if reset is not None:
        edit["reset_structure"] = reset
    await rejected(settings, flow, [edit], message="reset_structure")


async def test_explicit_child_removals_allow_shape_change_without_reset(settings):
    flow = saved_tree()
    flow["inputs"][0]["children"][0]["defaultValue"] = 0
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [
            {"op": "delete_field", "collection": "inputs", "name": "payload.count"},
            {"op": "delete_field", "collection": "inputs", "name": "payload.details"},
            {"op": "delete_field", "collection": "inputs", "name": "payload.tags"},
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "payload",
                "type": "integer",
            },
        ],
    )
    expected = copy.deepcopy(flow["inputs"][0])
    expected.update(type="integer", type_label="Integer")
    expected.pop("children")
    assert planned["payload"]["inputs"] == [expected]


@pytest.mark.parametrize(
    "field,value",
    [
        ("defaultValue", False),
        ("defaultValue", 0),
        ("defaultValue", ""),
        ("value", 0),
        ("value", "retained"),
    ],
)
async def test_reset_structure_cannot_implicitly_drop_descendant_defaults_or_values(
    settings, field, value
):
    flow = saved_tree()
    child = flow["inputs"][0]["children"][0]
    child[field] = value
    if value is False:
        child.update(type="boolean", type_label="True/False")
    elif isinstance(value, str):
        child.update(type="string", type_label="String")
    await rejected(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "payload",
                "type": "array.string",
                "reset_structure": True,
            },
        ],
    )


@pytest.mark.parametrize("value", [False, 0, "retained"])
async def test_scalar_default_requires_a_separate_explicit_clear_before_complex_type(
    settings, value
):
    kind = (
        "boolean" if value is False else "integer" if type(value) is int else "string"
    )
    flow = subflow()
    row = saved_field("payload", kind, 10)
    row["defaultValue"] = value
    flow["inputs"] = [row]
    await rejected(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "payload",
                "type": "object",
            },
        ],
        message="Clear the scalar default explicitly",
    )
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "payload",
                "default_value": None,
            },
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "payload",
                "type": "object",
            },
        ],
    )
    assert planned["payload"]["inputs"] == [
        {
            **row,
            "type": "object",
            "type_label": "Object",
            "defaultValue": None,
            "children": [],
        }
    ]


@pytest.mark.parametrize(
    "start,end,value",
    [
        ("integer", "number", 0),
        ("number", "integer", 0),
        ("string", "glide_date_time", ""),
    ],
)
async def test_compatible_scalar_type_updates_preserve_explicit_defaults(
    settings, start, end, value
):
    flow = subflow()
    row = saved_field("value", start, 10)
    row["defaultValue"] = value
    flow["outputs"] = [row]
    fixtures(settings, flow)
    result = await plan(
        settings,
        flow,
        [{"op": "update_field", "collection": "outputs", "name": "value", "type": end}],
    )
    assert result["payload"]["outputs"] == [
        {**row, "type": end, "type_label": LABELS[end]}
    ]
    assert type(result["payload"]["outputs"][0]["defaultValue"]) is type(value)


async def test_incompatible_scalar_default_is_not_implicitly_cleared(settings):
    flow = subflow()
    row = saved_field("value", "string", 10)
    row["defaultValue"] = "not an integer"
    flow["outputs"] = [row]
    await rejected(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "outputs",
                "name": "value",
                "type": "integer",
            }
        ],
    )


@pytest.mark.parametrize(
    "operation", [{"new_name": "renamed"}, {"type": "integer", "reset_structure": True}]
)
async def test_parent_rename_or_shape_reset_cannot_mutate_readonly_descendants(
    settings, operation
):
    flow = saved_tree()
    flow["inputs"][0]["children"][1]["children"][0]["readonly"] = True
    await rejected(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "payload",
                **operation,
            }
        ],
        message="Read-only",
    )


@pytest.mark.parametrize(
    "change",
    [{"snapshot": True}, {"masterSnapshot": True}, {"security": {"can_write": False}}],
)
@pytest.mark.parametrize(
    "edit",
    [
        {"new_name": "renamed"},
        {"type": "integer", "reset_structure": True},
        {"max_rows": 3},
    ],
)
async def test_declaration_modes_preserve_snapshot_and_readonly_owners(
    settings, change, edit
):
    flow = subflow()
    flow["inputs"] = [saved_array("payload")]
    flow.update(copy.deepcopy(change))
    await rejected(
        settings,
        flow,
        [{"op": "update_field", "collection": "inputs", "name": "payload", **edit}],
    )


@pytest.mark.parametrize("kind", ["reference", "json", "decimal", "float"])
@pytest.mark.parametrize("op", ["create_field", "update_field"])
async def test_object_child_type_picker_rejects_non_native_direct_scalars(
    settings, kind, op
):
    flow = saved_tree()
    edit = {"op": op, "collection": "inputs", "type": kind}
    if op == "create_field":
        edit.update(parent="payload", name="unsupported", label="Unsupported")
    else:
        edit["name"] = "payload.count"
    if kind == "reference":
        edit["reference"] = "task"
    await rejected(settings, flow, [edit], code="UNAVAILABLE")


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("maximum", [1, 23, None])
async def test_max_rows_updates_native_owner_facets_without_schema_or_identity_drift(
    settings, nested, maximum
):
    flow = saved_tree() if nested else subflow()
    if not nested:
        flow["inputs"] = [saved_array()]
    name = "payload.tags" if nested else "items"
    expected = copy.deepcopy(flow["inputs"][0])
    target = expected["children"][2] if nested else expected
    target["fieldFacetMap" if nested else "attributes"]["max_rows"] = maximum
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": name,
                "max_rows": maximum,
            }
        ],
    )
    assert planned["payload"]["inputs"] == [expected]
    assert planned["created_fields"] == [] and planned["removed_fields"] == []
    actual = copy.deepcopy(planned["payload"])
    if nested:
        actual["inputs"][0]["children"][2]["fieldFacetMap"]["max_rows"] = 99
    else:
        actual["inputs"][0]["attributes"]["max_rows"] = 99
    with pytest.raises(semantics.SemanticMismatch):
        semantics.compare_saved(planned["payload"], actual, generated_ids=set())


@pytest.mark.parametrize("maximum", [False, True, 0, -1, 1.5, "3", [], {}])
async def test_max_rows_rejects_non_positive_or_non_integer_values(settings, maximum):
    flow = subflow()
    flow["inputs"] = [saved_array()]
    await rejected(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "items",
                "max_rows": maximum,
            }
        ],
    )


async def test_max_rows_is_unavailable_on_scalar_or_object_declarations(settings):
    flow = saved_tree()
    await rejected(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "payload",
                "max_rows": 3,
            }
        ],
    )


def qualifier_routes(observed):
    def table(request):
        fields = request.url.params.get("sysparm_fields", "").split(",")
        row = {
            "name": "task",
            "sys_id": "b" * 32,
            "sys_mod_count": "4",
            "sys_updated_on": "2026-10-26 10:00:00",
            "super_class.name": "",
        }
        selected = (
            row
            if fields == [""]
            else {key: value for key, value in row.items() if key in fields}
        )
        return httpx2.Response(200, json={"result": [copy.deepcopy(selected)]})

    def dictionary(request):
        query = request.url.params["sysparm_query"]
        fields = request.url.params.get("sysparm_fields", "").split(",")
        column = query.split("^element=", 1)[1].split("^", 1)[0]
        row = observed.get(column)
        selected = (
            row
            if fields == [""] or row is None
            else {key: value for key, value in row.items() if key in fields}
        )
        rows = [] if selected is None else [copy.deepcopy(selected)]
        return httpx2.Response(200, json={"result": rows})

    http_mock.get(f"{BASE}/api/now/table/sys_db_object").mock(side_effect=table)
    http_mock.get(f"{BASE}/api/now/table/sys_dictionary").mock(side_effect=dictionary)


def qualifier_dictionary():
    return {
        "active": {
            "sys_id": "c" * 32,
            "name": "task",
            "element": "active",
            "column_label": "Active",
            "internal_type": "boolean",
            "reference": "",
            "choice": "0",
            "sys_mod_count": "4",
            "sys_updated_on": "2026-10-26 10:00:00",
        },
        "short_description": {
            "sys_id": "d" * 32,
            "name": "task",
            "element": "short_description",
            "column_label": "Short description",
            "internal_type": "string",
            "reference": "",
            "choice": "0",
            "sys_mod_count": "4",
            "sys_updated_on": "2026-10-26 10:00:00",
        },
    }


@pytest.mark.parametrize("collection", ["flowVariables", "inputs", "outputs"])
async def test_static_reference_qualifier_saves_native_ref_qual_and_preserves_ownership(
    settings, collection
):
    flow = subflow()
    row = saved_field("record", "reference", 10)
    row.update(reference="task", table="task", ref_qual="", defaultValue="")
    flow[collection] = [row]
    _, tools = fixtures(settings, flow)
    qualifier_routes(qualifier_dictionary())
    condition = "active=true^short_descriptionSTARTSWITHVPN"
    expected = {**copy.deepcopy(row), "ref_qual": condition}

    def save(request):
        body = json.loads(request.content)
        assert body[collection] == [expected]
        actual = copy.deepcopy(body)
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": copy.deepcopy(body)}})

    route = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_field",
                    "collection": collection,
                    "name": "record",
                    "reference_qualifier": condition,
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    assert not route.called
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "success", result
    assert route.call_count == 1


@pytest.mark.parametrize(
    "condition",
    [
        "javascript:gs.getUserID()",
        "active=javascript:gs.getUserID()",
        "active=true^EQ^javascript:gs.getUserID()",
        "missing_field=true",
    ],
)
async def test_reference_qualifier_rejects_scripts_and_unknown_dictionary_fields(
    settings, condition
):
    flow = subflow()
    flow["inputs"] = [
        {**saved_field("record", "reference", 10), "reference": "task", "table": "task"}
    ]
    before = copy.deepcopy(flow)
    coordinator, tools = fixtures(settings, flow)
    qualifier_routes(qualifier_dictionary())
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_field",
                    "collection": "inputs",
                    "name": "record",
                    "reference_qualifier": condition,
                }
            ],
        )
    )
    assert result["status"] == "error", result
    assert len(coordinator.store) == 0 and flow == before
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("drift", ["type", "revision", "removed"])
async def test_reference_qualifier_dictionary_drift_blocks_apply_before_save(
    settings, drift
):
    flow = subflow()
    flow["inputs"] = [
        {**saved_field("record", "reference", 10), "reference": "task", "table": "task"}
    ]
    before = copy.deepcopy(flow)
    _, tools = fixtures(settings, flow)
    observed = qualifier_dictionary()
    qualifier_routes(observed)
    route = http_mock.route(
        method="PUT", url=f"{BASE}/api/now/processflow/flow"
    ).respond(500)
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_field",
                    "collection": "inputs",
                    "name": "record",
                    "reference_qualifier": "active=true",
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    if drift == "removed":
        observed.pop("active")
    else:
        observed["active"]["internal_type" if drift == "type" else "sys_mod_count"] = (
            "string" if drift == "type" else "5"
        )
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "error", result
    if drift != "removed":
        assert result["error"]["code"] == "STALE_PREVIEW", result
    assert not route.called and flow == before
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_reference_qualifier_change_does_not_implicitly_discard_a_default(
    settings,
):
    flow = subflow()
    row = saved_field("record", "reference", 10)
    row.update(reference="task", table="task", defaultValue="e" * 32)
    flow["inputs"] = [row]
    await rejected(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "record",
                "reference_qualifier": "active=true",
            }
        ],
        message="explicit default_value",
    )


@pytest.mark.parametrize("kind", ["integer", "object", "array.reference"])
async def test_reference_qualifier_is_only_a_scalar_reference_setting(settings, kind):
    flow = subflow()
    row = (
        saved_array("value")
        if kind == "array.reference"
        else saved_field("value", kind, 10)
    )
    if kind == "object":
        row["children"] = []
    if kind == "array.reference":
        row.update(type=kind, reference="task", table="task")
        row["attributes"].update(child_type="reference", child_type_label="Reference")
    flow["inputs"] = [row]
    await rejected(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "value",
                "reference_qualifier": "active=true",
            }
        ],
    )


@pytest.mark.parametrize(
    "new_name",
    ["", "CamelCase", "two.parts", "has-hyphen", "1first", "a" * 81, None, False],
)
async def test_new_name_is_one_local_native_identifier(settings, new_name):
    await rejected(
        settings,
        subflow(),
        [
            {
                "op": "create_field",
                "collection": "inputs",
                "name": "value",
                "label": "Value",
                "type": "integer",
            },
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "value",
                "new_name": new_name,
            },
        ],
    )


@pytest.mark.parametrize(
    "name,new_name", [("payload.count", "details"), ("payload.details", "tags")]
)
async def test_nested_rename_rejects_existing_sibling_names(settings, name, new_name):
    await rejected(
        settings,
        saved_tree(),
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": name,
                "new_name": new_name,
            },
        ],
        message="already exists",
    )


@pytest.mark.parametrize(
    "edit",
    [
        {
            "op": "create_field",
            "name": "new",
            "label": "New",
            "type": "integer",
            "new_name": "renamed",
        },
        {"op": "delete_field", "name": "payload", "new_name": "renamed"},
        {"op": "update_field", "name": "payload", "reset_structure": True},
        {
            "op": "update_field",
            "name": "payload",
            "type": "integer",
            "reset_structure": "true",
        },
    ],
)
async def test_name_and_reset_modes_require_their_explicit_edit_operation(
    settings, edit
):
    await rejected(settings, saved_tree(), [{"collection": "inputs", **edit}])


@pytest.mark.parametrize("value", [False, 0, "retained"])
async def test_parent_stored_value_is_not_lost_by_a_shape_change(settings, value):
    flow = subflow()
    kind = (
        "boolean" if value is False else "integer" if type(value) is int else "string"
    )
    row = saved_field("payload", kind, 10)
    row["value"] = value
    flow["inputs"] = [row]
    await rejected(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "payload",
                "type": "object",
            },
        ],
        message="stored value",
    )


async def test_scalar_default_cannot_be_cleared_implicitly_in_the_type_change(settings):
    flow = subflow()
    row = saved_field("payload", "integer", 10)
    row["defaultValue"] = 0
    flow["inputs"] = [row]
    await rejected(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "payload",
                "type": "object",
                "default_value": None,
            },
        ],
        message="Clear the scalar default explicitly",
    )


async def test_nested_parent_scalar_object_array_scalar_cutover_preserves_ownership(
    settings,
):
    flow = saved_tree()
    fixtures(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "payload.count",
                "type": "object",
            },
            {
                "op": "create_field",
                "collection": "inputs",
                "parent": "payload.count",
                "name": "value",
                "label": "Value",
                "type": "integer",
            },
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "payload.count",
                "type": "array.integer",
                "reset_structure": True,
                "max_rows": 9,
            },
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "payload.count",
                "type": "integer",
                "reset_structure": True,
            },
        ],
    )
    assert result["payload"]["inputs"] == flow["inputs"]
    assert result["created_fields"] == []


@pytest.mark.parametrize("maximum", [1, 17, None])
async def test_max_rows_can_be_authored_on_a_new_nested_array(settings, maximum):
    flow = saved_tree()
    fixtures(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "inputs",
                "parent": "payload",
                "name": "more",
                "label": "More",
                "type": "array.integer",
                "max_rows": maximum,
            }
        ],
    )
    root = result["payload"]["inputs"][0]
    row = root["children"][-1]
    uid = row["uiUniqueId"]
    assert row == {
        "name": "payload.more",
        "label": "More",
        "type": "array.integer",
        "type_label": "Array.Integer",
        "order": 4,
        "path": 3,
        "parent": "payload",
        "children": [],
        "mandatory": False,
        "hint": "",
        "uiUniqueId": uid,
        "parentId": "declaration-10",
        "attributes": {
            "uiUniqueId": uid,
            "sourceUiUniqueId": "",
            "sourceType": "",
            "sourceId": "",
        },
        "sourceUiUniqueId": "",
        "sourceType": "",
        "sourceId": "",
        "fieldFacetMap": {
            "uiUniqueId": uid,
            "max_rows": maximum,
            "child_name": "more_child0",
            "child_label": "More_child0",
            "child_type": "integer",
            "child_type_label": "Integer",
            "child_hint": "",
            "child_maxsize": "",
            "child_uiUniqueId": row["fieldFacetMap"]["child_uiUniqueId"],
        },
    }
    expected_existing = copy.deepcopy(flow["inputs"][0]["children"])
    for position, child in enumerate(expected_existing, start=1):
        child["order"] = position
    assert root["children"][:-1] == expected_existing


@pytest.mark.parametrize(
    "defect", [None, "identity", "schema", "opaque", "resurrected_child"]
)
async def test_shape_cutover_saved_readback_rejects_identity_or_schema_loss(
    settings, defect
):
    flow = saved_tree()
    before = copy.deepcopy(flow)
    expected = copy.deepcopy(flow["inputs"][0])
    expected.update(type="integer", type_label="Integer")
    expected.pop("children")
    _, tools = fixtures(settings, flow)

    def save(request):
        body = json.loads(request.content)
        assert body["inputs"] == [expected]
        actual = copy.deepcopy(body)
        if defect == "identity":
            actual["inputs"][0]["id"] = "f" * 32
        elif defect == "schema":
            actual["inputs"][0]["type"] = "string"
        elif defect == "opaque":
            actual["inputs"][0]["attributes"]["vendor"]["preserve"] = []
        elif defect == "resurrected_child":
            actual["inputs"][0]["children"] = copy.deepcopy(
                before["inputs"][0]["children"]
            )
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": copy.deepcopy(body)}})

    route = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_field",
                    "collection": "inputs",
                    "name": "payload",
                    "type": "integer",
                    "reset_structure": True,
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    assert flow == before and not route.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    if defect is None:
        assert applied["status"] == "success", applied
    else:
        assert applied["error"]["code"] == "UNKNOWN_OUTCOME", applied
    assert route.call_count == 1 and flow == before


async def test_reference_qualifier_explicit_clear_keeps_reference_identity_and_empty_default(
    settings,
):
    flow = subflow()
    row = saved_field("record", "reference", 10)
    row.update(
        reference="task", table="task", ref_qual="active=true", defaultValue="e" * 32
    )
    flow["inputs"] = [row]
    fixtures(settings, flow)
    qualifier_routes(qualifier_dictionary())
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "record",
                "reference_qualifier": "",
                "default_value": "",
            }
        ],
    )
    assert result["payload"]["inputs"] == [{**row, "ref_qual": "", "defaultValue": ""}]


DEPENDENT_LABELS = {
    "document_id": "Record ID",
    "conditions": "Conditions",
    "template_value": "Template",
    "field_list": "Field List",
}


def native_dependent_types():
    return http_mock.get(f"{BASE}/api/now/processflow/field_types").respond(
        200,
        json={
            "result": {
                "data": [
                    {"value": kind, "label": label}
                    for kind, label in DEPENDENT_LABELS.items()
                ]
            }
        },
    )


def dependent_subflow(kind, *, dependency="record", static=False):
    flow = subflow()
    source = saved_field("record", "reference", 30)
    source.update(reference="task", table="task")
    dependent = saved_field("selection", kind, 31)
    dependent.update(
        type_label=DEPENDENT_LABELS[kind],
        dependent_on="" if static else dependency,
        use_dependent=not static,
    )
    if static:
        dependent.update(reference="task", table="task")
    flow["inputs"] = [source, dependent]
    return flow


@pytest.mark.parametrize("kind", list(DEPENDENT_LABELS))
async def test_native_dependent_input_creation_keeps_exact_payload_and_sibling_identity(
    settings, kind
):
    flow = dependent_subflow(kind)
    source = copy.deepcopy(flow["inputs"][0])
    flow["inputs"] = [source]
    fixtures(settings, flow)
    native_dependent_types()
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "inputs",
                "name": "selection",
                "label": "Selection",
                "type": kind,
                "dependent_on": "record",
            }
        ],
    )
    actual = planned["payload"]["inputs"][1]
    uid = actual["uiUniqueId"]
    assert planned["payload"]["inputs"] == [
        source,
        {
            "name": "selection",
            "label": "Selection",
            "type": kind,
            "type_label": DEPENDENT_LABELS[kind],
            "order": 1,
            "path": 1,
            "parent": "",
            "mandatory": False,
            "hint": "",
            "uiUniqueId": uid,
            "attributes": {
                "uiUniqueId": uid,
                "sourceUiUniqueId": "",
                "sourceType": "",
                "sourceId": "",
            },
            "sourceUiUniqueId": "",
            "sourceType": "",
            "sourceId": "",
            "dependent_on": "record",
            "use_dependent": True,
        },
    ]
    assert "defaultValue" not in actual and "value" not in actual


@pytest.mark.parametrize("kind", list(DEPENDENT_LABELS))
async def test_dependent_and_static_table_modes_switch_only_with_explicit_clears(
    settings, kind
):
    flow = dependent_subflow(kind, static=True)
    before = copy.deepcopy(flow)
    fixtures(settings, flow)
    native_dependent_types()
    qualifier_routes(qualifier_dictionary())
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "selection",
                "reference": "",
                "dependent_on": "record",
            }
        ],
    )
    expected = copy.deepcopy(before["inputs"])
    expected[1].update(
        reference="", table="", dependent_on="record", use_dependent=True
    )
    assert planned["payload"]["inputs"] == expected
    fixtures(settings, planned["payload"])
    qualifier_routes(qualifier_dictionary())
    restored = await plan(
        settings,
        planned["payload"],
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "selection",
                "reference": "task",
                "dependent_on": "",
            }
        ],
    )
    assert restored["payload"]["inputs"] == before["inputs"]


@pytest.mark.parametrize(
    "static,change",
    [
        (True, {"dependent_on": "record"}),
        (False, {"reference": "task"}),
    ],
)
async def test_dependent_mode_never_implicitly_clears_reference_or_dependency(
    settings, static, change
):
    flow = dependent_subflow("document_id", static=static)
    native_dependent_types()
    await rejected(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "selection",
                **change,
            }
        ],
    )


@pytest.mark.parametrize(
    "dependency", ["missing", "selection", "inputs.record", "record.child", None, False]
)
async def test_authored_dependency_requires_a_real_distinct_bare_sibling_name(
    settings, dependency
):
    flow = dependent_subflow("document_id")
    flow["inputs"].pop()
    native_dependent_types()
    await rejected(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "inputs",
                "name": "selection",
                "label": "Selection",
                "type": "document_id",
                "dependent_on": dependency,
            }
        ],
    )


async def test_saved_dependency_cycle_is_not_preserved_by_an_edit(settings):
    flow = dependent_subflow("document_id")
    flow["inputs"][0].update(
        type="document_id",
        type_label="Record ID",
        reference="",
        table="",
        dependent_on="selection",
        use_dependent=True,
    )
    native_dependent_types()
    await rejected(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "selection",
                "hint": "Cannot retain a cycle",
            }
        ],
        message="cycle",
    )


@pytest.mark.parametrize("kind", list(DEPENDENT_LABELS))
async def test_dependent_input_can_be_unconfigured_and_dependency_can_be_explicitly_cleared(
    settings, kind
):
    flow = subflow()
    fixtures(settings, flow)
    native_dependent_types()
    created = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "inputs",
                "name": "selection",
                "label": "Selection",
                "type": kind,
            }
        ],
    )
    actual = created["payload"]["inputs"][0]
    uid = actual["uiUniqueId"]
    assert actual == {
        "name": "selection",
        "label": "Selection",
        "type": kind,
        "type_label": DEPENDENT_LABELS[kind],
        "order": 1,
        "path": 0,
        "parent": "",
        "mandatory": False,
        "hint": "",
        "uiUniqueId": uid,
        "attributes": {
            "uiUniqueId": uid,
            "sourceUiUniqueId": "",
            "sourceType": "",
            "sourceId": "",
        },
        "sourceUiUniqueId": "",
        "sourceType": "",
        "sourceId": "",
    }
    configured = dependent_subflow(kind)
    fixtures(settings, configured)
    cleared = await plan(
        settings,
        configured,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "selection",
                "dependent_on": "",
            }
        ],
    )
    expected = copy.deepcopy(configured["inputs"])
    expected[1].update(dependent_on="", use_dependent=False)
    assert cleared["payload"]["inputs"] == expected


@pytest.mark.parametrize(
    "collection,kind",
    [
        ("outputs", "document_id"),
        ("flowVariables", "document_id"),
        ("inputs", "integer"),
    ],
)
async def test_dependency_setting_rejects_wrong_collection_or_type(
    settings, collection, kind
):
    native_dependent_types()
    await rejected(
        settings,
        subflow(),
        [
            {
                "op": "create_field",
                "collection": collection,
                "name": "selection",
                "label": "Selection",
                "type": kind,
                "dependent_on": "record",
            }
        ],
    )


@pytest.mark.parametrize(
    "kind,value",
    [
        ("document_id", "e" * 32),
        ("conditions", "active=true"),
        ("template_value", "short_description=VPN"),
        ("field_list", "short_description"),
    ],
)
async def test_dependent_input_cannot_store_a_default_without_a_static_table(
    settings, kind, value
):
    native_dependent_types()
    flow = dependent_subflow(kind)
    await rejected(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "selection",
                "default_value": value,
            }
        ],
        message="defaults only when the dependent declaration has a static table",
    )


async def test_switching_to_dependency_does_not_silently_preserve_a_static_default(
    settings,
):
    flow = dependent_subflow("conditions", static=True)
    flow["inputs"][1]["defaultValue"] = "active=true"
    native_dependent_types()
    await rejected(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "selection",
                "reference": "",
                "dependent_on": "record",
            }
        ],
    )
    fixtures(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "selection",
                "reference": "",
                "dependent_on": "record",
                "default_value": "",
            }
        ],
    )
    expected = copy.deepcopy(flow["inputs"])
    expected[1].update(
        reference="",
        table="",
        dependent_on="record",
        use_dependent=True,
        defaultValue="",
    )
    assert result["payload"]["inputs"] == expected


@pytest.mark.parametrize("dependency", ["record", "inputs.record"])
@pytest.mark.parametrize(
    "change", [{"op": "delete_field"}, {"op": "update_field", "new_name": "renamed"}]
)
async def test_native_dependency_consumers_block_source_input_delete_and_rename(
    settings, dependency, change
):
    flow = dependent_subflow("document_id", dependency=dependency)
    native_dependent_types()
    await rejected(
        settings, flow, [{"collection": "inputs", "name": "record", **change}]
    )
