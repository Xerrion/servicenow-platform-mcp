"""Source-derived transform DTOs; synthetic metadata, not captured live saves.

Tests exercise static metadata reads and authoring only. No function/script,
action, or flow execution endpoint is used.
"""

import copy
import json
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import data, editing, input_transforms, tree
from servicenow_mcp.capabilities.mutations.models import fingerprint
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_editing import BASE, FLOW, LOG, base_flow, fixtures, persist
from tests.test_flow_fields import plan

TRANSFORM = "b" * 32
SECOND = "c" * 32
COMPOSITION = "d" * 32
SECOND_COMPOSITION = "e" * 32


def definition(
    label: str = "Trim",
    *,
    identity: str = TRANSFORM,
    parameters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": identity,
        "label": label,
        "category": "Fixture native category",
        "description": "Synthetic source-shaped transform metadata",
        "transformCompositions": []
        if parameters is None
        else [{"id": COMPOSITION, "parameters": copy.deepcopy(parameters)}],
        "server_metadata": {"revision": "independently read"},
    }


def option(
    identity: str = TRANSFORM,
    *,
    parameters: dict[str, Any] | None = None,
    source: str = "{{ui-1.status}}",
) -> dict[str, Any]:
    transform: dict[str, Any] = {"transform_sys_id": identity}
    if parameters is not None:
        transform["compositions"] = [
            {"composition_sys_id": COMPOSITION, "parameters": parameters}
        ]
    return {"log_message": {"source": source, "transforms": [transform]}}


def native_flow(source_type="string", target_type="string"):
    flow = base_flow()
    flow["subFlowInstances"] = []
    flow["actionInstances"][0]["actionType"]["outputs"] = [
        {"name": "status", "label": "Status", "type": source_type, "children": []},
        {
            "name": "owner",
            "label": "Owner",
            "type": "reference",
            "reference": "sys_user_group",
            "children": [],
        },
    ]
    flow["actionInstances"][1]["inputs"][0]["parameter"].update(
        name="log_message", type=target_type
    )
    flow["actionInstances"][1]["data"] = {"log_message": "two"}
    flow["actionInstances"][1]["inputs"][0]["opaque"] = {"preserve": True}
    flow["pharmacy"] = {
        "opaque": {"preserve": True},
        "pharmacyCompound": {
            "unrelated": {
                "id": "9" * 32,
                "uiId": "unrelated",
                "order": 0,
                "prescription": "{{ui-1.status}}",
                "attachedTo": FLOW,
                "scopeId": "global",
                "opaque": {"preserve": True},
            }
        },
    }
    return flow


def mock_log_definition(flow):
    declared = {
        "id": LOG,
        "security": {"can_read": True},
        "entitled": True,
        "state": "published",
        "is_snapshot": True,
        "inputs": copy.deepcopy(
            [row["parameter"] for row in flow["actionInstances"][1]["inputs"]]
        ),
        "outputs": copy.deepcopy(flow["actionInstances"][0]["actionType"]["outputs"]),
    }
    return http_mock.get(
        f"{BASE}/api/now/processflow/action/action_types/{LOG}"
    ).respond(
        200,
        json={"result": declared},
    )


@asynccontextmanager
async def context(settings, definitions, *, flow=None):
    flow = native_flow() if flow is None else flow
    route = http_mock.get(f"{BASE}/api/now/transformdefinitions").respond(
        200, json={"result": {"data": definitions}}
    )
    mock_log_definition(flow)
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        roots = tree.build_tree(flow)
        resolver = editing.PillResolver(client, None, flow, roots)
        yield flow, roots[1], resolver, route


def compounds(flow):
    return flow["pharmacy"]["pharmacyCompound"]


def root_pill(step: tree.Step) -> str:
    pill = data.whole_pill(step.raw["inputs"][0]["value"])
    assert pill is not None
    return pill


def transform_rows(flow):
    return sorted(
        (row for row in compounds(flow).values() if row.get("transformDefinitionId")),
        key=lambda row: row["order"],
    )


@pytest.mark.parametrize(
    "bad",
    [
        {
            "log_message": {
                "source": "literal",
                "transforms": [{"transform_sys_id": TRANSFORM}],
            }
        },
        {
            "log_message": {
                "source": "{{fd_transform:root}}",
                "transforms": [{"transform_sys_id": TRANSFORM}],
            }
        },
        {"log_message": {"source": "{{ui-1.status}}", "transforms": []}},
        {
            "log_message": {
                "source": "{{ui-1.status}}",
                "transforms": [{"transform_sys_id": TRANSFORM, "outputType": "string"}],
            }
        },
        {
            "log_message": {
                "source": "{{ui-1.status}}",
                "transforms": [
                    {
                        "transform_sys_id": TRANSFORM,
                        "compositions": [
                            {
                                "composition_sys_id": COMPOSITION,
                                "parameters": {},
                                "id": "user DTO",
                            }
                        ],
                    }
                ],
            }
        },
        {
            "log_message": {
                "source": "{{ui-1.status}}",
                "transforms": [
                    {
                        "transform_sys_id": TRANSFORM,
                        "compositions": [
                            {"composition_sys_id": COMPOSITION, "parameters": {}},
                            {"composition_sys_id": COMPOSITION, "parameters": {}},
                        ],
                    }
                ],
            }
        },
    ],
)
def test_public_shape_has_no_studio_dto_escape_hatch(bad):
    with pytest.raises((CapabilityError, ValueError)):
        input_transforms.parse(bad)


