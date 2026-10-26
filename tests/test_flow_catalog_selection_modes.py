"""Catalog output DTOs are selected from native metadata, never synthesized schemas."""

import copy
import json
from typing import Any, override

import httpx2
import pytest

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import calls, data, declarations, tree
from servicenow_mcp.errors import PolicyError
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_editing import BASE, FLOW, fixtures, persist
from tests.test_flow_fields import plan
from tests.test_flow_native_compat import catalog_flow
from tests.test_flow_remaining_array_values import packed_array

CATALOG = "c" * 32
VARIABLE = "d" * 32
MODEL = "e" * 32
CHOICES = [{"value": "open", "label": "Open"}, {"value": "closed", "label": "Closed"}]


def native_field(name: str, kind: str, **metadata: Any) -> dict[str, Any]:
    return {
        "name": name,
        "label": name.rsplit(".", 1)[-1].title(),
        "type": kind,
        "children": [],
        "defaultValue": "",
        "defaultDisplayValue": "",
        **copy.deepcopy(metadata),
    }


def catalog_schema(
    kind: str = "object", *, table: str = "item_option_new"
) -> dict[str, Any]:
    if kind in declarations.ARRAYS:
        if kind in {"array.string", "array.choice", "array.datetime", "array.array"}:
            row = packed_array("native_result", kind)
        else:
            item = native_field(
                "native_result.native_result_child0",
                declarations.ARRAYS[kind],
                parent="native_result",
                uiUniqueId="native-item-ui",
                fieldFacetMap={"uiUniqueId": "native-item-ui"},
                cannotBeDeleted=True,
            )
            row = native_field(
                "native_result",
                kind,
                children=[item],
                parent="",
                uiUniqueId="native-root-ui",
                attributes={"uiUniqueId": "native-root-ui"},
            )
            if kind == "array.reference":
                row["reference"] = item["reference"] = "sys_user"
            if kind == "array.object":
                row["children"][0]["children"] = [
                    native_field("native_result.native_result_child0.message", "string")
                ]
            row = declarations.pack(row)
    else:
        row = native_field("native_result", kind)
        if kind == "object":
            row["children"] = [
                native_field(
                    "native_result.details",
                    "object",
                    children=[
                        native_field(
                            "native_result.details.message",
                            "string",
                            defaultValue="source child default",
                        ),
                        native_field(
                            "native_result.details.count",
                            "integer",
                            defaultValue="0007",
                        ),
                    ],
                ),
                native_field("native_result.flag", "boolean", defaultValue=False),
            ]
        if kind in {"reference", "glide_list", "records"}:
            row["reference"] = "sys_user"
        if kind == "choice":
            row["choices"] = copy.deepcopy(CHOICES)
    row.update(
        id=VARIABLE,
        table=table,
        defaultValue=False,
        defaultDisplayValue="source default display",
        value="native server value",
        display_value="native server display",
        sys_class_name=table,
        sys_mod_count="7",
        opaque_source_metadata={"nested": [False, 0, {"preserve": True}]},
    )
    row.setdefault("attributes", {}).update(
        {"catalogType": "6", "source_extension": {"preserve": [False, 0]}}
    )
    if table != "item_option_new":
        row["attributes"]["child_type"] = "object"
    return row


def selection_flow(
    rows: list[dict[str, Any]], *, saved: bool = False, preserve: bool = False
):
    flow, _ = catalog_flow()
    instance = flow["actionInstances"][0]
    instance["outputs"] = copy.deepcopy(rows) if saved else []
    instance["inputs"][1]["value"] = ",".join(
        row["id"] + ":" + row.get("table", "item_option_new") for row in rows
    )
    if preserve:
        instance["inputs"][1]["parameter"]["attributes"]["preserve_selected"] = "true"
    return flow


