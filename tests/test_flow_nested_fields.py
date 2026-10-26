"""Nested declaration contracts derived from Studio source, not live saves."""

import copy
import json

import httpx2
import pytest

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import data, declarations, semantics
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_data_edits import typed_flow
from tests.test_flow_editing import BASE, FLOW, fixtures, persist
from tests.test_flow_fields import plan
from tests.test_flow_output_assignments import routes


def subflow():
    flow = typed_flow()
    flow["type"] = "subflow"
    return flow


def object_edits(collection="inputs"):
    return [
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
            "name": "count",
            "label": "Count",
            "type": "integer",
        },
        {
            "op": "create_field",
            "collection": collection,
            "parent": "payload",
            "name": "details",
            "label": "Details",
            "type": "object",
        },
        {
            "op": "create_field",
            "collection": collection,
            "parent": "payload.details",
            "name": "message",
            "label": "Message",
            "type": "string",
        },
    ]


@pytest.mark.parametrize("collection", ["inputs", "outputs"])
async def test_recursive_object_crud_and_pills(settings, collection):
    flow = subflow()
    fixtures(settings, flow)
    edits = object_edits(collection) + [
        {
            "op": "update_field",
            "collection": collection,
            "name": "payload.details.message",
            "hint": "Message hint",
        }
    ]
    if collection == "inputs":
        edits.append(
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {
                    "count": "{{subflow.payload.count}}",
                    "log_message": "{{subflow.payload.details.message}}",
                },
            }
        )
    result = await plan(settings, flow, edits)
    root = result["payload"][collection][0]
    assert root["type"] == "object"
    assert [row["name"] for row in root["children"]] == [
        "payload.count",
        "payload.details",
    ]
    leaf = root["children"][1]["children"][0]
    assert leaf["hint"] == "Message hint"
    assert leaf["fieldFacetMap"]["uiUniqueId"] == leaf["uiUniqueId"]
    assert len(result["created_fields"]) == 4
    if collection == "inputs":
        assert {row["name"] for row in result["payload"]["label_cache"]} == {
            "subflow.payload.count",
            "subflow.payload.details.message",
        }
    saved = result["payload"]
    fixtures(settings, saved)
    removed = await plan(
        settings,
        saved,
        [{"op": "delete_field", "collection": collection, "name": "payload.details"}]
        if collection == "outputs"
        else [
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"log_message": "no dependency"},
            },
            {"op": "delete_field", "collection": collection, "name": "payload.details"},
        ],
    )
    assert [row["name"] for row in removed["payload"][collection][0]["children"]] == [
        "payload.count"
    ]


@pytest.mark.parametrize(
    "kind,item_type",
    [
        ("array.string", "string"),
        ("array.integer", "integer"),
        ("array.boolean", "boolean"),
        ("array.object", "object"),
        ("array.reference", "reference"),
    ],
)
async def test_array_item_metadata_packing_and_nested_facets(settings, kind, item_type):
    flow = subflow()
    fixtures(settings, flow)
    if kind == "array.reference":
        http_mock.get(f"{BASE}/api/now/table/sys_db_object").respond(
            200, json={"result": [{"name": "sc_req_item"}]}
        )
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "inputs",
                "name": "root",
                "label": "Root",
                "type": "object",
            },
            {
                "op": "create_field",
                "collection": "inputs",
                "parent": "root",
                "name": "items",
                "label": "Items",
                "type": kind,
                **({"reference": "sc_req_item"} if kind == "array.reference" else {}),
            },
            {
                "op": "create_field",
                "collection": "outputs",
                "name": "values",
                "label": "Values",
                "type": kind,
                **({"reference": "sc_req_item"} if kind == "array.reference" else {}),
            },
        ],
    )
    nested = result["payload"]["inputs"][0]["children"][0]
    top = result["payload"]["outputs"][0]
    for row, storage in [(nested, "fieldFacetMap"), (top, "attributes")]:
        assert row[storage]["child_type"] == item_type
        assert row[storage]["child_uiUniqueId"] != row["uiUniqueId"]
        assert row.get("children", []) == []
        assert data.children(row)[0]["type"] == item_type
        if kind == "array.reference":
            assert row["reference"] == "sc_req_item"
            assert data.children(row)[0]["reference"] == "sc_req_item"
            assert "child_reference" not in row[storage]
            assert declarations.pack(declarations.unpack(row)) == row
    assert top["attributes"]["child_name"] == "values_child0"