async def test_authors_native_root_chain_compositions_and_values(settings):
    parameters = [
        {
            "id": "1" * 32,
            "name": "start",
            "label": "Start Index",
            "type": "integer",
            "mandatory": True,
            "value": "0",
        },
        {
            "id": "2" * 32,
            "name": "end",
            "label": "End Index",
            "type": "integer",
            "mandatory": True,
            "value": "12",
        },
        {
            "id": "3" * 32,
            "name": "policy",
            "label": "Policy",
            "type": "string",
            "readonly": True,
            "value": "native",
        },
        {
            "id": "4" * 32,
            "name": "optional",
            "label": "Optional",
            "type": "string",
            "defaultValue": "not the UI value",
        },
    ]
    first = definition("Substring", parameters=parameters)
    first["transformCompositions"].append(
        {
            "id": SECOND_COMPOSITION,
            "parameters": [
                {
                    "name": "extra",
                    "type": "string",
                    "value": "second native configuration",
                }
            ],
        }
    )
    definitions = [first, definition("To Upper Case", identity=SECOND)]
    values = option(parameters={"start": 2, "end": 8})
    values["log_message"]["transforms"].append({"transform_sys_id": SECOND})
    async with context(settings, definitions) as (flow, step, resolver, route):
        before = copy.deepcopy(flow["pharmacy"])
        evidence = await input_transforms.apply(
            flow, step, values, resolver, seed="stable-edit"
        )
        pill = root_pill(step)
        root_id = pill.removeprefix("fd_transform:")
        assert compounds(flow)[root_id] == {
            "uiId": root_id,
            "order": 0,
            "prescription": "{{ui-1.status}}",
        }
        chain = transform_rows(flow)
        assert [row["order"] for row in chain] == [1, 2]
        assert [row["transformDefinitionId"] for row in chain] == [TRANSFORM, SECOND]
        assert all(row["sourceUiId"] == root_id for row in chain)
        configs = chain[0]["compositions"]
        assert [row["id"] for row in configs] == [COMPOSITION, SECOND_COMPOSITION]
        assert [row["order"] for row in configs] == [0, 1]
        assert [row.get("value") for row in configs[0]["parameters"]] == [
            "2",
            "8",
            "native",
            None,
        ]
        assert "value" not in configs[0]["parameters"][3]
        assert all(
            "uiId" not in row and row["order"] == order
            for order, row in enumerate(configs[0]["parameters"])
        )
        assert all("uiId" not in config for config in configs)
        assert all(
            "parameter" not in row and "displayValue" not in row
            for row in configs[0]["parameters"]
        )
        assert step.raw["data"]["log_message"] == step.raw["inputs"][0]["value"]
        assert step.raw["inputs"][0]["displayValue"] == step.raw["inputs"][0]["value"]
        assert step.raw["inputs"][0]["opaque"] == {"preserve": True}
        assert compounds(flow)["unrelated"] == before["pharmacyCompound"]["unrelated"]
        assert flow["pharmacy"]["opaque"] == before["opaque"]
        assert evidence[0]["definitions"] == definitions
        assert evidence[0]["transform_sys_ids"] == [TRANSFORM, SECOND]
        assert evidence[0]["flow_sys_id"] == FLOW and evidence[0]["scope"] == "global"
        assert evidence[0]["consumer_ui_id"] == "ui-2" and evidence[0][
            "input_names"
        ] == ["log_message"]
        assert (await resolver.resolve(pill, step))["type"] == "string"
        assert route.call_count == 1


async def test_deterministic_identifiers_do_not_depend_on_read_sequence(settings):
    authored = []
    for _ in range(2):
        async with context(settings, [definition()]) as (flow, step, resolver, _route):
            await input_transforms.apply(
                flow, step, option(), resolver, seed="same-plan"
            )
            authored.append(copy.deepcopy(flow))
    assert authored[0] == authored[1]


@pytest.mark.parametrize(
    "label,source_type,target_type,expected",
    [
        ("Contains", "string", "boolean", "boolean"),
        ("String to Date", "string", "glide_date_time", "glide_date_time"),
        ("Convert String to Number", "string", "number", "number"),
        ("Is Null", "reference", "boolean", "boolean"),
    ],
)
async def test_documented_types_replace_source_type_without_execution(
    settings, label, source_type, target_type, expected
):
    async with context(
        settings, [definition(label)], flow=native_flow(source_type, target_type)
    ) as (flow, step, resolver, route):
        await input_transforms.apply(flow, step, option(), resolver, seed=label)
        result = await resolver.resolve(root_pill(step), step)
        assert result["type"] == expected
        assert input_transforms.source(flow, root_pill(step)) == "ui-1.status"
        assert route.call_count == 1


