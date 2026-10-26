"""Native root picker consumers; bundle:232783-232795,232851-232873."""

import copy
from typing import Any

import httpx2
import pytest

from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_editing import BASE, FLOW, base_flow, fixtures
from tests.test_flow_fields import plan

FIELD_TYPES = f"{BASE}/api/now/processflow/field_types"


def subflow() -> dict[str, Any]:
    flow = base_flow()
    flow.update(type="subflow", actionInstances=[], subFlowInstances=[])
    flow["vendor"] = {"preserve": [False, 0, "native"]}
    return flow


def native_catalog(
    kind: str = "double", label: str = "Native real number"
) -> list[dict[str, Any]]:
    return [
        {
            "value": kind,
            "label": label,
            "vendorMetadata": {"revision": "2", "preserve": [False, 0, ""]},
        },
        {
            "value": "string",
            "label": "Native text",
            "vendorMetadata": {"revision": "1"},
        },
    ]


def catalog_route(rows: list[dict[str, Any]]):
    return http_mock.get(FIELD_TYPES).mock(
        side_effect=lambda _: httpx2.Response(
            200, json={"result": {"data": copy.deepcopy(rows)}}
        )
    )


def create(
    collection: str = "inputs", kind: str = "double", **settings: Any
) -> dict[str, Any]:
    return {
        "op": "create_field",
        "collection": collection,
        "name": "native_value",
        "label": "Native value",
        "type": kind,
        **copy.deepcopy(settings),
    }


@pytest.mark.parametrize("collection", ["inputs", "outputs"])
@pytest.mark.parametrize(
    "kind,value,metadata",
    [
        ("string_full_utf8", "Grøn 🙂", {}),
        ("html", '<p class="native">HTML &amp; text</p>', {}),
        ("url", "https://example.invalid/native", {}),
        ("long", -12, {}),
        ("double", 0.125, {}),
        ("date", "2026-10-26", {}),
        ("glide_date", "2026-10-26", {}),
        ("glide_time", "1970-01-01 01:02:03", {}),
        ("glide_duration", "1970-01-01 01:02:03", {}),
        ("glide_list", "1" * 32 + "," + "2" * 32, {"reference": "sc_req_item"}),
        ("records", "1" * 32 + "," + "2" * 32, {"reference": "sc_req_item"}),
        ("conditions", "active=true^EQ", {"reference": "sc_req_item"}),
        (
            "template_value",
            "short_description=Native^^value^active=true",
            {"reference": "sc_req_item"},
        ),
        ("name_values", '[ {"name":"header","value":"literal"} ]', {}),
    ],
)
async def test_native_root_catalog_types_preserve_labels_defaults_and_complete_evidence(
    settings, collection, kind, value, metadata
):
    flow = subflow()
    fixtures(settings, flow)
    label = "Native picker label: " + kind
    rows = native_catalog(kind, label)
    before = copy.deepcopy(rows)
    route = catalog_route(rows)
    if metadata:
        http_mock.get(f"{BASE}/api/now/table/sys_db_object").respond(
            200, json={"result": [{"name": "sc_req_item"}]}
        )
    planned = await plan(
        settings,
        flow,
        [
            create(collection, kind, default_value=value, **metadata),
            {
                **create(collection, kind, **metadata),
                "name": "unset_value",
                "label": "Unset value",
            },
        ],
    )
    field, unset = planned["payload"][collection]
    assert field["type"] == kind and field["type_label"] == label
    assert field["defaultValue"] == value
    assert type(field["defaultValue"]) is type(value)
    assert unset["type"] == kind and unset["type_label"] == label
    assert "defaultValue" not in unset and "value" not in unset
    for declaration in (field, unset):
        assert declaration["parent"] == ""
        assert declaration.get("children", []) == []
        assert declaration["attributes"]["uiUniqueId"] == declaration["uiUniqueId"]
        assert "vendorMetadata" not in declaration
        if metadata:
            assert declaration["reference"] == "sc_req_item"
            assert declaration["table"] == "sc_req_item"
    assert planned["payload"]["vendor"] == flow["vendor"]
    assert rows == before
    assert route.called
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_empty_native_picker_label_uses_the_native_type_value(settings):
    flow = subflow()
    fixtures(settings, flow)
    rows = native_catalog(label="")
    catalog_route(rows)
    planned = await plan(settings, flow, [create(default_value="0012.500")])
    field = planned["payload"]["inputs"][0]
    assert field["type"] == "double" and field["type_label"] == "double"
    assert field["defaultValue"] == "0012.500"