async def test_array_object_item_child_edit_uses_packed_schema(settings):
    flow = subflow()
    fixtures(settings, flow)
    result = await plan(
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
                "op": "create_field",
                "collection": "inputs",
                "parent": "rows.rows_child0",
                "name": "count",
                "label": "Count",
                "type": "integer",
            },
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "rows.rows_child0",
                "hint": "Item hint",
            },
        ],
    )
    row = result["payload"]["inputs"][0]
    assert row["attributes"]["child_hint"] == "Item hint"
    assert row["children"][0]["name"] == "rows.rows_child0.count"
    assert data.children(row)[0]["children"][0]["type"] == "integer"


async def test_complex_flow_variables_remain_unavailable_per_studio_type_picker(
    settings,
):
    flow = typed_flow()
    coordinator, tools = fixtures(settings, flow)
    result = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "create_field",
                    "collection": "flowVariables",
                    "name": "obj",
                    "label": "Object",
                    "type": "object",
                }
            ],
        )
    )
    assert result["error"]["code"] == "UNAVAILABLE"
    assert len(coordinator.store) == 0


@pytest.mark.parametrize("collection", ["inputs", "outputs"])
@pytest.mark.parametrize(
    "defect",
    [
        None,
        "leaf_id_race",
        "duplicate_id",
        "facet",
        "item_identity",
        "value",
        "missing_child",
        "parent",
    ],
)
async def test_nested_save_identity_and_opaque_metadata_are_strict(
    settings, collection, defect
):
    flow = subflow()
    _, tools = fixtures(settings, flow)
    edits = object_edits(collection) + [
        {
            "op": "create_field",
            "collection": collection,
            "parent": "payload",
            "name": "tags",
            "label": "Tags",
            "type": "array.string",
        },
    ]

    def save(request):
        body = json.loads(request.content)
        for i, (_, node) in enumerate(declarations.walk(body[collection]), start=10):
            node["id"] = f"{i:032x}"
        echo = copy.deepcopy(body)
        actual = copy.deepcopy(echo)
        root = actual[collection][0]
        if defect == "leaf_id_race":
            root["children"][0]["id"] = "a" * 32
        elif defect == "duplicate_id":
            root["children"][0]["id"] = root["id"]
        elif defect == "facet":
            root["children"][-1]["fieldFacetMap"]["unknown"] = False
        elif defect == "item_identity":
            root["children"][-1]["fieldFacetMap"]["child_uiUniqueId"] = "different"
        elif defect == "value":
            root["children"][0]["defaultValue"] = 7
        elif defect == "missing_child":
            root["children"].pop()
        elif defect == "parent":
            root["children"][0]["parent"] = "other"
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "success", preview
    schema = preview["data"]["summary"]["fields"][collection][0]
    assert schema["children"][-1]["children"][0]["type"] == "string"
    assert not write.called
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    if defect:
        assert applied["error"]["code"] == "UNKNOWN_OUTCOME", applied
    else:
        assert applied["status"] == "success", applied
    assert write.call_count == 1


@pytest.mark.parametrize("operation", ["delete_field", "update_field"])
@pytest.mark.parametrize(
    "path", ["payload", "payload.details", "payload.details.message"]
)
async def test_nested_deletion_and_type_changes_reject_actual_uses(
    settings, operation, path
):
    flow = subflow()
    fixtures(settings, flow)
    edits = object_edits() + [
        {
            "op": "update_inputs",
            "step": "ui-2",
            "inputs": {"log_message": "{{subflow.payload.details.message}}"},
        },
        {
            "op": operation,
            "collection": "inputs",
            "name": path,
            **({"type": "integer"} if operation == "update_field" else {}),
        },
    ]
    with pytest.raises(CapabilityError):
        await plan(settings, flow, edits)
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_nested_unused_sibling_can_be_removed_with_used_leaf_preserved(settings):
    flow = subflow()
    fixtures(settings, flow)
    result = await plan(
        settings,
        flow,
        object_edits()
        + [
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"count": "{{subflow.payload.count}}"},
            },
            {"op": "delete_field", "collection": "inputs", "name": "payload.details"},
        ],
    )
    assert result["payload"]["inputs"][0]["children"][0]["name"] == "payload.count"
    assert result["payload"]["label_cache"][0]["name"] == "subflow.payload.count"


async def test_packed_roundtrip_and_edits_preserve_unknown_reference_and_array_facets(
    settings,
):
    flow = subflow()
    fixtures(settings, flow)
    result = await plan(
        settings,
        flow,
        object_edits()
        + [
            {
                "op": "create_field",
                "collection": "inputs",
                "parent": "payload",
                "name": "tags",
                "label": "Tags",
                "type": "array.string",
            },
        ],
    )
    saved = result["payload"]
    root = saved["inputs"][0]
    root["attributes"]["vendor"] = {"keep": [False, 0]}
    root["children"][-1]["fieldFacetMap"]["private_facet"] = {"keep": True}
    root["children"][-1]["attributes"]["opaque"] = 0
    root["children"].append(
        {
            "id": "b" * 32,
            "name": "payload.existing_ref",
            "parent": "payload",
            "type": "reference",
            "reference": "sc_req_item",
            "fieldFacetMap": {"opaque": True},
        }
    )
    assert declarations.pack(declarations.unpack(root)) == root
    fixtures(settings, saved)
    http_mock.get(f"{BASE}/api/now/table/sys_db_object").respond(
        200, json={"result": [{"name": "sc_req_item"}]}
    )
    updated = await plan(
        settings,
        saved,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "payload.details.message",
                "label": "Renamed message",
            }
        ],
    )
    changed = updated["payload"]["inputs"][0]
    assert changed["attributes"] == root["attributes"]
    assert changed["children"][-2:] == root["children"][-2:]