async def test_chain_uses_previous_transform_result_type(settings):
    definitions = [
        definition("String to Date"),
        definition("Date to String", identity=SECOND),
    ]
    values = option()
    values["log_message"]["transforms"].append({"transform_sys_id": SECOND})
    async with context(settings, definitions) as (flow, step, resolver, _route):
        await input_transforms.apply(flow, step, values, resolver, seed="date-chain")
        assert (await resolver.resolve(root_pill(step), step))["type"] == "string"


@pytest.mark.parametrize(
    "defect", ["source_type", "consumer_type", "later_source", "chain_type"]
)
async def test_producer_consumer_and_chain_constraints_fail_atomically(
    settings, defect
):
    definitions = [definition("Trim"), definition("Day", identity=SECOND)]
    flow = native_flow(
        "integer" if defect == "source_type" else "string",
        "boolean" if defect == "consumer_type" else "string",
    )
    if defect == "later_source":
        flow["actionInstances"][2]["actionType"]["outputs"] = [
            {"name": "status", "type": "string"}
        ]
    values = option(
        source="{{ui-3.status}}" if defect == "later_source" else "{{ui-1.status}}"
    )
    if defect == "chain_type":
        values["log_message"]["transforms"].append({"transform_sys_id": SECOND})
    before = copy.deepcopy(flow)
    async with context(settings, definitions, flow=flow) as (
        flow,
        step,
        resolver,
        _route,
    ):
        with pytest.raises(CapabilityError):
            await input_transforms.apply(flow, step, values, resolver, seed=defect)
        assert flow == before


@pytest.mark.parametrize(
    "parameters,values",
    [
        ([{"name": "required", "type": "string", "mandatory": True}], {}),
        (
            [
                {
                    "name": "required",
                    "type": "string",
                    "mandatory": True,
                    "value": "native",
                }
            ],
            {"required": ""},
        ),
        (
            [{"name": "fixed", "type": "string", "readonly": True, "value": "native"}],
            {"fixed": "changed"},
        ),
        ([{"name": "integer", "type": "integer"}], {"integer": True}),
        (
            [
                {
                    "name": "choice",
                    "type": "choice",
                    "choices": [{"value": "native", "label": "Native"}],
                }
            ],
            {"choice": "invented"},
        ),
        ([{"name": "text", "type": "string"}], {"unknown": "value"}),
    ],
)
async def test_parameters_use_actual_schema_and_shared_value_validation(
    settings, parameters, values
):
    async with context(settings, [definition("Substring", parameters=parameters)]) as (
        flow,
        step,
        resolver,
        _route,
    ):
        before = copy.deepcopy(flow)
        with pytest.raises(CapabilityError):
            await input_transforms.apply(
                flow, step, option(parameters=values), resolver, seed="bad-values"
            )
        assert flow == before


async def test_parameter_pill_table_is_not_inferred_from_consumer_type(settings):
    parameters = [
        {
            "name": "owner",
            "label": "Owner",
            "type": "reference",
            "reference": "sys_user",
        }
    ]
    http_mock.get(f"{BASE}/api/now/table/sys_db_object").respond(
        200, json={"result": [{"super_class.name": ""}]}
    )
    async with context(settings, [definition("Substring", parameters=parameters)]) as (
        flow,
        step,
        resolver,
        _route,
    ):
        before = copy.deepcopy(flow)
        with pytest.raises(CapabilityError, match="reference table"):
            await input_transforms.apply(
                flow,
                step,
                option(parameters={"owner": "{{ui-1.owner}}"}),
                resolver,
                seed="wrong-table",
            )
        assert flow == before


async def test_saved_nonidentity_chain_is_validated_and_clear_restores_source(settings):
    async with context(settings, [definition()]) as (flow, step, resolver, _route):
        await input_transforms.apply(flow, step, option(), resolver, seed="clear")
        old_root = root_pill(step).removeprefix("fd_transform:")
        old_ids = {
            key
            for key, row in compounds(flow).items()
            if key == old_root or row.get("sourceUiId") == old_root
        }
        for index, key in enumerate(old_ids):
            compounds(flow)[key].update(
                id=str(index + 1) * 32,
                attachedTo=FLOW,
                scopeId="global",
                opaque={"keep": True},
            )
        await input_transforms.apply(
            flow, step, {"log_message": None}, resolver, seed="clear-other-seed"
        )
        assert step.raw["inputs"][0]["value"] == "{{ui-1.status}}"
        assert step.raw["inputs"][0]["displayValue"] == "{{ui-1.status}}"
        assert step.raw["data"]["log_message"] == "{{ui-1.status}}"
        assert not old_ids & set(compounds(flow))
        assert "unrelated" in compounds(flow) and flow["pharmacy"]["opaque"] == {
            "preserve": True
        }