def metadata_routes(rows: list[dict[str, Any]], *, preserve: bool = False):
    if preserve:
        return http_mock.post(
            f"{BASE}/api/now/processflow/flow/{CATALOG}/service_get_catalog_variables"
        ).respond(
            200,
            json={
                "result": {
                    "data": {
                        "availableList": copy.deepcopy(rows),
                        "activeVariables": [
                            row["id"] + ":" + row.get("table", "item_option_new")
                            for row in rows
                        ],
                        "opaque_response_metadata": {"revision": 1},
                    },
                    "errorCode": 0,
                }
            },
        )
    return http_mock.get(
        f"{BASE}/api/now/processflow/flow/{CATALOG}/service_catalog_variables"
    ).respond(200, json={"result": {"data": copy.deepcopy(rows), "errorCode": 0}})


def select_edit(rows):
    return {
        "op": "update_inputs",
        "step": "ui-1",
        "inputs": {
            "catalog_variables": ",".join(
                row["id"] + ":" + row.get("table", "item_option_new") for row in rows
            )
        },
    }


class CatalogMetadata(ServiceNowClient):
    """Return shared metadata objects so accidental ownership leaks are observable."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = copy.deepcopy(rows)
        self.requests: list[tuple[Any, ...]] = []
        self.get_flags: list[bool] = []
        self.response: dict[str, Any] = {
            "availableList": self.rows,
            "activeVariables": [
                row["id"] + ":" + row.get("table", "item_option_new")
                for row in self.rows
            ],
            "opaque_response_metadata": {"revision": 1},
        }

    @override
    async def studio_catalog_variables(
        self,
        catalog_id: str,
        model_id: str | None = None,
        *,
        include_multirow: bool = True,
    ) -> list[dict[str, Any]]:
        self.requests.append(("GET", catalog_id, model_id))
        self.get_flags.append(include_multirow)
        return self.rows

    @override
    async def studio_catalog_selection(
        self,
        catalog_id: str,
        model_id: str | None = None,
        *,
        include_multirow: bool,
        selected_variables: dict[str, str],
    ) -> dict[str, Any]:
        self.requests.append(
            (
                "POST",
                catalog_id,
                model_id,
                {
                    "include_multirow": include_multirow,
                    "selected_variables": copy.deepcopy(selected_variables),
                },
            )
        )
        return self.response


class PreservedCatalogMetadata(CatalogMetadata):
    def __init__(self, row: dict[str, Any], flow: dict[str, Any]) -> None:
        super().__init__([])
        self.response["activeVariables"] = [row["id"] + ":" + row["table"]]
        self.saved_flow = copy.deepcopy(flow)
        self.variable: dict[str, Any] = {
            "sys_id": row["id"],
            "sys_mod_count": "3",
            "name": row["name"],
            "type": row["attributes"].get("catalogType", "6"),
            "sys_class_name": row["table"],
            "cat_item": CATALOG,
            "variable_set": "",
            "flow_catalog_model": flow.get("flowCatalogVariableModelId", ""),
            "reference": row.get("reference", ""),
            "opaque_definition_metadata": {"preserve": [False, 0]},
        }

    @override
    async def studio_request(
        self,
        resource: str,
        *,
        data: dict[str, Any] | None = None,
        create: bool = False,
        minimal: bool = False,
    ) -> dict[str, Any]:
        assert resource == "flow/" + FLOW and minimal and data is None and not create
        self.requests.append(("GET", resource))
        return self.saved_flow

    @override
    async def get_record(
        self,
        table: str,
        sys_id: str,
        fields: list[str] | None = None,
        display_values: bool = False,
    ) -> dict[str, Any]:
        assert fields is None and display_values is False
        self.requests.append(("GET", table, sys_id))
        return self.variable


@pytest.mark.parametrize(
    "kind",
    [
        "object",
        "array.object",
        "array.string",
        "array.integer",
        "array.boolean",
        "array.choice",
        "array.datetime",
        "array.reference",
        "array.array",
        "glide_date",
        "glide_date_time",
        "glide_duration",
        "email",
        "html",
        "url",
        "multi_small",
        "glide_list",
        "mask_code",
        "ip_addr",
        "integer",
        "boolean",
        "choice",
    ],
)
async def test_catalog_native_types_preserve_exact_source_dtos_and_deepcopy(kind):
    row = catalog_schema(kind)
    flow = selection_flow([row])
    client = CatalogMetadata([row])
    original = copy.deepcopy(client.rows)
    step = tree.Step(flow["actionInstances"][0], "action")
    evidence = await calls.catalog_selection(step, flow, client, changed=True)
    assert step.raw["outputs"] == original
    assert data.value_type(step.raw["outputs"][0]).kind == kind
    assert evidence["schema"] == evidence["metadata"] == original
    assert client.rows == original
    step.raw["outputs"][0]["opaque_source_metadata"]["nested"][2]["preserve"] = False
    evidence["schema"][0]["attributes"]["source_extension"]["preserve"].append(
        "changed"
    )
    evidence["metadata"][0]["opaque_source_metadata"]["nested"].append("changed")
    assert client.rows == original
    assert client.requests == [("GET", CATALOG, None)]


@pytest.mark.parametrize(
    "kind", ["array.string", "array.object", "array.reference", "array.choice"]
)
async def test_native_catalog_arrays_do_not_require_ui_only_item_metadata(kind):
    row = catalog_schema(kind)
    row["attributes"] = {
        key: value
        for key, value in row["attributes"].items()
        if not key.startswith("child_")
        or kind == "array.object"
        and key == "child_type"
    }
    if kind == "array.object":
        row["children"] = [native_field("message", "string")]
    before = copy.deepcopy(row)
    flow = selection_flow([row])
    client = CatalogMetadata([row])
    evidence = await calls.catalog_selection(
        tree.Step(flow["actionInstances"][0], "action"), flow, client, changed=True
    )
    assert flow["actionInstances"][0]["outputs"] == [before]
    assert evidence["schema"] == evidence["metadata"] == [before]
    assert client.rows == [before]


async def test_catalog_scripted_defaults_are_copied_as_metadata_without_execution():
    row = catalog_schema()
    row["defaultValue"] = "javascript:gs.getUserID()"
    row["children"][0]["children"][0]["defaultValue"] = "{{fd_scripted.native_output}}"
    row["attributes"]["native_script_metadata"] = "return current.requested_for;"
    flow = selection_flow([row])
    client = CatalogMetadata([row])
    await calls.catalog_selection(
        tree.Step(flow["actionInstances"][0], "action"), flow, client, changed=True
    )
    assert flow["actionInstances"][0]["outputs"] == [row]
    assert client.requests == [("GET", CATALOG, None)]


@pytest.mark.parametrize("preserve", [False, True])
async def test_catalog_payload_generates_nested_scalar_pills_and_full_native_outputs(
    settings, preserve
):
    row = catalog_schema()
    flow = selection_flow([row], preserve=preserve)
    before = copy.deepcopy(flow)
    fixtures(settings, flow)
    route = metadata_routes([row], preserve=preserve)
    result = await plan(
        settings,
        flow,
        [
            select_edit([row]),
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {
                    "log_message": "Native {{ui-1.native_result.details.message}} / {{ui-1.native_result.flag}}",
                },
            },
        ],
    )
    assert flow == before
    assert result["payload"]["actionInstances"][0]["outputs"] == [row]
    assert {
        entry["name"]: entry["type"] for entry in result["payload"]["label_cache"]
    } == {
        "ui-1.native_result.details.message": "string",
        "ui-1.native_result.flag": "boolean",
    }
    assert route.call_count >= 2  # Selection and the independent saved-output pass.
    assert all(
        call.request.method in ({"GET", "POST"} if preserve else {"GET"})
        for call in http_mock.calls
    )
    if preserve:
        request = json.loads(route.calls[0].request.content)
        assert request["includeMultiRowVariableSets"] is True
        assert json.loads(request["selectedVariables"]) == {"item_option_new": VARIABLE}


@pytest.mark.parametrize(
    "pill",
    [
        "{{ui-1.native_result.details.absent}}",
        "{{ui-2.native_result.details.message}}",
        "{{ui-1.native_result.details.count}}",
    ],
)
async def test_catalog_nested_pills_reject_wrong_fields_producers_and_types(
    settings, pill
):
    row = catalog_schema()
    flow = selection_flow([row])
    fixtures(settings, flow)
    metadata_routes([row])
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [
                select_edit([row]),
                {
                    "op": "update_inputs",
                    "step": "ui-3",
                    "inputs": {"log_message": pill},
                },
            ],
        )


async def test_catalog_collections_cannot_be_dot_walked_without_an_item(settings):
    row = catalog_schema("array.object", table="item_option_new_set")
    flow = selection_flow([row])
    fixtures(settings, flow)
    metadata_routes([row])
    with pytest.raises(CapabilityError, match="loop item"):
        await plan(
            settings,
            flow,
            [
                select_edit([row]),
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"log_message": "Native {{ui-1.native_result.message}}"},
                },
            ],
        )


async def test_nested_catalog_array_pill_uses_existing_packed_declaration_schema(
    settings,
):
    row = catalog_schema()
    item = native_field(
        "native_result.items.entry",
        "reference",
        reference="sys_user",
        parent="native_result.items",
        uiUniqueId="nested-item-ui",
        fieldFacetMap={"uiUniqueId": "nested-item-ui"},
        cannotBeDeleted=True,
    )
    nested = declarations.pack(
        native_field(
            "native_result.items",
            "array.reference",
            reference="sys_user",
            parent="native_result",
            children=[item],
            uiUniqueId="nested-array-ui",
            fieldFacetMap={"uiUniqueId": "nested-array-ui"},
        )
    )
    row["children"].append(nested)
    flow = selection_flow([row])
    target = catalog_schema("array.reference")
    target.pop("table")
    target.pop("id")
    flow["actionInstances"][1]["inputs"].append(
        {
            "name": "native_result",
            "parameter": target,
            "value": "",
            "children": [],
        }
    )
    fixtures(settings, flow)
    metadata_routes([row])
    result = await plan(
        settings,
        flow,
        [
            select_edit([row]),
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"native_result": "{{ui-1.native_result.items}}"},
            },
        ],
    )
    assert result["payload"]["actionInstances"][0]["outputs"] == [row]
    assert result["payload"]["label_cache"][0]["type"] == "array.reference"


@pytest.mark.parametrize(
    "kind",
    [
        "object",
        "array.object",
        "array.reference",
        "array.choice",
        "array.datetime",
        "array.array",
    ],
)
async def test_catalog_whole_complex_pills_use_selected_source_schema_not_action_outputs(
    settings, kind
):
    row = catalog_schema(
        kind,
        table="item_option_new_set" if kind == "array.object" else "item_option_new",
    )
    flow = selection_flow([row])
    target = copy.deepcopy(row)
    for key in ("id", "table", "sys_class_name", "value", "display_value"):
        target.pop(key, None)
    target["name"] = "native_result"
    flow["actionInstances"][1]["inputs"].append(
        {
            "name": "native_result",
            "parameter": copy.deepcopy(target),
            "value": "",
            "children": [],
        }
    )
    fixtures(settings, flow)
    metadata_routes([row])
    result = await plan(
        settings,
        flow,
        [
            select_edit([row]),
            {
                "op": "update_inputs",
                "step": "ui-2",
                "inputs": {"native_result": "{{ui-1.native_result}}"},
            },
        ],
    )
    assert result["payload"]["actionInstances"][0]["actionType"]["outputs"] == []
    assert result["payload"]["actionInstances"][0]["outputs"] == [row]
    consumer = result["payload"]["actionInstances"][1]["inputs"][-1]
    assert consumer["value"] == "{{ui-1.native_result}}" and consumer["children"] == []
    assert result["payload"]["label_cache"][0]["type"] == kind


async def test_saved_catalog_output_metadata_is_unchanged_while_full_independent_evidence_is_owned():
    row = catalog_schema()
    flow = selection_flow([row], saved=True)
    saved = flow["actionInstances"][0]["outputs"][0]
    saved["defaultValue"] = "earlier native default"
    saved["defaultDisplayValue"] = "earlier native display"
    saved["value"] = False
    saved["display_value"] = "earlier value display"
    saved["opaque_saved_metadata"] = {"preserve": True}
    before = copy.deepcopy(flow)
    client = CatalogMetadata([row])
    evidence = await calls.catalog_selection(
        tree.Step(flow["actionInstances"][0], "action"), flow, client, changed=False
    )
    assert flow == before
    assert evidence["schema"] == [row]
    assert evidence["metadata"] == [row]
    assert evidence["schema"][0] is not evidence["metadata"][0]


@pytest.mark.parametrize("table", ["item_option_new", "sys_flow_cat_variable"])
async def test_preserved_catalog_output_requires_saved_server_schema_active_identity_and_definition(
    table,
):
    row = catalog_schema("reference", table=table)
    row["attributes"]["catalogType"] = "8"
    flow = selection_flow([row], saved=True, preserve=True)
    if table == "sys_flow_cat_variable":
        flow["triggerInstances"] = [{"type": "service_catalog"}]
        flow["flowCatalogVariableModelId"] = MODEL
    client = PreservedCatalogMetadata(row, flow)
    flow["actionInstances"][0]["inputs"][0]["value"] = "9" * 32
    original = copy.deepcopy(flow)
    evidence = await calls.catalog_selection(
        tree.Step(flow["actionInstances"][0], "action"), flow, client, changed=True
    )
    assert flow == original
    assert evidence["schema"] == [row]
    assert evidence["metadata"]["availableList"] == []
    assert (
        evidence["preserved_metadata"][0]["producer"]
        == client.saved_flow["actionInstances"][0]
    )
    assert evidence["preserved_metadata"][0]["variable"] == client.variable
    evidence["preserved_metadata"][0]["variable"]["opaque_definition_metadata"][
        "preserve"
    ].append("changed")
    assert client.variable["opaque_definition_metadata"] == {"preserve": [False, 0]}
    assert client.requests[-2:] == [("GET", "flow/" + FLOW), ("GET", table, VARIABLE)]


@pytest.mark.parametrize(
    "defect",
    [
        "forged_output",
        "forged_false_zero",
        "new_output",
        "different_producer",
        "unselected_saved_output",
        "inactive",
        "identity",
        "revision",
        "type",
        "owner",
        "reference",
        "class",
        "unknown_native_type",
    ],
)
async def test_preserved_catalog_output_never_accepts_forged_or_unproven_metadata(
    defect,
):
    row = catalog_schema("reference")
    row["attributes"]["catalogType"] = "8"
    flow = selection_flow([row], saved=True, preserve=True)
    client = PreservedCatalogMetadata(row, flow)
    instance = flow["actionInstances"][0]
    if defect == "forged_output":
        instance["outputs"][0]["opaque_source_metadata"]["nested"].append("forged")
    elif defect == "forged_false_zero":
        instance["outputs"][0]["defaultValue"] = 0
    elif defect == "new_output":
        instance["outputs"] = []
    elif defect == "different_producer":
        client.saved_flow["actionInstances"][0]["id"] = "7" * 32
    elif defect == "unselected_saved_output":
        client.saved_flow["actionInstances"][0]["inputs"][1]["value"] = ""
    elif defect == "inactive":
        client.response["activeVariables"] = []
    elif defect == "identity":
        client.variable["sys_id"] = "7" * 32
    elif defect == "revision":
        client.variable.pop("sys_mod_count")
    elif defect == "type":
        client.variable["type"] = "6"
    elif defect == "owner":
        client.variable["cat_item"] = "7" * 32
    elif defect == "reference":
        client.variable["reference"] = "incident"
    elif defect == "class":
        client.variable["sys_class_name"] = "incident"
    elif defect == "unknown_native_type":
        instance["outputs"][0]["attributes"].pop("catalogType")
        client.saved_flow["actionInstances"][0]["outputs"] = copy.deepcopy(
            instance["outputs"]
        )
    before = copy.deepcopy(flow)
    with pytest.raises(CapabilityError):
        await calls.catalog_selection(
            tree.Step(instance, "action"), flow, client, changed=True
        )
    assert flow == before


async def test_native_multirow_selection_keeps_source_table_and_legacy_bare_ids():
    row = catalog_schema("array.object", table="item_option_new_set")
    flow = selection_flow([row], preserve=True)
    client = CatalogMetadata([row])
    evidence = await calls.catalog_selection(
        tree.Step(flow["actionInstances"][0], "action"), flow, client, changed=True
    )
    assert evidence["metadata"] == client.response
    assert client.requests == [
        (
            "POST",
            CATALOG,
            None,
            {
                "include_multirow": True,
                "selected_variables": {"item_option_new_set": VARIABLE},
            },
        )
    ]
    assert flow["actionInstances"][0]["outputs"] == [row]
    legacy = catalog_schema("string")
    legacy.pop("table")
    legacy_flow = selection_flow([legacy])
    legacy_flow["actionInstances"][0]["inputs"][1]["value"] = VARIABLE
    await calls.catalog_selection(
        tree.Step(legacy_flow["actionInstances"][0], "action"),
        legacy_flow,
        CatalogMetadata([legacy]),
        changed=True,
    )
    assert (
        legacy_flow["actionInstances"][0]["inputs"][1]["value"]
        == VARIABLE + ":item_option_new"
    )


@pytest.mark.parametrize("include_multirow", [False, True])
async def test_catalog_native_multirow_flag_controls_read_and_generated_selection(
    include_multirow,
):
    row = catalog_schema("string")
    flow = selection_flow([row])
    instance = flow["actionInstances"][0]
    instance["inputs"][1]["value"] = VARIABLE
    instance["inputs"][1]["parameter"]["attributes"][
        "include_multirow_variablesets"
    ] = str(include_multirow).lower()
    client = CatalogMetadata([row])
    await calls.catalog_selection(
        tree.Step(instance, "action"), flow, client, changed=True
    )
    assert client.get_flags == [include_multirow]
    assert instance["inputs"][1]["value"] == VARIABLE + (
        ":item_option_new" if include_multirow else ""
    )


@pytest.mark.parametrize("wrong_table", [False, True])
async def test_native_template_picker_keeps_wire_value_and_validates_source_table(
    wrong_table,
):
    row = catalog_schema("string")
    flow = selection_flow([row])
    flow["flowCatalogVariableModelId"] = ""
    picker = json.dumps(
        {
            "display": "Template",
            "value": CATALOG,
            "table": "incident" if wrong_table else "sc_cat_item",
        }
    )
    flow["actionInstances"][0]["inputs"][0]["value"] = picker
    client = CatalogMetadata([row])
    step = tree.Step(flow["actionInstances"][0], "action")
    if wrong_table:
        with pytest.raises(CapabilityError):
            await calls.catalog_selection(step, flow, client, changed=True)
        assert client.requests == []
    else:
        evidence = await calls.catalog_selection(step, flow, client, changed=True)
        assert evidence["catalog"] == CATALOG
        assert step.raw["inputs"][0]["value"] == picker
        assert client.requests == [("GET", CATALOG, None)]


async def test_catalog_source_has_no_artificial_one_hundred_variable_cap():
    row = catalog_schema("string")
    others = [
        {**catalog_schema("string"), "id": f"{index:032x}", "name": f"field_{index}"}
        for index in range(101)
    ]
    client = CatalogMetadata([row, *others])
    flow = selection_flow([row])
    evidence = await calls.catalog_selection(
        tree.Step(flow["actionInstances"][0], "action"), flow, client, changed=True
    )
    assert len(evidence["metadata"]) == 102
    assert flow["actionInstances"][0]["outputs"] == [row]


@pytest.mark.parametrize(
    "defect",
    [
        "duplicate_identity",
        "duplicate_table_identity",
        "duplicate_selected_identity",
        "wrong_selected_table",
        "wrong_source",
        "unknown_type",
        "scalar_children",
        "duplicate_child",
        "wrong_child_parent",
        "wrong_child_producer",
        "unknown_array_type",
        "wrong_array_item",
        "unknown_reference",
        "denied_reference",
        "denied_source",
        "native_label",
        "missing_catalog_type",
        "inactive_selected",
        "missing_output",
        "ambiguous_saved_output",
        "saved_nested_drift",
        "saved_opaque_drift",
        "wrong_producer_kind",
        "unreadable_producer",
        "dynamic_catalog",
    ],
)
async def test_catalog_selection_rejects_ambiguous_unknown_or_cross_source_metadata(
    defect,
):
    row = catalog_schema()
    flow = selection_flow([row], saved=True, preserve=defect == "inactive_selected")
    instance = flow["actionInstances"][0]
    source: list[dict[str, Any]] = [copy.deepcopy(row)]
    kind = "action"
    if defect == "duplicate_identity":
        source.append(copy.deepcopy(row))
    elif defect == "duplicate_table_identity":
        source.append({**copy.deepcopy(row), "table": "item_option_new_set"})
    elif defect == "duplicate_selected_identity":
        instance["inputs"][1]["value"] += "," + VARIABLE + ":item_option_new_set"
    elif defect == "wrong_selected_table":
        instance["inputs"][1]["value"] = VARIABLE + ":item_option_new_set"
    elif defect == "wrong_source":
        instance["inputs"][1]["parameter"]["attributes"]["source_table"] = "incident"
    elif defect == "unknown_type":
        source[0]["type"] = "future_unregistered_type"
    elif defect == "scalar_children":
        source[0]["type"] = "string"
    elif defect == "duplicate_child":
        source[0]["children"].append(copy.deepcopy(source[0]["children"][0]))
    elif defect == "wrong_child_parent":
        source[0]["children"][0]["parent"] = "other_output"
    elif defect == "wrong_child_producer":
        source[0]["children"][0]["name"] = "other_output.details"
    elif defect in {"unknown_array_type", "wrong_array_item"}:
        source[0] = catalog_schema("array.string")
        if defect == "unknown_array_type":
            source[0]["type"] = "array.unregistered"
        else:
            source[0]["attributes"]["child_type"] = "boolean"
    elif defect in {"unknown_reference", "denied_reference"}:
        source[0]["children"][0]["children"][0].update(
            type="reference",
            reference="" if defect == "unknown_reference" else "sys_credentials",
        )
    elif defect == "denied_source":
        source[0]["table"] = "sys_credentials"
        instance["inputs"][1]["value"] = VARIABLE + ":sys_credentials"
    elif defect == "native_label":
        source[0]["attributes"]["catalogType"] = "11"
    elif defect == "missing_catalog_type":
        source[0]["attributes"].pop("catalogType")
    elif defect == "missing_output":
        instance["outputs"] = []
    elif defect == "ambiguous_saved_output":
        instance["outputs"].append(copy.deepcopy(row))
    elif defect == "saved_nested_drift":
        instance["outputs"][0]["children"][0]["children"][0]["type"] = "integer"
    elif defect == "saved_opaque_drift":
        instance["outputs"][0]["attributes"]["source_extension"] = {"changed": True}
    elif defect == "wrong_producer_kind":
        kind = "subflow"
    elif defect == "unreadable_producer":
        instance["actionType"]["userCanRead"] = False
    elif defect == "dynamic_catalog":
        instance["inputs"][0]["value"] = "{{ui-2.catalog_item}}"
    client = CatalogMetadata(source)
    if defect == "inactive_selected":
        client.response["activeVariables"] = []
    before = copy.deepcopy(flow)
    with pytest.raises((CapabilityError, PolicyError, ValueError)):
        await calls.catalog_selection(
            tree.Step(instance, kind), flow, client, changed=False
        )
    assert flow == before


@pytest.mark.parametrize("trigger,model", [(False, True), (True, False), (True, True)])
async def test_flow_specific_catalog_source_is_bound_to_native_trigger_and_model(
    trigger, model
):
    row = catalog_schema("string", table="sys_flow_cat_variable")
    flow = selection_flow([row])
    if trigger:
        flow["triggerInstances"] = [{"type": "service_catalog"}]
    if model:
        flow["flowCatalogVariableModelId"] = MODEL
    client = CatalogMetadata([row])
    step = tree.Step(flow["actionInstances"][0], "action")
    if trigger and model:
        evidence = await calls.catalog_selection(step, flow, client, changed=True)
        assert evidence["model"] == MODEL and step.raw["outputs"] == [row]
    else:
        with pytest.raises(CapabilityError):
            await calls.catalog_selection(step, flow, client, changed=True)


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "nested_schema",
        "native_default",
        "opaque_metadata",
        "unselected_metadata",
        "revision",
        "readback",
    ],
)
async def test_catalog_full_metadata_revision_and_readback_are_bound_to_preview(
    settings, defect
):
    row = catalog_schema()
    flow = selection_flow([row])
    coordinator, tools = fixtures(settings, flow)
    other: dict[str, Any] = {
        **catalog_schema("string"),
        "id": "f" * 32,
        "name": "other_result",
    }
    metadata_routes([row, other])

    def save(request):
        submitted = json.loads(request.content)
        actual = copy.deepcopy(submitted)
        if defect == "readback":
            actual["actionInstances"][0]["outputs"][0]["children"][0]["children"][0][
                "defaultValue"
            ] = "changed after save"
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": copy.deepcopy(submitted)}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit", sys_id=FLOW, edits=[select_edit([row])]
        )
    )
    assert preview["status"] == "success", preview
    if defect not in (None, "readback"):
        changed = copy.deepcopy(row)
        if defect == "nested_schema":
            changed["children"][0]["children"][0]["type"] = "integer"
        elif defect == "native_default":
            changed["defaultValue"] = "changed native default"
        elif defect == "opaque_metadata":
            changed["opaque_source_metadata"]["nested"].append("changed")
        elif defect == "revision":
            changed["sys_mod_count"] = "8"
        if defect == "unselected_metadata":
            other["opaque_source_metadata"]["nested"].append("changed")
        metadata_routes([changed, other])
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    if defect == "readback":
        assert (
            applied["status"] == "error"
            and applied["error"]["code"] == "UNKNOWN_OUTCOME"
        )
        assert write.call_count == 1
    elif defect:
        assert applied["status"] == "error" and not write.called
    else:
        assert applied["status"] == "success", applied
        assert write.call_count == 1
    assert len(coordinator.store) == 0


@pytest.mark.parametrize(
    "defect", [None, "revision", "type", "owner", "opaque_metadata"]
)
async def test_preserved_catalog_definition_metadata_is_independently_drift_bound(
    settings, defect
):
    row = catalog_schema("reference")
    row["attributes"]["catalogType"] = "8"
    flow = selection_flow([row], saved=True, preserve=True)
    coordinator, tools = fixtures(settings, flow)
    http_mock.post(
        f"{BASE}/api/now/processflow/flow/{CATALOG}/service_get_catalog_variables"
    ).respond(
        200,
        json={
            "result": {
                "data": {
                    "availableList": [],
                    "activeVariables": [VARIABLE + ":item_option_new"],
                },
                "errorCode": 0,
            }
        },
    )
    record = PreservedCatalogMetadata(row, flow).variable
    record_route = http_mock.get(f"{BASE}/api/now/table/item_option_new/{VARIABLE}")
    record_route.respond(200, json={"result": copy.deepcopy(record)})

    def save(request):
        submitted = json.loads(request.content)
        persist(copy.deepcopy(submitted))
        return httpx2.Response(200, json={"result": {"data": copy.deepcopy(submitted)}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"log_message": "Preserved {{ui-1.native_result}}"},
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    if defect:
        changed = copy.deepcopy(record)
        if defect == "revision":
            changed["sys_mod_count"] = "4"
        elif defect == "type":
            changed["type"] = "6"
        elif defect == "owner":
            changed["cat_item"] = "7" * 32
        else:
            changed["opaque_definition_metadata"]["preserve"].append("changed")
        record_route.respond(200, json={"result": changed})
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    if defect:
        assert applied["status"] == "error" and not write.called
    else:
        assert applied["status"] == "success", applied
        assert write.call_count == 1
    assert len(coordinator.store) == 0