async def test_array_item_metadata_update_preserves_identity_and_fixed_type(settings):
    flow = subflow()
    fixtures(settings, flow)
    created = (
        await plan(
            settings,
            flow,
            [
                {
                    "op": "create_field",
                    "collection": "inputs",
                    "name": "tags",
                    "label": "Tags",
                    "type": "array.string",
                }
            ],
        )
    )["payload"]
    fixtures(settings, created)
    before = created["inputs"][0]["attributes"]
    updated = await plan(
        settings,
        created,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "tags.tags_child0",
                "maxsize": 64,
                "hint": "Tag",
            }
        ],
    )
    after = updated["payload"]["inputs"][0]["attributes"]
    assert after == {**before, "child_maxsize": 64, "child_hint": "Tag"}
    for edit in [{"op": "delete_field"}, {"op": "update_field", "type": "integer"}]:
        with pytest.raises(CapabilityError):
            await plan(
                settings,
                created,
                [{**edit, "collection": "inputs", "name": "tags.tags_child0"}],
            )


@pytest.mark.parametrize(
    "defect",
    ["duplicate_ui", "facet_identity", "orphan", "duplicate_name", "item_type"],
)
async def test_nested_registration_conflicts_block_edits(settings, defect):
    flow = subflow()
    fixtures(settings, flow)
    saved = (
        await plan(
            settings,
            flow,
            object_edits()
            + [
                {
                    "op": "create_field",
                    "collection": "inputs",
                    "parent": "payload",
                    "name": "tags",
                    "label": "Tags",
                    "type": "array.string",
                }
            ],
        )
    )["payload"]
    root = saved["inputs"][0]
    if defect == "duplicate_ui":
        root["children"][0]["uiUniqueId"] = root["uiUniqueId"]
        root["children"][0]["fieldFacetMap"]["uiUniqueId"] = root["uiUniqueId"]
        root["children"][0]["attributes"]["uiUniqueId"] = root["uiUniqueId"]
    elif defect == "facet_identity":
        root["children"][0]["fieldFacetMap"]["uiUniqueId"] = "different"
    elif defect == "orphan":
        root["children"][0]["parent"] = "wrong"
    elif defect == "duplicate_name":
        root["children"].append(copy.deepcopy(root["children"][0]))
    else:
        root["children"][-1]["fieldFacetMap"]["child_type"] = "integer"
    coordinator, tools = fixtures(settings, saved)
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_field",
                    "collection": "inputs",
                    "name": "payload.details.message",
                    "hint": "Updated",
                }
            ],
        )
    )
    assert preview["status"] == "error", preview
    assert len(coordinator.store) == 0


async def test_saved_nested_ids_remain_strict_while_new_child_receives_id(settings):
    flow = subflow()
    fixtures(settings, flow)
    saved = (await plan(settings, flow, object_edits()))["payload"]
    for i, (_, row) in enumerate(declarations.walk(saved["inputs"]), start=10):
        row["id"] = f"{i:032x}"
    fixtures(settings, saved)
    planned = await plan(
        settings,
        saved,
        [
            {
                "op": "create_field",
                "collection": "inputs",
                "parent": "payload.details",
                "name": "flag",
                "label": "Flag",
                "type": "boolean",
            }
        ],
    )
    actual = copy.deepcopy(planned["payload"])
    actual["inputs"][0]["children"][1]["children"][-1]["id"] = "c" * 32
    semantics.compare_saved(
        planned["payload"],
        actual,
        generated_ids=set(),
        generated_fields=planned["created_fields"],
    )
    actual["inputs"][0]["children"][0]["id"] = "d" * 32
    with pytest.raises(CapabilityError):
        semantics.compare_saved(
            planned["payload"],
            actual,
            generated_ids=set(),
            generated_fields=planned["created_fields"],
        )