async def test_clear_retains_a_chain_used_by_another_input(settings):
    async with context(settings, [definition()]) as (flow, step, resolver, _route):
        await input_transforms.apply(flow, step, option(), resolver, seed="shared")
        before = copy.deepcopy(compounds(flow))
        flow["actionInstances"][2]["inputs"][0]["value"] = step.raw["inputs"][0][
            "value"
        ]
        await input_transforms.apply(
            flow, step, {"log_message": None}, resolver, seed="shared-clear"
        )
        assert compounds(flow) == before


async def test_clear_cannot_silently_restore_an_incompatible_source(settings):
    async with context(
        settings, [definition("Contains")], flow=native_flow("string", "boolean")
    ) as (flow, step, resolver, _route):
        await input_transforms.apply(flow, step, option(), resolver, seed="boolean")
        before = copy.deepcopy(flow)
        with pytest.raises(CapabilityError, match="incompatible"):
            await input_transforms.apply(
                flow, step, {"log_message": None}, resolver, seed="boolean-clear"
            )
        assert flow == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("attachedTo", "f" * 32),
        ("scopeId", "different_scope"),
        ("uiId", "different-ui"),
        ("id", "not-a-record-id"),
        ("order", 2),
    ],
)
async def test_saved_readback_declared_identities_and_orders_are_strict(
    settings, field, value
):
    async with context(settings, [definition()]) as (flow, step, resolver, _route):
        await input_transforms.apply(
            flow, step, option(), resolver, seed="saved-identities"
        )
        transform_rows(flow)[0][field] = value
        with pytest.raises((CapabilityError, ValueError)):
            await input_transforms.resolve(flow, root_pill(step), resolver, step)


async def test_full_independent_catalog_metadata_drift_changes_revision(settings):
    definitions = [definition("Trim"), definition("not documented", identity=SECOND)]
    definitions[1]["opaque_new_server_metadata"] = {"version": 1}
    async with context(settings, definitions) as (flow, _step, resolver, _route):
        first = await input_transforms.evidence(
            flow, resolver.client, transform_sys_ids=[TRANSFORM]
        )
        definitions[1]["opaque_new_server_metadata"]["version"] = 2
        http_mock.get(f"{BASE}/api/now/transformdefinitions").respond(
            200, json={"result": {"data": definitions}}
        )
        second = await input_transforms.evidence(
            flow, resolver.client, transform_sys_ids=[TRANSFORM]
        )
        assert first["definitions"][1]["opaque_new_server_metadata"] == {"version": 1}
        assert fingerprint(first) != fingerprint(second)
        assert first["transform_sys_ids"] == second["transform_sys_ids"] == [TRANSFORM]


async def test_saved_parameter_schema_drift_is_not_trusted(settings):
    parameters = [
        {
            "id": "1" * 32,
            "name": "start",
            "label": "Start Index",
            "type": "integer",
            "value": "0",
        }
    ]
    definitions = [definition("Substring", parameters=parameters)]
    async with context(settings, definitions) as (flow, step, resolver, _route):
        await input_transforms.apply(
            flow, step, option(parameters={"start": 2}), resolver, seed="schema-drift"
        )
        definitions[0]["transformCompositions"][0]["parameters"][0]["type"] = "string"
        http_mock.get(f"{BASE}/api/now/transformdefinitions").respond(
            200, json={"result": {"data": definitions}}
        )
        fresh = editing.PillResolver(resolver.client, None, flow, tree.build_tree(flow))
        with pytest.raises(CapabilityError, match="metadata differs"):
            await input_transforms.resolve(flow, root_pill(step), fresh, step)


@pytest.mark.parametrize("label", ["End of Month", "Unknown native extension"])
async def test_only_unprovable_signatures_fail_closed(settings, label):
    async with context(settings, [definition(label)]) as (flow, step, resolver, _route):
        with pytest.raises(CapabilityError):
            await input_transforms.apply(flow, step, option(), resolver, seed=label)


async def test_split_static_separator_builds_actual_array_result_schema(settings):
    parameters = [
        {"name": "separator", "label": "Separator", "type": "string", "value": ","}
    ]
    flow = native_flow("string", "array.string")
    flow["actionInstances"][1]["inputs"][0]["parameter"]["children"] = [
        {"name": "element", "type": "string", "children": []}
    ]
    async with context(
        settings, [definition("Split", parameters=parameters)], flow=flow
    ) as (flow, step, resolver, _route):
        await input_transforms.apply(
            flow, step, option(parameters={"separator": ","}), resolver, seed="split"
        )
        pill = root_pill(step)
        assert (await resolver.resolve(pill, step))["type"] == "array.string"
        assert (
            data.value_type(data.children(resolver.schemas[pill])[0]).kind == "string"
        )
        assert step.raw["inputs"][0]["children"] == []