@pytest.mark.parametrize("collection", ["inputs", "outputs"])
async def test_saved_root_type_change_uses_native_label_and_preserves_identity(
    settings, collection
):
    flow = subflow()
    flow[collection] = [
        {
            "id": "c" * 32,
            "name": "native_value",
            "label": "Native value",
            "type": "string",
            "type_label": "String",
            "defaultValue": "old",
            "opaque": {"keep": [False, 0]},
        }
    ]
    fixtures(settings, flow)
    rows = native_catalog("html", "Native rich text")
    catalog_route(rows)
    planned = await plan(
        settings,
        flow,
        [
            {
                "op": "update_field",
                "collection": collection,
                "name": "native_value",
                "type": "html",
                "default_value": "<p>Native value</p>",
            }
        ],
    )
    assert planned["payload"][collection][0] == {
        **flow[collection][0],
        "type": "html",
        "type_label": "Native rich text",
        "defaultValue": "<p>Native value</p>",
    }
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "empty",
        "unlisted",
        "missing_value",
        "missing_label",
        "empty_value",
        "invalid_value",
        "invalid_label",
        "duplicate",
        "duplicate_unrelated",
        "oversized",
    ],
)
async def test_root_type_admission_rejects_missing_incomplete_or_ambiguous_metadata(
    settings, defect
):
    flow = subflow()
    before = copy.deepcopy(flow)
    coordinator, tools = fixtures(settings, flow)
    rows = native_catalog()
    if defect == "empty":
        rows = []
    elif defect == "unlisted":
        rows = rows[1:]
    elif defect == "missing_value":
        rows[0].pop("value")
    elif defect == "missing_label":
        rows[0].pop("label")
    elif defect == "empty_value":
        rows[0]["value"] = ""
    elif defect == "invalid_value":
        rows[0]["value"] = "invalid native type"
    elif defect == "invalid_label":
        rows[0]["label"] = None
    elif defect == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif defect == "duplicate_unrelated":
        rows.append(copy.deepcopy(rows[1]))
    elif defect == "oversized":
        rows.extend(
            {"value": f"native_{index}", "label": "Native"} for index in range(999)
        )
    if defect == "missing":
        route = http_mock.get(FIELD_TYPES).respond(
            404, json={"error": {"message": "Missing"}}
        )
    else:
        route = catalog_route(rows)
    preview = decode_response(
        await tools["flow_preview"](
            action="edit", sys_id=FLOW, edits=[create(default_value=0.125)]
        )
    )
    assert preview["status"] == "error", preview
    assert flow == before
    assert route.called
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "collection,kind",
    [
        ("inputs", "double"),
        ("outputs", "double"),
        ("flowVariables", "glide_date_time"),
        ("flowVariables", "decimal"),
        ("flowVariables", "float"),
        ("inputs", "decimal"),
        ("inputs", "float"),
        ("outputs", "decimal"),
        ("outputs", "float"),
    ],
)
@pytest.mark.parametrize("drift", ["label", "opaque_revision", "unrelated_row"])
async def test_native_type_catalog_revision_drift_blocks_apply_before_save(
    settings, collection, kind, drift
):
    flow = subflow()
    before = copy.deepcopy(flow)
    coordinator, tools = fixtures(settings, flow)
    rows = native_catalog(kind)
    catalog_route(rows)
    effect = http_mock.route(
        method="PUT", url=f"{BASE}/api/now/processflow/flow"
    ).respond(200, json={"result": {"data": copy.deepcopy(flow)}})
    edit = create(collection, kind)
    if collection != "flowVariables":
        edit["default_value"] = 0.125
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=[edit])
    )
    assert preview["status"] == "success", preview
    assert not effect.called
    if drift == "label":
        rows[0]["label"] = "Revised native label"
    elif drift == "opaque_revision":
        rows[0]["vendorMetadata"]["revision"] = "3"
    else:
        rows[1]["vendorMetadata"]["revision"] = "2"
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["error"]["code"] == "STALE_PREVIEW", applied
    assert flow == before
    assert not effect.called
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("owner", ["flowVariables", "object_child"])
async def test_native_root_catalog_does_not_expand_other_declaration_pickers(
    settings, owner
):
    flow = subflow()
    before = copy.deepcopy(flow)
    coordinator, tools = fixtures(settings, flow)
    route = catalog_route(native_catalog("html", "Native rich text"))
    if owner == "flowVariables":
        edits = [create("flowVariables", "html")]
    else:
        edits = [
            create(kind="object"),
            {
                **create(kind="html"),
                "name": "message",
                "label": "Message",
                "parent": "native_value",
            },
        ]
    preview = decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )
    assert preview["status"] == "error", preview
    assert flow == before
    assert not route.called
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)