async def test_deleted_nested_id_cannot_reappear_under_new_name(settings):
    flow = subflow()
    fixtures(settings, flow)
    saved = (await plan(settings, flow, object_edits()))["payload"]
    saved["inputs"][0]["children"][0]["id"] = "a" * 32
    fixtures(settings, saved)
    planned = await plan(
        settings,
        saved,
        [
            {"op": "delete_field", "collection": "inputs", "name": "payload.count"},
            {
                "op": "create_field",
                "collection": "inputs",
                "parent": "payload",
                "name": "new_count",
                "label": "New count",
                "type": "integer",
            },
        ],
    )
    actual = copy.deepcopy(planned["payload"])
    actual["inputs"][0]["children"][-1]["id"] = "a" * 32
    with pytest.raises(CapabilityError):
        semantics.compare_saved(
            planned["payload"],
            actual,
            generated_ids=set(),
            generated_fields=planned["created_fields"],
            removed_fields=planned["removed_fields"],
        )


@pytest.mark.parametrize("kind", ["reference", "json"])
async def test_unsupported_child_types_do_not_guess_metadata(settings, kind):
    flow = subflow()
    coordinator, tools = fixtures(settings, flow)
    edits = object_edits() + [
        {
            "op": "create_field",
            "collection": "inputs",
            "parent": "payload",
            "name": "unsupported",
            "label": "Unsupported",
            "type": kind,
            **({"reference": "task"} if kind == "reference" else {}),
        }
    ]
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "error"
    assert len(coordinator.store) == 0


async def test_new_nested_ids_are_unique_across_same_shape_roots_and_arrays(settings):
    flow = subflow()
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "inputs",
                "name": "first",
                "label": "First",
                "type": "array.object",
            },
            {
                "op": "create_field",
                "collection": "inputs",
                "name": "second",
                "label": "Second",
                "type": "array.object",
            },
            {
                "op": "create_field",
                "collection": "inputs",
                "parent": "first.first_child0",
                "name": "count",
                "label": "Count",
                "type": "integer",
            },
            {
                "op": "create_field",
                "collection": "inputs",
                "parent": "second.second_child0",
                "name": "count",
                "label": "Count",
                "type": "integer",
            },
        ],
    )
    expanded = [declarations.unpack(row) for row in planned["payload"]["inputs"]]
    ids = [declarations.identity(row) for _, row in declarations.walk(expanded)]
    assert len(ids) == len(set(ids)) == 6


async def test_nested_array_roundtrip_does_not_drop_unknown_attributes(settings):
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
                "op": "create_field",
                "collection": "inputs",
                "parent": "rows.rows_child0",
                "name": "tags",
                "label": "Tags",
                "type": "array.string",
            },
        ],
    )
    root = planned["payload"]["inputs"][0]
    root["attributes"]["unknown"] = {"keep": False}
    root["children"][0]["fieldFacetMap"]["unknown"] = [0, "opaque"]
    assert declarations.pack(declarations.unpack(root)) == root


async def test_existing_relative_child_names_are_preserved_and_resolve_as_pills(
    settings,
):
    flow = subflow()
    flow["inputs"] = [
        {
            "id": "a" * 32,
            "name": "obj",
            "label": "Object",
            "type": "object",
            "children": [
                {
                    "id": "b" * 32,
                    "name": "count",
                    "type": "integer",
                    "label": "Count",
                    "vendor": False,
                }
            ],
        }
    ]
    fixtures(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "obj.count",
                "hint": "Count hint",
            },
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"count": "{{subflow.obj.count}}"},
            },
        ],
    )
    assert result["payload"]["inputs"][0]["children"][0] == {
        **flow["inputs"][0]["children"][0],
        "hint": "Count hint",
    }
    assert result["payload"]["label_cache"][0]["name"] == "subflow.obj.count"