async def test_split_empty_separator_is_source_native_identity(settings):
    parameters = [
        {"name": "separator", "label": "Separator", "type": "string", "value": ""}
    ]
    async with context(settings, [definition("Split", parameters=parameters)]) as (
        flow,
        step,
        resolver,
        _route,
    ):
        await input_transforms.apply(flow, step, option(), resolver, seed="empty-split")
        assert (await resolver.resolve(root_pill(step), step))["type"] == "string"


async def test_dynamic_split_separator_reports_its_specific_conditional_limit(settings):
    parameters = [{"name": "separator", "label": "Separator", "type": "string"}]
    async with context(settings, [definition("Split", parameters=parameters)]) as (
        flow,
        step,
        resolver,
        _route,
    ):
        with pytest.raises(CapabilityError, match="either String or Array.String"):
            await input_transforms.apply(
                flow,
                step,
                option(parameters={"separator": "{{ui-1.status}}"}),
                resolver,
                seed="dynamic-split",
            )


async def test_complex_array_item_uses_independently_fetched_producer_schema(settings):
    flow = native_flow("array.object", "object")
    object_fields = [
        {"name": "message", "label": "Message", "type": "string", "children": []}
    ]
    source = flow["actionInstances"][0]["actionType"]["outputs"][0]
    source["children"] = [{"name": "item", "type": "object", "children": object_fields}]
    target = flow["actionInstances"][1]["inputs"][0]["parameter"]
    target["children"] = copy.deepcopy(object_fields)
    async with context(
        settings, [definition("Get First Item from Array")], flow=flow
    ) as (flow, step, resolver, _route):
        await input_transforms.apply(flow, step, option(), resolver, seed="object-item")
        pill = root_pill(step)
        assert (await resolver.resolve(pill, step))["type"] == "object"
        assert resolver.schemas[pill]["children"] == object_fields
        assert resolver.definition_cache["action/" + LOG]["outputs"][0] == source


@pytest.mark.parametrize(
    "kind,value,expected",
    [
        ("integer", 4, "4"),
        ("integer", "3", "3"),
        ("decimal", 1.25, "1.25"),
        ("boolean", False, False),
        ("glide_duration", "1970-01-02 00:00:00", "1970-01-02 00:00:00"),
        ("name_values", '[{"name":"A","value":"B"}]', '[{"name":"A","value":"B"}]'),
    ],
)
async def test_native_parameter_widgets_keep_their_actual_value_wire_shape(
    settings, kind, value, expected
):
    parameters = [{"name": "setting", "label": "Setting", "type": kind}]
    async with context(settings, [definition("Substring", parameters=parameters)]) as (
        flow,
        step,
        resolver,
        _route,
    ):
        await input_transforms.apply(
            flow, step, option(parameters={"setting": value}), resolver, seed=kind
        )
        parameter = transform_rows(flow)[0]["compositions"][0]["parameters"][0]
        assert parameter["value"] == expected
        assert parameter["type"] == kind and "children" not in parameter


async def test_name_values_parameter_pills_validate_native_string_cells(settings):
    parameters = [{"name": "mapping", "label": "Mapping", "type": "name_values"}]
    value = '[{"name":"source","value":"Value={{ui-1.status}}"}]'
    async with context(
        settings, [definition("Key Value Map", parameters=parameters)]
    ) as (flow, step, resolver, _route):
        await input_transforms.apply(
            flow,
            step,
            option(parameters={"mapping": value}),
            resolver,
            seed="map-pills",
        )
        assert (
            transform_rows(flow)[0]["compositions"][0]["parameters"][0]["value"]
            == value
        )


async def test_nullable_uninitialized_pharmacy_is_native_empty_state(settings):
    flow = native_flow()
    flow["pharmacy"] = None
    async with context(settings, [definition()], flow=flow) as (
        flow,
        step,
        resolver,
        _route,
    ):
        await input_transforms.apply(
            flow, step, option(), resolver, seed="null-pharmacy"
        )
        assert len(compounds(flow)) == 2


@pytest.mark.parametrize(
    "collection", ["dynamicInputs", "workflowInputs", "decisionTableInputs"]
)
async def test_transform_targets_common_native_input_collections(settings, collection):
    flow = native_flow()
    row = flow["actionInstances"][1]["inputs"].pop()
    flow["actionInstances"][1][collection] = [row]
    async with context(settings, [definition()], flow=flow) as (
        flow,
        step,
        resolver,
        _route,
    ):
        await input_transforms.apply(flow, step, option(), resolver, seed=collection)
        assert step.raw["inputs"] == []
        pill = data.whole_pill(step.raw[collection][0]["value"])
        assert pill is not None and pill.startswith("fd_transform:")
        assert (await resolver.resolve(pill, step))["type"] == "string"


async def test_native_collection_name_ambiguity_is_rejected(settings):
    flow = native_flow()
    flow["actionInstances"][1]["workflowInputs"] = copy.deepcopy(
        flow["actionInstances"][1]["inputs"]
    )
    async with context(settings, [definition()], flow=flow) as (
        flow,
        step,
        resolver,
        _route,
    ):
        before = copy.deepcopy(flow)
        with pytest.raises(CapabilityError, match="ambiguous"):
            await input_transforms.apply(
                flow, step, option(), resolver, seed="ambiguous"
            )
        assert flow == before


async def test_planner_revision_includes_full_independent_transform_metadata(settings):
    flow = native_flow()
    definitions = [definition(), definition("Unselected extension", identity=SECOND)]
    fixtures(settings, flow)
    action_read = mock_log_definition(flow)
    http_mock.get(f"{BASE}/api/now/transformdefinitions").respond(
        200, json={"result": {"data": definitions}}
    )
    edit = {"op": "update_inputs", "step": "ui-2", "input_transforms": option()}
    first = await plan(settings, flow, [edit])
    definitions[1]["server_metadata"]["revision"] = "changed independently"
    http_mock.get(f"{BASE}/api/now/transformdefinitions").respond(
        200, json={"result": {"data": definitions}}
    )
    second = await plan(settings, flow, [edit])
    assert first["payload"] == second["payload"]
    assert first["reference_revision"] != second["reference_revision"]
    assert action_read.call_count >= 2


async def test_observed_native_upper_case_composition_shape(settings):
    # Sanitized read-only metadata supplied by NativeAuthoringProof. The native
    # label is 'To Upper Case'; 'Upper Case' was only a human shorthand.
    identity = "b863a54cc76333003884f80bf4c26054"
    composition_id = "57fa5919b7233300388487c2de11a92f"
    native = {
        "id": identity,
        "label": "To Upper Case",
        "transformCompositions": [
            {
                "id": composition_id,
                "transformType": "eef1a9c8c76333003884f80bf4c26097",
                "transformDefinition": identity,
                "order": 1,
                "parameters": [],
            }
        ],
    }
    async with context(settings, [native]) as (flow, step, resolver, route):
        recorded = await input_transforms.apply(
            flow, step, option(identity), resolver, seed="observed-native-upper-case"
        )
        compound = transform_rows(flow)[0]
        assert compound["transformDefinitionId"] == identity
        assert compound["compositions"][0]["id"] == composition_id
        assert compound["compositions"][0]["order"] == 0
        assert compound["compositions"][0]["parameters"] == []
        assert set(compound["compositions"][0]) == {"id", "order", "parameters"}
        assert "transformType" not in compound["compositions"][0]
        assert "transformDefinition" not in compound["compositions"][0]
        assert recorded[0]["definitions"] == [native]
        assert (await resolver.resolve(root_pill(step), step))["type"] == "string"
        assert route.call_count == 1


@pytest.mark.parametrize(
    "label,source_type,target_type,parameters,values",
    [
        (
            "Contains?",
            "string",
            "boolean",
            [{"name": "string", "type": "string", "mandatory": True, "value": ""}],
            {"string": "needle"},
        ),
        (
            "Does not Contain?",
            "string",
            "boolean",
            [{"name": "string", "type": "string", "mandatory": True, "value": ""}],
            {"string": "needle"},
        ),
        (
            "Ends With?",
            "string",
            "boolean",
            [{"name": "string", "type": "string", "mandatory": True, "value": ""}],
            {"string": "suffix"},
        ),
        (
            "Starts With?",
            "string",
            "boolean",
            [{"name": "string", "type": "string", "mandatory": True, "value": ""}],
            {"string": "prefix"},
        ),
        ("Is Blank?", "string", "boolean", None, None),
        ("Is False?", "integer", "boolean", None, None),
        ("Is Not Blank?", "string", "boolean", None, None),
        ("Is Null?", "reference", "boolean", None, None),
        ("Is True?", "integer", "boolean", None, None),
        (
            "Get item from Name/Values",
            "name_values",
            "string",
            [
                {"name": "key", "type": "string", "value": ""},
                {"name": "default", "type": "string", "value": ""},
            ],
            {"key": "ticket"},
        ),
        (
            "Get item from Name/Values",
            "simple_name_values",
            "string",
            [
                {"name": "key", "type": "string", "value": ""},
                {"name": "default", "type": "string", "value": ""},
            ],
            {"key": "ticket"},
        ),
        ("Sanitize Bash Shell Arguments", "string", "string", None, None),
    ],
)
async def test_observed_native_labels_match_documented_signatures_exactly(
    settings, label, source_type, target_type, parameters, values
):
    async with context(
        settings,
        [definition(label, parameters=parameters)],
        flow=native_flow(source_type, target_type),
    ) as (flow, step, resolver, _route):
        await input_transforms.apply(
            flow, step, option(parameters=values), resolver, seed=label
        )
        assert (await resolver.resolve(root_pill(step), step))["type"] == target_type
        assert resolver.input_transform_metadata["definitions"][0]["label"] == label