async def test_new_child_in_saved_object_save_reconciles_only_new_id(settings):
    flow = subflow()
    fixtures(settings, flow)
    flow = (await plan(settings, flow, object_edits()))["payload"]
    for i, (_, row) in enumerate(declarations.walk(flow["inputs"]), start=10):
        row["id"] = f"{i:032x}"
    _, tools = fixtures(settings, flow)

    def save(request):
        body = json.loads(request.content)
        body["inputs"][0]["children"][1]["children"][-1]["id"] = "d" * 32
        persist(body)
        return httpx2.Response(200, json={"result": {"data": body}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "create_field",
                    "collection": "inputs",
                    "parent": "payload.details",
                    "name": "flag",
                    "label": "Flag",
                    "type": "boolean",
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    assert write.call_count == 1


@pytest.mark.parametrize("phase", ["preview", "apply"])
async def test_nested_field_policy_and_context_revision_remain_checked(
    settings, monkeypatch, phase
):
    from servicenow_mcp import policy

    coordinator, tools = fixtures(settings, subflow())
    preview = None
    if phase == "apply":
        preview = decode_response(
            await tools["flow_preview"](
                action="edit", sys_id=FLOW, edits=object_edits()
            )
        )
        assert preview["status"] == "success", preview
    monkeypatch.setattr(
        policy, "DENIED_TABLES", policy.DENIED_TABLES | {"sys_hub_flow_input"}
    )
    result = (
        decode_response(
            await tools["mutation_apply"](
                preview_token=preview["data"]["preview_token"]
            )
        )
        if preview
        else decode_response(
            await tools["flow_preview"](
                action="edit", sys_id=FLOW, edits=object_edits()
            )
        )
    )
    assert result["status"] == "error"
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_nested_schema_change_invalidates_preview(settings):
    flow = subflow()
    fixtures(settings, flow)
    flow = (await plan(settings, flow, object_edits()))["payload"]
    _, tools = fixtures(settings, flow)
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_field",
                    "collection": "inputs",
                    "name": "payload.count",
                    "hint": "Updated",
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    changed = copy.deepcopy(flow)
    changed["inputs"][0]["children"][0]["type"] = "string"
    persist(changed)
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["error"]["code"] == "STALE_PREVIEW"
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_nested_child_default_is_typed_but_not_exposed_in_preview(settings):
    flow = subflow()
    _, tools = fixtures(settings, flow)
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "create_field",
                    "collection": "inputs",
                    "name": "obj",
                    "label": "Object",
                    "type": "object",
                },
                {
                    "op": "create_field",
                    "collection": "inputs",
                    "parent": "obj",
                    "name": "password",
                    "label": "Password",
                    "type": "string",
                    "default_value": "private-sensitive-marker",
                },
            ],
        )
    )
    assert preview["status"] == "success", preview
    assert "private-sensitive-marker" not in str(preview)


async def test_array_object_field_delete_and_add_keeps_item_identity(settings):
    flow = subflow()
    fixtures(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "outputs",
                "name": "rows",
                "label": "Rows",
                "type": "array.object",
            },
            {
                "op": "create_field",
                "collection": "outputs",
                "parent": "rows.rows_child0",
                "name": "first",
                "label": "First",
                "type": "string",
            },
            {
                "op": "create_field",
                "collection": "outputs",
                "parent": "rows.rows_child0",
                "name": "second",
                "label": "Second",
                "type": "integer",
            },
        ],
    )
    saved = result["payload"]
    before = copy.deepcopy(saved["outputs"][0])
    fixtures(settings, saved)
    changed = await plan(
        settings,
        saved,
        [
            {
                "op": "delete_field",
                "collection": "outputs",
                "name": "rows.rows_child0.first",
            },
            {
                "op": "create_field",
                "collection": "outputs",
                "parent": "rows.rows_child0",
                "name": "third",
                "label": "Third",
                "type": "boolean",
            },
        ],
    )
    row = changed["payload"]["outputs"][0]
    assert row["attributes"] == before["attributes"]
    assert row["children"][0] == {**before["children"][1], "order": 1}
    assert row["children"][1]["name"] == "rows.rows_child0.third"


async def test_relative_array_object_field_identity_path_includes_synthetic_item(
    settings,
):
    flow = subflow()
    fixtures(settings, flow)
    flow = (
        await plan(
            settings,
            flow,
            [
                {
                    "op": "create_field",
                    "collection": "inputs",
                    "name": "rows",
                    "label": "Rows",
                    "type": "array.object",
                }
            ],
        )
    )["payload"]
    flow["inputs"][0]["children"] = [
        {
            "name": "relative",
            "label": "Relative",
            "type": "string",
            "uiUniqueId": "relative-ui",
            "fieldFacetMap": {"uiUniqueId": "relative-ui"},
        }
    ]
    fixtures(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "rows.rows_child0.relative",
                "hint": "Updated",
            }
        ],
    )
    node = result["payload"]["inputs"][0]["children"][0]
    assert node["name"] == "relative" and node["hint"] == "Updated"
    assert (
        declarations.walk(result["payload"]["inputs"])[1][0]
        == "rows.rows_child0.relative"
    )


async def test_packed_item_unrepresented_settings_are_not_silently_accepted(settings):
    flow = subflow()
    fixtures(settings, flow)
    flow = (
        await plan(
            settings,
            flow,
            [
                {
                    "op": "create_field",
                    "collection": "inputs",
                    "name": "tags",
                    "label": "Tags",
                    "type": "array.string",
                }
            ],
        )
    )["payload"]
    fixtures(settings, flow)
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "update_field",
                    "collection": "inputs",
                    "name": "tags.tags_child0",
                    "mandatory": True,
                }
            ],
        )


async def test_nested_edit_native_protocol_and_schema_validation(settings):
    import anyio
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.mcp.inputs import NativeMCPServer
    from tests.helpers import register_mutations

    flow = subflow()
    fixtures(settings, flow)
    server = NativeMCPServer("nested-wire")
    register_mutations(
        server, settings, OAuthPKCEProvider(settings), frozenset({"flows"})
    )

    def save(request):
        body = json.loads(request.content)
        for i, (_, row) in enumerate(declarations.walk(body["inputs"]), start=10):
            row["id"] = f"{i:032x}"
        persist(body)
        return httpx2.Response(200, json={"result": {"data": body}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    async with (
        create_client_server_memory_streams() as (client_streams, server_streams),
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(
            server._lowlevel_server.run,
            *server_streams,
            server._lowlevel_server.create_initialization_options(),
        )
        async with ClientSession(*client_streams) as session:
            await session.initialize()
            preview = await session.call_tool(
                "flow_preview",
                {
                    "action": "edit",
                    "sys_id": FLOW,
                    "edits": object_edits()
                    + [
                        {
                            "op": "update_inputs",
                            "step": "ui-2",
                            "inputs": {"count": "{{subflow.payload.count}}"},
                        }
                    ],
                },
            )
            await session.validate_tool_result("flow_preview", preview)
            assert not preview.is_error, preview
            assert not write.called
            applied = await session.call_tool(
                "mutation_apply",
                {"preview_token": preview.structured_content["data"]["preview_token"]},
            )
            await session.validate_tool_result("mutation_apply", applied)
            assert not applied.is_error, applied
            assert write.call_count == 1
        tasks.cancel_scope.cancel()


async def test_nested_declarations_do_not_allow_production_preview(prod_settings):
    coordinator, tools = fixtures(prod_settings, subflow())
    result = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=object_edits())
    )
    assert result["status"] == "error"
    assert len(coordinator.store) == 0 and not http_mock.calls


async def test_existing_reference_leaf_pill_keeps_table_and_facets(settings):
    flow = subflow()
    flow["inputs"] = [
        {
            "name": "obj",
            "label": "Object",
            "type": "object",
            "children": [
                {
                    "name": "record",
                    "label": "Record",
                    "type": "reference",
                    "reference": "sc_req_item",
                    "fieldFacetMap": {"opaque": {"keep": False}},
                }
            ],
        }
    ]
    fixtures(settings, flow)
    updated = await plan(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "obj",
                "hint": "Object hint",
            },
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"log_message": "Record {{subflow.obj.record}}"},
            },
        ],
    )
    assert updated["payload"]["inputs"][0]["children"] == flow["inputs"][0]["children"]
    assert updated["payload"]["label_cache"][0]["reference"] == "sc_req_item"


@pytest.mark.parametrize("location", ["script", "source", "transform", "default"])
async def test_nested_leaf_removal_checks_non_input_dependencies(settings, location):
    flow = subflow()
    fixtures(settings, flow)
    flow = (await plan(settings, flow, object_edits()))["payload"]
    if location == "script":
        binding = flow["actionInstances"][0]["inputs"][0]
        binding.update(
            scriptActive=True,
            script={
                binding["name"]: {
                    "script": "return fd_data.subflow_inputs.payload.details.message;",
                    "scriptActive": True,
                    "showScript": True,
                }
            },
        )
    elif location == "source":
        flow["actionInstances"][0]["inputs"][0]["sourceUiUniqueId"] = flow["inputs"][0][
            "children"
        ][1]["children"][0]["uiUniqueId"]
    elif location == "transform":
        flow["pharmacy"] = {
            "prescriptions": [{"prescription": "{{subflow.payload.details.message}}"}]
        }
    else:
        flow["outputs"] = [
            {
                "name": "text",
                "type": "string",
                "defaultValue": "{{subflow.payload.details.message}}",
            }
        ]
    fixtures(settings, flow)
    with pytest.raises(CapabilityError, match="referenced or assigned"):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "delete_field",
                    "collection": "inputs",
                    "name": "payload.details.message",
                }
            ],
        )


async def test_nested_scalar_type_update_preserves_identity_and_valid_default(settings):
    flow = subflow()
    fixtures(settings, flow)
    flow = (await plan(settings, flow, object_edits()))["payload"]
    fixtures(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "payload.count",
                "type": "string",
                "default_value": "zero",
            }
        ],
    )
    before = flow["inputs"][0]["children"][0]
    after = result["payload"]["inputs"][0]["children"][0]
    assert after == {
        **before,
        "type": "string",
        "type_label": "String",
        "defaultValue": "zero",
    }


async def test_complex_defaults_are_rejected_without_json_string_coercion(settings):
    coordinator, tools = fixtures(settings, subflow())
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "create_field",
                    "collection": "inputs",
                    "name": "object",
                    "label": "Object",
                    "type": "object",
                    "default_value": {"count": 1},
                }
            ],
        )
    )
    assert preview["error"]["code"] == "UNAVAILABLE"
    assert len(coordinator.store) == 0