async def test_observed_native_math_int_parameter_keeps_type_and_wire_value(settings):
    identity = "50980bfa23a220103a3339e1d7bf6510"
    composition_id = "06190ffa23a220103a3339e1d7bf65f9"
    native = {
        "id": identity,
        "label": "Add",
        "suggestedTypes": "number",
        "transformCompositions": [
            {
                "id": composition_id,
                "transformType": "81e383ba23a220103a3339e1d7bf65b8",
                "transformDefinition": identity,
                "order": 1,
                "parameters": [
                    {
                        "name": "number",
                        "label": "Number",
                        "order": 100,
                        "attributes": {},
                        "value": "",
                        "type": "int",
                        "defaultValue": "",
                        "mandatory": True,
                        "choices": [],
                    }
                ],
            }
        ],
    }
    values = {
        "log_message": {
            "source": "{{ui-1.status}}",
            "transforms": [
                {
                    "transform_sys_id": identity,
                    "compositions": [
                        {
                            "composition_sys_id": composition_id,
                            "parameters": {"number": 4},
                        }
                    ],
                }
            ],
        }
    }
    async with context(settings, [native], flow=native_flow("number", "number")) as (
        flow,
        step,
        resolver,
        _route,
    ):
        await input_transforms.apply(
            flow, step, values, resolver, seed="native-math-int"
        )
        parameter = transform_rows(flow)[0]["compositions"][0]["parameters"][0]
        assert parameter["type"] == "int" and parameter["value"] == "4"
        assert (await resolver.resolve(root_pill(step), step))["type"] == "number"


@pytest.mark.parametrize("value", [-1, 1000])
async def test_observed_substring_integer_limits_are_enforced(settings, value):
    parameters = [
        {
            "name": "start_index",
            "label": "Start Index",
            "type": "integer",
            "mandatory": True,
            "value": "",
            "attributes": {"min_value": "0", "max_value": "999"},
        }
    ]
    async with context(settings, [definition("Substring", parameters=parameters)]) as (
        flow,
        step,
        resolver,
        _route,
    ):
        before = copy.deepcopy(flow)
        with pytest.raises(CapabilityError):
            await input_transforms.apply(
                flow,
                step,
                option(parameters={"start_index": value}),
                resolver,
                seed="native-range",
            )
        assert flow == before


async def test_native_custom_date_format_is_conditionally_mandatory(settings):
    parameters = [
        {
            "name": "date_format",
            "label": "Input Date Format",
            "type": "choice",
            "mandatory": True,
            "value": "",
            "choices": [{"label": "Custom (enter below)", "value": "NULL_OVERRIDE"}],
        },
        {
            "name": "custom_format",
            "label": "Custom Format",
            "type": "string",
            "mandatory": False,
            "value": "",
        },
    ]
    async with context(
        settings,
        [definition("String to Date", parameters=parameters)],
        flow=native_flow("string", "glide_date_time"),
    ) as (flow, step, resolver, _route):
        with pytest.raises(CapabilityError, match="Custom Format is required"):
            await input_transforms.apply(
                flow,
                step,
                option(parameters={"date_format": "NULL_OVERRIDE"}),
                resolver,
                seed="custom-date",
            )
        await input_transforms.apply(
            flow,
            step,
            option(
                parameters={
                    "date_format": "NULL_OVERRIDE",
                    "custom_format": "yyyy-MM-dd",
                }
            ),
            resolver,
            seed="custom-date",
        )
        assert (await resolver.resolve(root_pill(step), step))[
            "type"
        ] == "glide_date_time"


async def test_existing_saved_nested_ui_metadata_remains_strict_and_unchanged(settings):
    parameters = [
        {"name": "start_index", "type": "integer", "mandatory": True, "value": "0"}
    ]
    async with context(settings, [definition("Substring", parameters=parameters)]) as (
        flow,
        step,
        resolver,
        _route,
    ):
        await input_transforms.apply(
            flow, step, option(), resolver, seed="existing-ui-metadata"
        )
        composition = transform_rows(flow)[0]["compositions"][0]
        composition["uiId"] = "existing-composition-ui"
        composition["opaque"] = {"preserve": True}
        composition["parameters"][0]["uiId"] = "existing-parameter-ui"
        composition["parameters"][0]["opaque"] = {"preserve": True}
        before = copy.deepcopy(flow)
        assert (await input_transforms.resolve(flow, root_pill(step), resolver, step))[
            "type"
        ] == "string"
        assert flow == before