async def test_nested_edit_cannot_bypass_readonly_ancestor(settings):
    flow = subflow()
    fixtures(settings, flow)
    flow = (await plan(settings, flow, object_edits()))["payload"]
    flow["inputs"][0]["readonly"] = True
    fixtures(settings, flow)
    with pytest.raises(CapabilityError, match="Read-only"):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "update_field",
                    "collection": "inputs",
                    "name": "payload.details.message",
                    "hint": "Cannot edit",
                }
            ],
        )


async def test_full_new_child_path_under_relative_saved_object_is_a_typed_pill(
    settings,
):
    flow = subflow()
    flow["inputs"] = [
        {
            "name": "payload",
            "type": "object",
            "children": [{"name": "details", "type": "object", "children": []}],
        }
    ]
    fixtures(settings, flow)
    result = await plan(
        settings,
        flow,
        [
            {
                "op": "create_field",
                "collection": "inputs",
                "parent": "payload.details",
                "name": "count",
                "label": "Count",
                "type": "integer",
            },
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"count": "{{subflow.payload.details.count}}"},
            },
        ],
    )
    assert result["payload"]["label_cache"][0]["type"] == "integer"


async def test_object_removal_cannot_implicitly_delete_readonly_child(settings):
    flow = subflow()
    fixtures(settings, flow)
    flow = (await plan(settings, flow, object_edits()))["payload"]
    flow["inputs"][0]["children"][0]["readonly"] = True
    fixtures(settings, flow)
    with pytest.raises(CapabilityError, match="Read-only"):
        await plan(
            settings,
            flow,
            [{"op": "delete_field", "collection": "inputs", "name": "payload"}],
        )


@pytest.mark.parametrize("collection", ["inputs", "outputs"])
async def test_reference_array_table_update_preserves_packed_identity(
    settings, collection
):
    flow = subflow()
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/table/sys_db_object").respond(
        200, json={"result": [{"name": "sc_req_item"}]}
    )
    saved = (
        await plan(
            settings,
            flow,
            [
                {
                    "op": "create_field",
                    "collection": collection,
                    "name": "records",
                    "label": "Records",
                    "type": "array.reference",
                    "reference": "sc_req_item",
                }
            ],
        )
    )["payload"]
    original = saved[collection][0]
    original["attributes"]["vendor"] = {"retain": False}
    fixtures(settings, saved)
    http_mock.get(f"{BASE}/api/now/table/sys_db_object").respond(
        200, json={"result": [{"name": "task"}]}
    )
    updated = await plan(
        settings,
        saved,
        [
            {
                "op": "update_field",
                "collection": collection,
                "name": "records",
                "reference": "task",
            }
        ],
    )
    actual = updated["payload"][collection][0]
    assert actual["reference"] == actual["table"] == "task"
    assert actual["attributes"] == original["attributes"]
    assert actual["uiUniqueId"] == original["uiUniqueId"]
    assert data.children(actual)[0]["reference"] == "task"
    assert declarations.pack(declarations.unpack(actual)) == actual
    fixtures(settings, updated["payload"])
    removed = await plan(
        settings,
        updated["payload"],
        [{"op": "delete_field", "collection": collection, "name": "records"}],
    )
    assert removed["payload"][collection] == []


@pytest.mark.parametrize("reference", [None, "", "Task", "task.field", "bad^table", 1])
async def test_reference_array_creation_requires_static_parent_table(
    settings, reference
):
    flow = subflow()
    fixtures(settings, flow)
    edit = {
        "op": "create_field",
        "collection": "inputs",
        "name": "records",
        "label": "Records",
        "type": "array.reference",
    }
    if reference is not None:
        edit["reference"] = reference
    with pytest.raises((CapabilityError, ValueError)):
        await plan(settings, flow, [edit])
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_reference_array_creation_rejects_unreadable_table(settings):
    flow = subflow()
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/table/sys_db_object").respond(
        200, json={"result": []}
    )
    with pytest.raises(CapabilityError, match="absent, ambiguous, or unreadable"):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "create_field",
                    "collection": "inputs",
                    "name": "records",
                    "label": "Records",
                    "type": "array.reference",
                    "reference": "task",
                },
            ],
        )


@pytest.mark.parametrize(
    "change",
    [
        {"type": "array.string", "reference": ""},
        {"name": "records.records_child0", "type": "string", "reference": ""},
        {"name": "records.records_child0", "reference": "sys_user"},
        {"op": "delete_field", "name": "records.records_child0"},
    ],
)
async def test_reference_array_item_type_table_and_identity_are_fixed(settings, change):
    flow = subflow()
    fixtures(settings, flow)
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "create_field",
                    "collection": "inputs",
                    "name": "records",
                    "label": "Records",
                    "type": "array.reference",
                    "reference": "task",
                },
                {
                    "op": "update_field",
                    "collection": "inputs",
                    "name": "records",
                    **change,
                },
            ],
        )