@pytest.mark.parametrize("display_drift", [None, "echo", "independent_get"])
async def test_saved_log_transform_string_display_round_trips_exactly(
    settings, display_drift
):
    # Source-shaped regression from native_edit_4_request/echo/readback: an
    # existing Log consumer's String transform pill is also its displayValue.
    # Values are synthetic; no authored native payload text is copied here.
    flow = native_flow()
    flow["actionInstances"] = flow["actionInstances"][:2]
    source = "{{ui-1.__action_status__.message}}"
    flow["actionInstances"][0]["actionType"]["outputs"] = [
        {
            "name": "__action_status__",
            "label": "Action Status",
            "type": "object",
            "children": [
                {
                    "name": "message",
                    "label": "Message",
                    "type": "string",
                    "readonly": True,
                    "maxsize": 4000,
                    "children": [],
                }
            ],
        }
    ]
    compounds(flow)["unrelated"]["prescription"] = source
    old_root = "af6a9a83-183b-57c9-b171-e43d39be0367"
    old_transform = "249ea5be-4248-5f56-b6c4-670210708c53"
    upper = "b863a54cc76333003884f80bf4c26054"
    lower = "a809ecb7b7133300388487c2de11a9f7"
    definitions = [
        {
            "id": upper,
            "label": "To Upper Case",
            "transformCompositions": [
                {"id": "57fa5919b7233300388487c2de11a92f", "order": 1, "parameters": []}
            ],
        },
        {
            "id": lower,
            "label": "To Lower Case",
            "transformCompositions": [
                {"id": "e0fce048c72333003884f80bf4c26002", "order": 1, "parameters": []}
            ],
        },
    ]
    compounds(flow).update(
        {
            old_root: {
                "id": "6" * 32,
                "uiId": old_root,
                "sourceUiId": "",
                "order": 0,
                "prescription": source,
                "transformDefinitionId": "",
                "compositions": [],
                "attachedTo": FLOW,
                "scopeId": "global",
            },
            old_transform: {
                "id": "7" * 32,
                "uiId": old_transform,
                "sourceUiId": old_root,
                "order": 1,
                "prescription": "",
                "transformDefinitionId": upper,
                "compositions": [
                    {
                        "id": "57fa5919b7233300388487c2de11a92f",
                        "order": 0,
                        "parameters": [],
                    }
                ],
                "attachedTo": FLOW,
                "scopeId": "global",
            },
        }
    )
    consumer = flow["actionInstances"][1]
    consumer["inputs"][0].update(
        id="8" * 32,
        value="{{fd_transform:" + old_root + "}}",
        displayValue="{{fd_transform:" + old_root + "}}",
        children=[],
        scriptActive=False,
    )
    consumer["data"]["log_message"] = consumer["inputs"][0]["value"]
    before = copy.deepcopy(flow)
    _, tools = fixtures(settings, flow)
    action_read = mock_log_definition(flow)
    metadata_read = http_mock.get(f"{BASE}/api/now/transformdefinitions").respond(
        200, json={"result": {"data": definitions}}
    )
    observed: dict[str, Any] = {}

    def save(request):
        payload = json.loads(request.content)
        outgoing = payload["actionInstances"][1]["inputs"][0]
        assert outgoing["id"] == "8" * 32
        assert outgoing["parameter"]["type"] == "string"
        assert outgoing["value"].startswith("{{fd_transform:")
        assert outgoing["displayValue"] == outgoing["value"]
        observed["outgoing"] = copy.deepcopy(outgoing)
        echo = copy.deepcopy(payload)
        for index, row in enumerate(compounds(echo).values(), start=64):
            if not row.get("id"):
                row.update(id=f"{index:032x}", attachedTo=FLOW, scopeId="global")
                if row["order"] == 0:
                    row.update(sourceUiId="", transformDefinitionId="", compositions=[])
                else:
                    row["prescription"] = ""
        actual = copy.deepcopy(echo)
        if display_drift == "echo":
            echo["actionInstances"][1]["inputs"][0]["displayValue"] = ""
        elif display_drift == "independent_get":
            actual["actionInstances"][1]["inputs"][0]["displayValue"] = ""
        observed["echo"] = copy.deepcopy(echo)
        observed["actual"] = copy.deepcopy(actual)
        persist(actual)
        return httpx2.Response(200, json={"result": {"data": echo}})

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
                    "step": consumer["uiUniqueIdentifier"],
                    "input_transforms": {
                        "log_message": {
                            "source": source,
                            "transforms": [{"transform_sys_id": lower}],
                        }
                    },
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    assert not write.called and flow == before
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert write.call_count == 1 and metadata_read.call_count >= 2
    assert action_read.call_count >= 2
    if display_drift:
        assert result["error"]["code"] == "UNKNOWN_OUTCOME"
    else:
        assert result["status"] == "success", result
        saved = observed["actual"]
        assert saved["actionInstances"][1]["inputs"][0] == observed["outgoing"]
        assert saved["actionInstances"][1]["id"] == before["actionInstances"][1]["id"]
        assert saved["actionInstances"][0]["id"] == before["actionInstances"][0]["id"]
        assert (
            saved["actionInstances"][0]["inputs"]
            == before["actionInstances"][0]["inputs"]
        )
        assert compounds(saved)["unrelated"] == compounds(before)["unrelated"]
        assert old_root not in compounds(saved) and old_transform not in compounds(
            saved
        )
        chain = transform_rows(saved)
        assert len(chain) == 1 and chain[0]["transformDefinitionId"] == lower
        assert observed["echo"] == saved
        assert (
            sum(
                call.request.method == "GET"
                and call.request.url.path == f"/api/now/processflow/flow/{FLOW}"
                for call in http_mock.calls
            )
            >= 3
        )
    assert flow == before