async def test_reference_array_nested_inside_array_object_keeps_parent_table(settings):
    flow = subflow()
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/table/sys_db_object").respond(
        200, json={"result": [{"name": "task"}]}
    )
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
                "op": "create_field",
                "collection": "inputs",
                "parent": "rows.rows_child0",
                "name": "records",
                "label": "Records",
                "type": "array.reference",
                "reference": "task",
            },
        ],
    )
    root = planned["payload"]["inputs"][0]
    nested = root["children"][0]
    assert nested["reference"] == "task"
    assert nested["fieldFacetMap"]["child_type"] == "reference"
    restored = declarations.unpack(root)
    item = restored["children"][0]["children"][0]["children"][0]
    assert item["reference"] == "task"
    assert item["uiUniqueId"] == nested["fieldFacetMap"]["child_uiUniqueId"]
    assert declarations.pack(restored) == root


async def test_reference_array_used_whole_pill_blocks_table_change_and_removal(
    settings,
):
    flow = subflow()
    routes(settings, flow)
    for operation in (
        {"op": "update_field", "reference": "sys_user"},
        {"op": "delete_field"},
    ):
        with pytest.raises(CapabilityError, match="in use|referenced or assigned"):
            await plan(
                settings,
                flow,
                [
                    {
                        "op": "create_field",
                        "collection": "inputs",
                        "name": "source",
                        "label": "Source",
                        "type": "array.reference",
                        "reference": "task",
                    },
                    {
                        "op": "create_field",
                        "collection": "outputs",
                        "name": "target",
                        "label": "Target",
                        "type": "array.reference",
                        "reference": "task",
                    },
                    {
                        "op": "assign_outputs",
                        "outputs": {"target": "{{subflow.source}}"},
                    },
                    {"collection": "inputs", "name": "source", **operation},
                ],
            )


@pytest.mark.parametrize(
    "defect",
    [
        "missing_table",
        "invalid_table",
        "wrong_type",
        "missing_identity",
        "scalar_children",
    ],
)
def test_packed_reference_array_rejects_ambiguous_item_schema(defect):
    row = {
        "name": "records",
        "type": "array.reference",
        "reference": "task",
        "children": [],
        "attributes": {
            "child_name": "records_child0",
            "child_type": "reference",
            "child_uiUniqueId": "item-ui",
            "vendor": {"retain": False},
        },
    }
    if defect == "missing_table":
        row.pop("reference")
    elif defect == "invalid_table":
        row["reference"] = "task.field"
    elif defect == "wrong_type":
        row["attributes"]["child_type"] = "string"
    elif defect == "missing_identity":
        row["attributes"].pop("child_uiUniqueId")
    else:
        row["children"] = [{"name": "undeclared", "type": "string"}]
    with pytest.raises(CapabilityError):
        declarations.unpack(row)


def test_reference_array_pack_does_not_move_parent_table_into_item_facets():
    row = {
        "name": "records",
        "type": "array.reference",
        "reference": "task",
        "children": [],
        "attributes": {
            "child_name": "records_child0",
            "child_type": "reference",
            "child_uiUniqueId": "item-ui",
            "vendor": {"retain": False},
        },
    }
    expanded = declarations.unpack(row)
    assert expanded["children"][0]["reference"] == "task"
    assert declarations.pack(expanded) == row
    expanded["children"][0]["reference"] = "sys_user"
    with pytest.raises(CapabilityError, match="contradicts"):
        declarations.pack(expanded)


@pytest.mark.parametrize("identity", ["item-ui", "wrong-ui", None])
def test_reference_array_existing_input_keeps_declared_item_identity(identity):
    parameter = {
        "name": "records",
        "type": "array.reference",
        "reference": "task",
        "children": [
            {
                "name": "item",
                "type": "reference",
                "uiUniqueId": "item-ui",
                "children": [],
            }
        ],
    }
    existing = {
        "name": "item",
        "type": "reference",
        "reference": "task",
        "uiUniqueId": identity,
        "value": "1" * 32,
        "vendor": {"retain": False},
    }
    binding = {
        "name": "records",
        "value": "",
        "parameter": parameter,
        "children": [existing],
    }
    if identity != "item-ui":
        with pytest.raises(CapabilityError, match="identity differs"):
            data.set_value(binding, ["2" * 32])
    else:
        data.set_value(binding, ["2" * 32])
        assert binding["children"][0]["uiUniqueId"] == "item-ui"
        assert binding["children"][0]["vendor"] == {"retain": False}
        assert binding["parameter"] == parameter
