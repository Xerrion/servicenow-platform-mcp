"""Source-native dynamic authoring with synthetic, independently resolved providers."""

import copy
import json
from typing import Any

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import (
    calls,
    connection_inputs,
    data,
    dynamic_inputs,
    dynamic_schemas,
    editing,
    input_transforms,
    tree,
)
from servicenow_mcp.capabilities.mutations.models import FlowCommand
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.test_flow_editing import BASE, FLOW, base_flow, fixtures, persist
from tests.test_flow_input_transforms import TRANSFORM
from tests.test_flow_input_transforms import definition as transform_definition
from tests.test_flow_schema_providers import (
    CHILD_ALIAS,
    OTHER_CHILD_ALIAS,
    OTHER_SOURCE_ALIAS,
    OWNER,
    PROVIDER,
    RECORD,
    SOURCE_ALIAS,
    ProviderClient,
    alias_context,
    command,
    resolved,
)


def fields() -> list[dict[str, Any]]:
    return [
        {
            "name": "quantity",
            "label": "Quantity",
            "type": "integer",
            "order": 0,
            "mandatory": True,
            "defaultValue": 9,
            "defaultDisplayValue": "Nine",
        },
        {
            "name": "enabled",
            "label": "Enabled",
            "type": "boolean",
            "defaultValue": True,
        },
        {
            "name": "message",
            "label": "Message",
            "type": "string",
            "defaultValue": "Default",
        },
        {
            "name": "decision",
            "label": "Decision",
            "type": "choice",
            "choices": [
                {"value": "approve", "label": "Approve"},
                {"value": "reject", "label": "Reject"},
            ],
        },
        {
            "name": "assignee",
            "label": "Assignee",
            "type": "reference",
            "reference": "sys_user",
            "reference_display": "name",
            "ref_qual": "active=true",
        },
    ]


def provider(
    mode: str = "dynamic_inputs", schema: list[dict[str, Any]] | None = None
) -> ProviderClient:
    fake = ProviderClient(mode)
    fake.output = {"data": copy.deepcopy(fields() if schema is None else schema)}
    return fake


def supplied(**changes: Any) -> dict[str, Any]:
    return {
        "quantity": 0,
        "enabled": False,
        "message": "A ^ literal = value",
        "decision": "approve",
        "assignee": RECORD,
        **changes,
    }


def binding(
    definition: dict[str, Any], evidence: dict[str, Any], value: Any
) -> dict[str, Any]:
    step = calls.step(
        base_flow(),
        copy.deepcopy(definition),
        {"requester": RECORD, "fields": copy.deepcopy(value)},
        subflow=False,
        uid="dynamic-consumer",
        schema_evidence=[evidence],
    )
    return data.named(step.raw["inputs"], "fields")


def pill_flow() -> dict[str, Any]:
    flow = copy.deepcopy(base_flow())
    outputs = copy.deepcopy(fields())
    outputs.append(
        {
            "name": "wide_decision",
            "label": "Wide decision",
            "type": "choice",
            "choices": [
                {"value": "approve", "label": "Approve"},
                {"value": "other", "label": "Other"},
            ],
        }
    )
    outputs.append(
        {
            "name": "ticket",
            "label": "Ticket",
            "type": "reference",
            "reference": "incident",
        }
    )
    flow["actionInstances"][0]["actionType"]["outputs"] = outputs
    return flow


def authoring_routes(
    settings: Any, flow: dict[str, Any], definition: dict[str, Any]
) -> None:
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/processflow/action/action_types/{OWNER}").respond(
        200,
        json={"result": copy.deepcopy(definition)},
    )


async def plan(
    settings: Any, edits: list[dict[str, Any]], evidence: list[dict[str, Any]]
) -> tuple[FlowCommand, dict[str, Any]]:
    request = FlowCommand(
        action="edit", sys_id=FLOW, edits=copy.deepcopy(edits)
    ).model_copy(
        update={"schema_evidence": copy.deepcopy(evidence)},
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await editing.plan(
            request, await editing.load_state(request, client), client
        )
    return request, result


async def test_calls_step_packs_native_zero_false_and_preserves_immutable_action_definition() -> (
    None
):
    fake = provider()
    evidence = await resolved(fake)
    definition = copy.deepcopy(fake.definitions[OWNER])
    original = copy.deepcopy(definition)
    inputs = {"requester": RECORD, "fields": supplied()}
    original_inputs = copy.deepcopy(inputs)
    original_evidence = copy.deepcopy(evidence)

    step = calls.step(
        base_flow(),
        definition,
        inputs,
        subflow=False,
        uid="dynamic-consumer",
        schema_evidence=[evidence],
    )
    item = data.named(step.raw["inputs"], "fields")
    packed = json.loads(item["value"])
    assert packed["quantity"] == {
        "value": "0",
        "display_value": "",
        "definition": {
            "label": "Quantity",
            "type": "integer",
            "order": 0,
            "mandatory": True,
            "defaultValue": 9,
            "defaultDisplayValue": "Nine",
            "readonly": False,
            "use_dependent": False,
            "type_label": "Integer",
        },
    }
    assert packed["enabled"]["value"] is False
    assert packed["message"]["value"] == "A ^ literal = value"
    assert packed["decision"]["value"] == "approve"
    assert packed["assignee"]["value"] == RECORD
    assert packed["assignee"]["definition"]["reference"] == "sys_user"
    assert item["children"] == []
    assert step.raw["actionType"] == original
    assert step.raw["actionType"]["inputs"][1] == original["inputs"][1]
    assert definition == original
    assert inputs == original_inputs
    assert evidence == original_evidence

    resolver = editing.PillResolver(fake.client, None)
    leaves = {
        leaf["name"]: (leaf["value"], data.value_type(parameter))
        for leaf, parameter in resolver._bindings(item)
    }
    assert leaves["quantity"][0] == 0 and type(leaves["quantity"][0]) is int
    assert leaves["enabled"][0] is False
    assert leaves["quantity"][1].kind == "integer"
    assert leaves["decision"][1].kind == "choice"
    assert leaves["assignee"][1].reference == "sys_user"


async def test_packed_values_roundtrip_without_replacing_typed_pills_or_display_metadata() -> (
    None
):
    fake = provider()
    evidence = await resolved(fake)
    item = binding(
        fake.definitions[OWNER],
        evidence,
        supplied(
            quantity="{{ui-1.quantity}}",
            enabled="{{ui-1.enabled}}",
            message="Selected {{ui-1.message}}",
            decision="{{ui-1.decision}}",
            assignee="{{ui-1.assignee}}",
        ),
    )
    packed = json.loads(item["value"])
    packed["assignee"]["display_value"] = "Fixture User"
    wire = json.dumps(packed, ensure_ascii=False)
    read = binding(fake.definitions[OWNER], evidence, wire)
    assert read["value"] == wire
    assert json.loads(read["value"])["assignee"]["display_value"] == "Fixture User"
    resolver = editing.PillResolver(fake.client, None)
    leaves = {leaf["name"]: leaf["value"] for leaf, _ in resolver._bindings(read)}
    assert leaves == {
        "quantity": "{{ui-1.quantity}}",
        "enabled": "{{ui-1.enabled}}",
        "message": "Selected {{ui-1.message}}",
        "decision": "{{ui-1.decision}}",
        "assignee": "{{ui-1.assignee}}",
    }


@pytest.mark.parametrize(
    "output",
    [
        {},
        {"data": None},
        {"data": {}},
        {"data": []},
        {"data": ["field"]},
        {"data": [{"name": "message", "label": "Message", "type": "object"}]},
        {"data": [{"name": "message", "label": "Message", "type": "array.string"}]},
        {"data": [{"name": "Bad.Name", "label": "Message", "type": "string"}]},
        {"data": [{"name": "message", "label": "", "type": "string"}]},
        {"data": [{"name": "message", "label": "Message", "type": "unknown_widget"}]},
        {
            "data": [
                {
                    "name": "message",
                    "label": "Message",
                    "type": "string",
                    "children": [{"name": "child"}],
                }
            ]
        },
        {
            "data": [
                {
                    "name": "message",
                    "label": "Message",
                    "type": "string",
                    "mandatory": "true",
                }
            ]
        },
        {
            "data": [
                {
                    "name": "message",
                    "label": "Message",
                    "type": "string",
                    "order": False,
                }
            ]
        },
        {
            "data": [
                {
                    "name": "message",
                    "label": "Message",
                    "type": "string",
                    "dependent_on": "requester",
                }
            ]
        },
        {
            "data": [
                {
                    "name": "message",
                    "label": "Message",
                    "type": "string",
                    "dynamic": {"actionId": OWNER},
                }
            ]
        },
        {"data": [{"name": "message", "label": "{{ui-1.message}}", "type": "string"}]},
        {
            "data": [
                {
                    "name": "quantity",
                    "label": "Quantity",
                    "type": "integer",
                    "defaultValue": "many",
                }
            ]
        },
        {
            "data": [
                {
                    "name": "decision",
                    "label": "Decision",
                    "type": "choice",
                    "choices": [],
                }
            ]
        },
        {"data": [{"name": "assignee", "label": "Assignee", "type": "reference"}]},
        {
            "data": [
                {
                    "name": "assignee",
                    "label": "Assignee",
                    "type": "reference",
                    "reference": "sys_user.name",
                }
            ]
        },
        {
            "data": [
                {
                    "name": "assignee",
                    "label": "Assignee",
                    "type": "reference",
                    "reference": "sys_user",
                    "ref_qual": "javascript:gs.getUserID()",
                }
            ]
        },
        {
            "data": [
                {"name": "message", "label": "Message"},
                {"name": "message", "label": "Again"},
            ]
        },
    ],
)
def test_dynamic_field_schema_rejects_unknown_malformed_and_unserializable_fields(
    output: dict[str, Any],
) -> None:
    with pytest.raises(CapabilityError):
        dynamic_inputs.validate_schema("dynamic_inputs", copy.deepcopy(output))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("quantity", False),
        ("quantity", "zero"),
        ("quantity", 1.5),
        ("enabled", "false"),
        ("decision", "Approve"),
        ("decision", "other"),
        ("assignee", "not-a-sys-id"),
        ("message", {"value": "text"}),
        ("unknown", "value"),
    ],
)
async def test_adoption_enforces_leaf_types_choice_values_reference_ids_and_known_names(
    name: str, value: Any
) -> None:
    fake = provider()
    evidence = await resolved(fake)
    with pytest.raises((CapabilityError, ValueError)):
        binding(fake.definitions[OWNER], evidence, supplied(**{name: value}))


@pytest.mark.parametrize(
    "defect",
    [
        "json",
        "shape",
        "entry",
        "extra_metadata",
        "missing_metadata",
        "display_pill",
        "integer",
        "choice",
    ],
)
async def test_saved_dynamic_json_is_parsed_independently_of_provider_schema(
    defect: str,
) -> None:
    fake = provider()
    evidence = await resolved(fake)
    item = binding(fake.definitions[OWNER], evidence, supplied())
    packed = json.loads(item["value"])
    if defect == "json":
        wire = "{broken"
    elif defect == "shape":
        wire = json.dumps(list(packed.values()))
    else:
        if defect == "entry":
            packed["quantity"].pop("definition")
        elif defect == "extra_metadata":
            packed["quantity"]["definition"]["children"] = []
        elif defect == "missing_metadata":
            packed["quantity"]["definition"].pop("readonly")
        elif defect == "display_pill":
            packed["assignee"]["display_value"] = "{{ui-1.assignee}}"
        elif defect == "integer":
            packed["quantity"]["value"] = "zero"
        else:
            packed["decision"]["value"] = "other"
        wire = json.dumps(packed)
    with pytest.raises(CapabilityError):
        binding(fake.definitions[OWNER], evidence, wire)


@pytest.mark.parametrize(
    "defect", ["missing", "duplicate", "owner", "input", "definition", "mode"]
)
async def test_evidence_is_bound_to_exact_owner_input_mode_and_immutable_definition(
    defect: str,
) -> None:
    fake = provider()
    evidence = await resolved(fake)
    definition = copy.deepcopy(fake.definitions[OWNER])
    tokens = [copy.deepcopy(evidence)]
    if defect == "missing":
        tokens = []
    elif defect == "duplicate":
        tokens.append(copy.deepcopy(evidence))
    elif defect == "owner":
        definition["id"] = "f" * 32
    elif defect == "input":
        definition["inputs"][1]["name"] = "different_fields"
    elif defect == "definition":
        definition["inputs"][0]["mandatory"] = True
    else:
        fake = provider("dynamic_choice", [{"name": "approve", "label": "Approve"}])
        tokens = [await resolved(fake)]
    with pytest.raises(CapabilityError) as error:
        dynamic_inputs.adopt(
            definition, {"requester": RECORD, "fields": supplied()}, tokens
        )
    assert error.value.code == (
        "STALE_PREVIEW" if defect in {"definition", "mode"} else "UNAVAILABLE"
    )


@pytest.mark.parametrize("context_name", ["requester", "region"])
async def test_adoption_rechecks_linked_dependencies_and_other_static_resolution_context(
    context_name: str,
) -> None:
    fake = provider()
    fake.definitions[OWNER]["inputs"].append(
        {"name": "region", "type": "string", "defaultValue": "north"}
    )
    evidence = await resolved(fake)
    assert evidence["plan"]["inputs"]["region"] == "north"
    assert "region" not in evidence["sibling_inputs"]
    values = {"requester": RECORD, "region": "north", "fields": supplied()}
    values[context_name] = "e" * 32 if context_name == "requester" else "south"
    with pytest.raises(CapabilityError) as error:
        dynamic_inputs.adopt(fake.definitions[OWNER], values, [evidence])
    assert error.value.code == "STALE_PREVIEW"


@pytest.mark.parametrize("drift", ["choices", "reference"])
async def test_saved_field_schema_cannot_be_reinterpreted_by_new_provider_output(
    drift: str,
) -> None:
    fake = provider()
    first = await resolved(fake)
    saved = binding(fake.definitions[OWNER], first, supplied())["value"]
    if drift == "choices":
        fake.output["data"][3]["choices"].append({"value": "other", "label": "Other"})
    else:
        fake.output["data"][4]["reference"] = "incident"
    current = await resolved(fake)
    with pytest.raises(CapabilityError) as error:
        binding(fake.definitions[OWNER], current, saved)
    assert error.value.code == "STALE_PREVIEW"


@pytest.mark.parametrize(
    "drift", ["table_identity", "display_field", "missing_binding"]
)
async def test_reference_schema_evidence_requires_independent_current_table_metadata(
    drift: str,
) -> None:
    fake = provider()
    evidence = await resolved(fake)
    if drift == "missing_binding":
        evidence["schema_reference_bindings"] = {}
        with pytest.raises(CapabilityError):
            binding(fake.definitions[OWNER], evidence, supplied())
    else:
        if drift == "table_identity":
            fake.tables["sys_user"]["sys_id"] = "b" * 32
        else:
            fake.descriptors["sys_user"]["displayField"] = "email"
        with pytest.raises(CapabilityError) as error:
            await dynamic_schemas.verify(evidence, fake.client)
        assert error.value.code == "STALE_PREVIEW"


async def test_dynamic_choice_projects_names_to_values_without_dynamic_field_json() -> (
    None
):
    fake = provider(
        "dynamic_choice",
        [
            {"name": "approve", "label": "Approve"},
            {"name": "reject", "label": "Reject"},
        ],
    )
    fake.definitions[OWNER]["inputs"][1].update(
        choiceOption="1",
        choices=[{"value": "", "label": "No decision", "order": 0}],
    )
    evidence = await resolved(fake)
    original = copy.deepcopy(fake.definitions[OWNER])
    item = binding(original, evidence, "approve")
    expected = [
        {"label": "No decision", "value": "", "order": 0},
        {"label": "Approve", "value": "approve", "order": 1},
        {"label": "Reject", "value": "reject", "order": 2},
    ]
    assert item["value"] == "approve"
    assert item["parameter"]["choices"] == expected
    assert item["choices"] == expected
    leaves = dynamic_inputs.bindings(item)
    assert leaves[0]["value"] == "approve"
    assert data.spec(leaves[0])["type"] == "choice"
    assert data.spec(leaves[0])["choices"] == expected
    assert "columns" not in item
    assert binding(original, evidence, "")["value"] == ""
    for value in ("Approve", "other", {"approve": True}):
        with pytest.raises(CapabilityError):
            binding(original, evidence, value)
    assert fake.definitions[OWNER] == original


async def test_dynamic_template_keeps_encoded_selection_separate_from_resolved_columns() -> (
    None
):
    fake = provider("dynamic_template")
    evidence = await resolved(fake)
    original = copy.deepcopy(fake.definitions[OWNER])
    reference = json.dumps(
        {"table": "sys_user", "value": RECORD, "sys_id": RECORD}, separators=(",", ":")
    )
    encoded = (
        "quantity=0^enabled=false^message=literal^^caret={{ui-1.message}}^decision=approve^assignee="
        + reference
    )
    item = binding(original, evidence, encoded)
    assert item["value"] == encoded
    columns = {row["name"]: row for row in item["columns"]}
    assert columns["quantity"]["value"] == "quantity"
    assert columns["quantity"]["type"] == "integer"
    assert columns["quantity"]["defaultValue"] == 9
    assert columns["assignee"]["reference"] == "sys_user"
    assert columns["decision"]["choices"] == fields()[3]["choices"]
    leaves = {row["name"]: row for row in dynamic_inputs.bindings(item)}
    assert leaves["enabled"]["value"] is False
    assert leaves["message"]["value"] == "literal^caret={{ui-1.message}}"
    assert leaves["decision"]["parameter"]["type"] == "choice"
    assert original["inputs"][1].get("columns") is None
    assert fake.definitions[OWNER] == original
    for value in (
        {"message": "text"},
        '{"message":"text"}',
        "unknown=text",
        "decision=other",
        "message=one^message=two",
    ):
        with pytest.raises(CapabilityError):
            binding(original, evidence, value)


@pytest.mark.parametrize(
    "mode", ["dynamic_inputs", "dynamic_choice", "dynamic_template", "unknown"]
)
def test_choice_provider_rows_are_not_interpreted_as_typed_field_definitions(
    mode: str,
) -> None:
    output = {"data": [{"name": "approve", "label": "Approve", "type": "object"}]}
    if mode == "dynamic_choice":
        rows = dynamic_inputs.validate_schema(mode, output)
        assert rows[0]["name"] == "approve"
    else:
        with pytest.raises(CapabilityError):
            dynamic_inputs.validate_schema(mode, output)


@pytest.mark.parametrize("mode", ["dynamic_choice", "dynamic_template"])
async def test_editing_resolves_pills_using_each_dynamic_modes_native_value_shape(
    settings: Any, mode: str
) -> None:
    schema = (
        [{"name": "approve", "label": "Approve"}, {"name": "reject", "label": "Reject"}]
        if mode == "dynamic_choice"
        else fields()
    )
    fake = provider(mode, schema)
    evidence = await resolved(fake)
    definition = copy.deepcopy(fake.definitions[OWNER])
    if mode == "dynamic_choice":
        value = "{{ui-1.decision}}"
    else:
        value = "quantity={{ui-1.quantity}}^enabled={{ui-1.enabled}}^message=Chosen {{ui-1.message}}^decision={{ui-1.decision}}^assignee={{ui-1.assignee}}"
    authoring_routes(settings, pill_flow(), definition)
    _, result = await plan(
        settings,
        [
            {
                "op": "insert_action",
                "action_type_sys_id": OWNER,
                "after": "ui-1",
                "inputs": {"requester": RECORD, "fields": value},
            }
        ],
        [evidence],
    )
    consumer = next(
        row
        for row in result["payload"]["actionInstances"]
        if row["actionTypeSysId"] == OWNER
    )
    item = data.named(consumer["inputs"], "fields")
    assert item["value"] == value
    assert consumer["actionType"]["inputs"] == definition["inputs"]
    labels = {row["name"]: row for row in result["payload"]["label_cache"]}
    assert {row["value"] for row in labels["ui-1.decision"]["choices"]} == {
        "approve",
        "reject",
    }
    if mode == "dynamic_choice":
        assert item["parameter"]["choices"] == [
            {"label": "Approve", "value": "approve", "order": 0},
            {"label": "Reject", "value": "reject", "order": 1},
        ]
        assert "columns" not in item["parameter"]
    else:
        assert labels["ui-1.quantity"]["type"] == "integer"
        assert labels["ui-1.enabled"]["type"] == "boolean"
        assert labels["ui-1.assignee"]["reference"] == "sys_user"
        assert {row["name"] for row in item["parameter"]["columns"]} == {
            "quantity",
            "enabled",
            "message",
            "decision",
            "assignee",
        }


@pytest.mark.parametrize("existing", [False, True], ids=["insert", "update"])
async def test_editing_dynamic_insert_update_and_independent_saved_readback(
    settings: Any, existing: bool
) -> None:
    fake = provider()
    evidence = await resolved(fake)
    definition = copy.deepcopy(fake.definitions[OWNER])
    flow = pill_flow()
    if existing:
        saved_step = calls.step(
            flow,
            definition,
            {"requester": RECORD, "fields": supplied()},
            subflow=False,
            uid="saved-dynamic",
            schema_evidence=[evidence],
        ).raw
        saved_step.update(id="e" * 32, parent="", order=2, isNew=False)
        saved_step["inputs"][1]["opaque_binding"] = {"keep": True}
        flow["actionInstances"][1] = saved_step
        edit = {
            "op": "update_inputs",
            "step": "saved-dynamic",
            "inputs": {
                "fields": supplied(
                    quantity="{{ui-1.quantity}}",
                    message="Chosen {{ui-1.message}}",
                    decision="{{ui-1.decision}}",
                    assignee="{{ui-1.assignee}}",
                )
            },
        }
    else:
        edit = {
            "op": "insert_action",
            "action_type_sys_id": OWNER,
            "after": "ui-1",
            "ref": "dynamic",
            "inputs": {
                "requester": RECORD,
                "fields": supplied(message="Chosen {{ui-1.message}}"),
            },
        }
    original = copy.deepcopy(flow)
    authoring_routes(settings, flow, definition)
    request, result = await plan(settings, [edit], [evidence])
    consumer = next(
        row
        for row in result["payload"]["actionInstances"]
        if row["actionTypeSysId"] == OWNER
    )
    item = data.named(consumer["inputs"], "fields")
    packed = json.loads(item["value"])
    assert packed["quantity"]["value"] == ("{{ui-1.quantity}}" if existing else "0")
    assert packed["enabled"]["value"] is False
    assert {key: consumer["actionType"][key] for key in definition} == definition
    assert item["parameter"]["dynamic"] == definition["inputs"][1]["dynamic"]
    if existing:
        assert item["opaque_binding"] == {"keep": True}
    labels = {row["name"]: row for row in result["payload"]["label_cache"]}
    assert labels["ui-1.message"]["type"] == "string"
    if existing:
        assert labels["ui-1.quantity"]["type"] == "integer"
        assert {row["value"] for row in labels["ui-1.decision"]["choices"]} == {
            "approve",
            "reject",
        }
        assert labels["ui-1.assignee"]["reference"] == "sys_user"
    assert flow == original

    persisted: dict[str, Any] = {}

    def save(http_request: httpx2.Request) -> httpx2.Response:
        body = json.loads(http_request.content)
        row = next(
            row for row in body["actionInstances"] if row["actionTypeSysId"] == OWNER
        )
        row.setdefault("id", "e" * 32)
        persisted.update(copy.deepcopy(body))
        persist(copy.deepcopy(body))
        return httpx2.Response(200, json={"result": {"data": body}})

    http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        applied = await editing.save_plan(request, result, client, binding_context=None)
        readback = await client.studio_request(f"flow/{FLOW}")
    assert applied["sys_id"] == FLOW
    saved = next(
        row for row in readback["actionInstances"] if row["actionTypeSysId"] == OWNER
    )
    assert saved["actionType"] == consumer["actionType"]
    saved_item = data.named(saved["inputs"], "fields")
    assert saved_item["value"] == item["value"]
    assert json.loads(saved_item["value"])["enabled"]["value"] is False
    resolver = editing.PillResolver(fake.client, None)
    leaves = {leaf["name"]: leaf["value"] for leaf, _ in resolver._bindings(saved_item)}
    assert leaves["quantity"] == ("{{ui-1.quantity}}" if existing else 0)
    assert leaves["message"] == "Chosen {{ui-1.message}}"
    assert persisted["actionInstances"] == readback["actionInstances"]


@pytest.mark.parametrize(
    ("leaf", "pill", "message"),
    [
        ("quantity", "ui-1.message", "incompatible"),
        ("decision", "ui-1.wide_decision", "contained"),
        ("assignee", "ui-1.ticket", "incompatible"),
    ],
)
async def test_editing_checks_dynamic_pills_against_leaf_type_choice_set_and_reference_table(
    settings: Any,
    leaf: str,
    pill: str,
    message: str,
) -> None:
    fake = provider()
    evidence = await resolved(fake)
    authoring_routes(settings, pill_flow(), fake.definitions[OWNER])
    http_mock.get(f"{BASE}/api/now/table/sys_db_object").respond(
        200,
        json={"result": [{"super_class.name": ""}]},
    )
    with pytest.raises(CapabilityError, match=message):
        await plan(
            settings,
            [
                {
                    "op": "insert_action",
                    "action_type_sys_id": OWNER,
                    "after": "ui-1",
                    "inputs": {
                        "requester": RECORD,
                        "fields": supplied(**{leaf: "{{" + pill + "}}"}),
                    },
                }
            ],
            [evidence],
        )
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("leaf", ["quantity", "enabled"])
async def test_save_echo_does_not_hide_dynamic_value_drift_in_independent_readback(
    settings: Any, leaf: str
) -> None:
    fake = provider()
    evidence = await resolved(fake)
    authoring_routes(settings, pill_flow(), fake.definitions[OWNER])
    request, result = await plan(
        settings,
        [
            {
                "op": "insert_action",
                "action_type_sys_id": OWNER,
                "after": "ui-1",
                "inputs": {"requester": RECORD, "fields": supplied()},
            }
        ],
        [evidence],
    )

    def save(http_request: httpx2.Request) -> httpx2.Response:
        echo = json.loads(http_request.content)
        consumer = next(
            row for row in echo["actionInstances"] if row["actionTypeSysId"] == OWNER
        )
        consumer["id"] = "e" * 32
        changed = copy.deepcopy(echo)
        stored = next(
            row for row in changed["actionInstances"] if row["actionTypeSysId"] == OWNER
        )
        item = data.named(stored["inputs"], "fields")
        packed = json.loads(item["value"])
        packed[leaf]["value"] = "" if leaf == "quantity" else True
        item["value"] = json.dumps(packed, separators=(",", ":"))
        persist(changed)
        return httpx2.Response(200, json={"result": {"data": echo}})

    http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(CapabilityError) as error:
            await editing.save_plan(request, result, client, binding_context=None)
    assert error.value.code == "UNKNOWN_OUTCOME"


async def test_unrelated_edit_still_revalidates_saved_dynamic_schema(
    settings: Any,
) -> None:
    fake = provider()
    evidence = await resolved(fake)
    flow = pill_flow()
    saved = calls.step(
        flow,
        fake.definitions[OWNER],
        {"requester": RECORD, "fields": supplied()},
        subflow=False,
        uid="saved-dynamic",
        schema_evidence=[evidence],
    ).raw
    saved.update(id="e" * 32, parent="", order=2, isNew=False)
    item = data.named(saved["inputs"], "fields")
    packed = json.loads(item["value"])
    packed["decision"]["definition"]["choices"].append(
        {"value": "other", "label": "Other"}
    )
    item["value"] = json.dumps(packed)
    flow["actionInstances"][1] = saved
    authoring_routes(settings, flow, fake.definitions[OWNER])
    with pytest.raises(CapabilityError) as error:
        await plan(
            settings,
            [
                {
                    "op": "update_inputs",
                    "step": "ui-3",
                    "inputs": {"log_message": "Changed"},
                }
            ],
            [evidence],
        )
    assert error.value.code == "STALE_PREVIEW"
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    ("kind", "default", "override"),
    [("string", "Declared", "Override"), ("integer", 0, 1), ("boolean", False, True)],
)
async def test_readonly_dynamic_fields_reject_native_and_opaque_wire_overrides(
    kind: str, default: Any, override: Any
) -> None:
    fake = provider(
        schema=[
            {
                "name": "fixed",
                "label": "Fixed",
                "type": kind,
                "readonly": True,
                "defaultValue": default,
            }
        ]
    )
    evidence = await resolved(fake)
    definition = copy.deepcopy(fake.definitions[OWNER])
    item = binding(definition, evidence, {})
    original = json.loads(item["value"])
    assert original["fixed"]["value"] == (
        str(default) if kind == "integer" else default
    )
    assert binding(definition, evidence, item["value"])["value"] == item["value"]
    for value in (default, override):
        with pytest.raises(CapabilityError):
            binding(definition, evidence, {"fixed": value})
    forged = copy.deepcopy(original)
    forged["fixed"]["value"] = str(override) if kind == "integer" else override
    assert forged["fixed"]["definition"] == original["fixed"]["definition"]
    with pytest.raises(CapabilityError):
        binding(definition, evidence, json.dumps(forged))


@pytest.mark.parametrize(
    ("kind", "expected"), [("string", ""), ("integer", ""), ("boolean", False)]
)
async def test_readonly_null_defaults_normalize_native_values_and_roundtrip(
    kind: str, expected: Any
) -> None:
    fake = provider(
        schema=[
            {
                "name": "fixed",
                "label": "Fixed",
                "type": kind,
                "readonly": True,
                "defaultValue": None,
            }
        ]
    )
    evidence = await resolved(fake)
    definition = copy.deepcopy(fake.definitions[OWNER])
    item = binding(definition, evidence, {})
    packed = json.loads(item["value"])
    assert packed["fixed"]["value"] == expected
    assert type(packed["fixed"]["value"]) is type(expected)
    assert packed["fixed"]["definition"]["defaultValue"] is None
    assert packed["fixed"]["definition"]["readonly"] is True
    readback = binding(definition, evidence, item["value"])
    assert readback["value"] == item["value"]
    leaf = dynamic_inputs.bindings(readback)[0]
    assert leaf["value"] == expected
    assert type(leaf["value"]) is type(expected)


@pytest.mark.parametrize("key", ["label", "defaultValue", "reference_display"])
@pytest.mark.parametrize("expression", ["{{ui-1.message}}", "{{broken", "broken}}"])
def test_dynamic_schema_metadata_never_becomes_a_runtime_pill(
    key: str, expression: str
) -> None:
    schema = {"name": "message", "label": "Message", "type": "string", key: expression}
    with pytest.raises(CapabilityError):
        dynamic_inputs.validate_schema("dynamic_inputs", {"data": [schema]})


async def test_dynamic_template_reference_json_is_retained_but_exposes_a_typed_record_leaf() -> (
    None
):
    fake = provider(
        "dynamic_template",
        [
            {
                "name": "assignee",
                "label": "Assignee",
                "type": "reference",
                "reference": "sys_user",
            }
        ],
    )
    evidence = await resolved(fake)
    native = {
        "table": "sys_user",
        "value": RECORD,
        "sys_id": RECORD,
        "displayValue": "Fixture User",
    }
    encoded = "assignee=" + json.dumps(native, separators=(",", ":"))
    item = binding(fake.definitions[OWNER], evidence, encoded)
    assert item["value"] == encoded
    leaf = dynamic_inputs.bindings(item)[0]
    assert leaf["value"] == RECORD
    assert data.value_type(leaf).kind == "reference"
    assert data.value_type(leaf).reference == "sys_user"


@pytest.mark.parametrize(
    "reference",
    [
        RECORD,
        {"table": "incident", "value": RECORD, "sys_id": RECORD},
        {"table": "sys_user", "value": RECORD, "sys_id": "e" * 32},
        {"table": "sys_user", "value": RECORD},
        {"table": "sys_user", "sys_id": RECORD},
        {"table": "sys_user"},
    ],
)
async def test_dynamic_template_reference_json_cannot_change_table_or_record_identity(
    reference: Any,
) -> None:
    fake = provider(
        "dynamic_template",
        [
            {
                "name": "assignee",
                "label": "Assignee",
                "type": "reference",
                "reference": "sys_user",
            }
        ],
    )
    evidence = await resolved(fake)
    encoded = "assignee=" + (
        reference if isinstance(reference, str) else json.dumps(reference)
    )
    with pytest.raises((CapabilityError, ValueError)):
        binding(fake.definitions[OWNER], evidence, encoded)


async def connection_evidence(
    fake: ProviderClient, overrides: dict[str, str]
) -> dict[str, Any]:
    preview, _ = await dynamic_schemas.preview(
        command(connection_overrides=copy.deepcopy(overrides)), fake.client
    )
    return await dynamic_schemas.apply(preview, fake.client)


def connected_provider() -> ProviderClient:
    fake = provider()
    alias_context(fake)
    extra_child = "9" * 32
    fake.definitions[OWNER]["connection_configurations"][0]["child_aliases"].append(
        {
            "alias_id": extra_child,
            "display_name": "Alternative connection",
            "type": "CONNECTION",
        }
    )
    fake.records[("sys_alias", extra_child)] = {
        **copy.deepcopy(fake.records[("sys_alias", CHILD_ALIAS)]),
        "sys_id": extra_child,
    }
    return fake


def connected_flow(
    fake: ProviderClient,
    evidence: dict[str, Any],
    *,
    scope: str,
    context: dict[str, str],
) -> dict[str, Any]:
    flow = copy.deepcopy(base_flow())
    flow["actionInstances"] = []
    flow["subFlowInstances"] = []
    flow["connection_configurations"] = copy.deepcopy(
        fake.definitions[OWNER]["connection_configurations"]
    )
    saved = calls.step(
        flow,
        copy.deepcopy(fake.definitions[OWNER]),
        {"requester": RECORD, "fields": supplied()},
        subflow=False,
        uid="saved-dynamic",
        schema_evidence=[evidence],
        connection_context=copy.deepcopy(context),
    ).raw
    saved.update(id="a" * 32, parent="", order=1, isNew=False, aliasIds=[SOURCE_ALIAS])
    flow["actionInstances"] = [saved]
    owner = saved if scope == "instance" else flow
    owner["aliasOverride"] = {
        "aliasOverrideMappings": [
            {"id": f"{index:032x}", "sourceAlias": source, "targetAlias": target}
            for index, (source, target) in enumerate(context.items(), start=128)
        ]
    }
    return flow


@pytest.mark.parametrize(
    "context",
    [
        {"": ""},
        {SOURCE_ALIAS: CHILD_ALIAS},
        {OTHER_SOURCE_ALIAS: OTHER_CHILD_ALIAS},
        {"": "", SOURCE_ALIAS: CHILD_ALIAS},
    ],
)
async def test_calls_and_saved_bindings_adopt_only_the_exact_resolved_native_connection_context(
    context: dict[str, str],
) -> None:
    fake = connected_provider()
    evidence = await connection_evidence(fake, context)
    definition = copy.deepcopy(fake.definitions[OWNER])
    before = copy.deepcopy(evidence)
    actual_context = copy.deepcopy(context)
    row = calls.step(
        base_flow(),
        definition,
        {"requester": RECORD, "fields": supplied()},
        subflow=False,
        uid="connected",
        schema_evidence=[evidence],
        connection_context=actual_context,
    )
    packed = json.loads(data.named(row.raw["inputs"], "fields")["value"])
    assert packed["quantity"]["value"] == "0"
    assert packed["enabled"]["value"] is False
    assert row.raw["actionType"] == definition
    updates = dynamic_inputs.adopt_bindings(
        row.raw,
        definition,
        {"fields": supplied(quantity=2)},
        [evidence],
        connection_context=actual_context,
    )
    assert json.loads(updates["fields"])["quantity"]["value"] == "2"
    assert evidence["connection_context"]["effective_overrides"] == context
    assert evidence == before
    assert actual_context == context


@pytest.mark.parametrize(
    ("resolved_context", "actual_context"),
    [
        ({"": ""}, {}),
        ({}, {"": ""}),
        ({SOURCE_ALIAS: CHILD_ALIAS}, {SOURCE_ALIAS: "9" * 32}),
        ({SOURCE_ALIAS: CHILD_ALIAS}, {OTHER_SOURCE_ALIAS: OTHER_CHILD_ALIAS}),
        ({"": "", SOURCE_ALIAS: CHILD_ALIAS}, {SOURCE_ALIAS: CHILD_ALIAS}),
    ],
)
async def test_stale_connection_diagnostics_request_the_actual_context_without_losing_tombstones(
    resolved_context: dict[str, str],
    actual_context: dict[str, str],
) -> None:
    fake = connected_provider()
    evidence = await connection_evidence(fake, resolved_context)
    executions = copy.deepcopy(fake.executions)
    with pytest.raises(CapabilityError) as error:
        calls.step(
            base_flow(),
            fake.definitions[OWNER],
            {"requester": RECORD, "fields": supplied()},
            subflow=False,
            uid="connected",
            schema_evidence=[evidence],
            connection_context=actual_context,
        )
    assert error.value.code == "STALE_PREVIEW"
    assert error.value.outcome == {
        "provider_executed": False,
        "schema_resolution": {
            "action_type_sys_id": OWNER,
            "input_name": "fields",
            "connection_overrides": actual_context,
        },
    }
    assert fake.executions == executions


@pytest.mark.parametrize(
    "context",
    [
        {"": ""},
        {SOURCE_ALIAS: CHILD_ALIAS},
        {"": "", OTHER_SOURCE_ALIAS: OTHER_CHILD_ALIAS},
    ],
)
def test_missing_evidence_diagnostics_retain_actual_safe_connection_ids_and_empty_marker(
    context: dict[str, str],
) -> None:
    fake = connected_provider()
    with pytest.raises(CapabilityError) as error:
        calls.step(
            base_flow(),
            fake.definitions[OWNER],
            {"requester": RECORD, "fields": supplied()},
            subflow=False,
            uid="connected",
            connection_context=copy.deepcopy(context),
        )
    assert error.value.code == "UNAVAILABLE"
    assert error.value.outcome == {
        "provider_executed": False,
        "schema_resolution": {
            "action_type_sys_id": OWNER,
            "input_name": "fields",
            "connection_overrides": context,
        },
    }
    assert fake.executions == []


async def test_editing_retains_instance_tombstone_context_instead_of_falling_back_to_root_connections(
    settings: Any,
) -> None:
    fake = connected_provider()
    marker = {"": ""}
    marker_evidence = await connection_evidence(fake, marker)
    empty_evidence = await connection_evidence(fake, {})
    flow = connected_flow(fake, marker_evidence, scope="instance", context=marker)
    flow["aliasOverride"] = {
        "aliasOverrideMappings": [
            {
                "id": "7" * 32,
                "sourceAlias": SOURCE_ALIAS,
                "targetAlias": CHILD_ALIAS,
            }
        ]
    }
    original = copy.deepcopy(flow)
    authoring_routes(settings, flow, fake.definitions[OWNER])
    edits = [
        {
            "op": "update_inputs",
            "step": "saved-dynamic",
            "inputs": {"fields": supplied(quantity=3)},
        }
    ]
    with pytest.raises(CapabilityError) as error:
        await plan(settings, edits, [empty_evidence])
    assert error.value.code == "STALE_PREVIEW"
    assert error.value.outcome is not None
    resolution = error.value.outcome["schema_resolution"]
    assert isinstance(resolution, dict)
    assert resolution["connection_overrides"] == marker
    assert all(call.request.method == "GET" for call in http_mock.calls)

    _, result = await plan(settings, edits, [marker_evidence])
    saved = result["payload"]["actionInstances"][0]
    assert connection_inputs.effective(result["payload"], saved) == marker
    assert saved["aliasOverride"] == original["actionInstances"][0]["aliasOverride"]
    assert result["payload"]["aliasOverride"] == original["aliasOverride"]
    assert (
        json.loads(data.named(saved["inputs"], "fields")["value"])["quantity"]["value"]
        == "3"
    )
    assert flow == original


@pytest.mark.parametrize("scope", ["flow", "instance"])
@pytest.mark.parametrize(
    "clear", [False, True], ids=["change_child", "clear_saved_mapping"]
)
async def test_final_editing_connection_changes_require_fresh_schema_resolution(
    settings: Any, scope: str, clear: bool
) -> None:
    fake = connected_provider()
    original_context = {SOURCE_ALIAS: CHILD_ALIAS}
    initial = await connection_evidence(fake, original_context)
    actual_context = (
        ({"": ""} if scope == "instance" else {}) if clear else {SOURCE_ALIAS: "9" * 32}
    )
    current = await connection_evidence(fake, actual_context)
    flow = connected_flow(fake, initial, scope=scope, context=original_context)
    original = copy.deepcopy(flow)
    authoring_routes(settings, flow, fake.definitions[OWNER])
    changes = {SOURCE_ALIAS: None if clear else "9" * 32}
    edit = (
        {"op": "set_connection_overrides", "connection_overrides": changes}
        if scope == "flow"
        else {
            "op": "update_inputs",
            "step": "saved-dynamic",
            "inputs": {},
            "connection_overrides": changes,
        }
    )
    with pytest.raises(CapabilityError) as error:
        await plan(settings, [edit], [initial])
    assert error.value.code == "STALE_PREVIEW"
    assert error.value.outcome is not None
    resolution = error.value.outcome["schema_resolution"]
    assert isinstance(resolution, dict)
    assert resolution["connection_overrides"] == actual_context
    assert all(call.request.method == "GET" for call in http_mock.calls)

    _, result = await plan(settings, [edit], [current])
    payload = result["payload"]
    saved = payload["actionInstances"][0]
    assert connection_inputs.effective(payload, saved) == actual_context
    mappings = (saved if scope == "instance" else payload)["aliasOverride"][
        "aliasOverrideMappings"
    ]
    original_mapping = (flow["actionInstances"][0] if scope == "instance" else flow)[
        "aliasOverride"
    ]["aliasOverrideMappings"][0]
    assert mappings[0]["id"] == original_mapping["id"]
    assert mappings[0]["sourceAlias"] == ("" if clear else SOURCE_ALIAS)
    assert mappings[0]["targetAlias"] == ("" if clear else "9" * 32)
    assert (
        json.loads(data.named(saved["inputs"], "fields")["value"])["enabled"]["value"]
        is False
    )
    assert saved["actionType"] == fake.definitions[OWNER]
    assert flow == original


@pytest.mark.parametrize("scope", ["flow", "instance"])
async def test_insert_action_adopts_the_prospective_root_or_call_connection_context(
    settings: Any, scope: str
) -> None:
    fake = connected_provider()
    context = {SOURCE_ALIAS: CHILD_ALIAS}
    empty = await connection_evidence(fake, {})
    evidence = await connection_evidence(fake, context)
    flow = copy.deepcopy(base_flow())
    flow["actionInstances"] = []
    flow["subFlowInstances"] = []
    flow["connection_configurations"] = copy.deepcopy(
        fake.definitions[OWNER]["connection_configurations"]
    )
    edit = {
        "op": "insert_action",
        "action_type_sys_id": OWNER,
        "inputs": {"requester": RECORD, "fields": supplied()},
    }
    if scope == "flow":
        flow["aliasOverride"] = {
            "aliasOverrideMappings": [
                {
                    "id": "7" * 32,
                    "sourceAlias": SOURCE_ALIAS,
                    "targetAlias": CHILD_ALIAS,
                }
            ]
        }
    else:
        edit["connection_overrides"] = context
    original = copy.deepcopy(flow)
    authoring_routes(settings, flow, fake.definitions[OWNER])
    with pytest.raises(CapabilityError) as error:
        await plan(settings, [edit], [empty])
    assert error.value.code == "STALE_PREVIEW"
    assert error.value.outcome is not None
    resolution = error.value.outcome["schema_resolution"]
    assert isinstance(resolution, dict)
    assert resolution["connection_overrides"] == context
    assert all(call.request.method == "GET" for call in http_mock.calls)

    _, result = await plan(settings, [edit], [evidence])
    saved = result["payload"]["actionInstances"][0]
    assert connection_inputs.effective(result["payload"], saved) == context
    assert saved["actionType"]["inputs"] == fake.definitions[OWNER]["inputs"]
    assert (
        json.loads(data.named(saved["inputs"], "fields")["value"])["quantity"]["value"]
        == "0"
    )
    assert flow == original


async def scripted_template_fixture() -> tuple[
    ProviderClient, dict[str, Any], dict[str, Any]
]:
    fake = provider(
        "dynamic_template",
        [
            {"name": "message", "label": "Message", "type": "string"},
            {"name": "quantity", "label": "Quantity", "type": "integer"},
            {"name": "enabled", "label": "Enabled", "type": "boolean"},
        ],
    )
    evidence = await resolved(fake)
    row = calls.step(
        base_flow(),
        copy.deepcopy(fake.definitions[OWNER]),
        {"requester": RECORD, "fields": "message=original^quantity=0^enabled=false"},
        subflow=False,
        uid="scripted-template",
        schema_evidence=[evidence],
    ).raw
    item = data.named(row["inputs"], "fields")
    item.update(
        value="message=fd-scripted^quantity=0^enabled=false",
        scriptActive=True,
        script={
            "message": {
                "scriptActive": True,
                "showScript": True,
                "script": "return 'generated';",
                "savedValue": "original",
            }
        },
        savedValue="message=original^quantity=0^enabled=false",
        opaque_template={"retain": True},
    )
    return fake, evidence, row


async def test_saved_scripted_dynamic_template_retains_native_script_state_and_refreshes_provider_columns() -> (
    None
):
    fake, _, row = await scripted_template_fixture()
    item = data.named(row["inputs"], "fields")
    script = copy.deepcopy(item["script"])
    item["scriptAsJsonString"] = json.dumps(script, ensure_ascii=False)
    script_mirror = item["scriptAsJsonString"]
    saved_value = item["savedValue"]
    original_definition = copy.deepcopy(row["actionType"])
    fake.output["data"][0]["label"] = "Resolved Message"
    evidence = await resolved(fake)
    updates = dynamic_inputs.adopt_bindings(
        row, fake.definitions[OWNER], {}, [evidence]
    )
    assert updates["fields"] == "message=fd-scripted^quantity=0^enabled=false"
    assert item["value"] == updates["fields"]
    assert item["scriptActive"] is True
    assert item["script"] == script
    assert item["savedValue"] == saved_value
    assert item["scriptAsJsonString"] == script_mirror
    assert item["opaque_template"] == {"retain": True}
    columns = {column["name"]: column for column in item["columns"]}
    assert columns["message"]["label"] == "Resolved Message"
    assert item["parameter"]["columns"] == item["columns"]
    assert row["actionType"] == original_definition
    resolver = editing.PillResolver(fake.client, None)
    leaves = {
        leaf["name"]: (leaf["value"], data.value_type(parameter).kind)
        for leaf, parameter in resolver._bindings(item)
    }
    assert leaves == {"quantity": ("0", "integer"), "enabled": (False, "boolean")}


@pytest.mark.parametrize(
    "defect",
    [
        "missing_parent_flag",
        "false_parent_flag",
        "string_parent_flag",
        "missing_script_map",
        "whole_input_script",
        "missing_column",
        "wrong_column",
        "inactive_marker",
        "string_column_flag",
        "missing_marker",
        "missing_saved_value",
        "marker_saved_value",
        "numeric_saved_value",
        "malformed_script_mirror",
        "changed_script_mirror",
    ],
)
async def test_scripted_dynamic_templates_reject_inconsistent_parent_marker_and_column_script_maps(
    defect: str,
) -> None:
    fake, evidence, row = await scripted_template_fixture()
    item = data.named(row["inputs"], "fields")
    if defect == "missing_parent_flag":
        item.pop("scriptActive")
    elif defect == "false_parent_flag":
        item["scriptActive"] = False
    elif defect == "string_parent_flag":
        item["scriptActive"] = "true"
    elif defect == "missing_script_map":
        item.pop("script")
    elif defect == "whole_input_script":
        item["script"] = "return 'whole template';"
    elif defect == "missing_column":
        item["script"] = {}
    elif defect == "wrong_column":
        item["script"] = {"quantity": item["script"]["message"]}
    elif defect == "inactive_marker":
        item["script"]["message"]["scriptActive"] = False
    elif defect == "string_column_flag":
        item["script"]["message"]["scriptActive"] = "true"
    elif defect == "missing_saved_value":
        item["script"]["message"].pop("savedValue")
    elif defect == "marker_saved_value":
        item["script"]["message"]["savedValue"] = "fd-scripted"
    elif defect == "numeric_saved_value":
        item["script"]["message"]["savedValue"] = 0
    elif defect == "malformed_script_mirror":
        item["scriptAsJsonString"] = "{broken"
    elif defect == "changed_script_mirror":
        mirror = copy.deepcopy(item["script"])
        mirror["message"]["script"] = "return 'different';"
        item["scriptAsJsonString"] = json.dumps(mirror)
    else:
        item["value"] = "message=original^quantity=0^enabled=false"
    with pytest.raises(CapabilityError):
        dynamic_inputs.bindings(item)
    with pytest.raises(CapabilityError):
        dynamic_inputs.adopt_bindings(row, fake.definitions[OWNER], {}, [evidence])


async def test_scripted_dynamic_template_cannot_assign_a_fresh_readonly_provider_column() -> (
    None
):
    fake, _, row = await scripted_template_fixture()
    fake.output["data"][0]["read_only"] = True
    current = await resolved(fake)
    with pytest.raises(CapabilityError):
        dynamic_inputs.adopt_bindings(row, fake.definitions[OWNER], {}, [current])


def text_dependency_provider() -> ProviderClient:
    fake = provider()
    for parameter in (
        fake.definitions[OWNER]["inputs"][0],
        fake.definitions[PROVIDER]["inputs"][0],
        fake.descriptor["inputs"][0],
    ):
        parameter["type"] = "string"
        parameter.pop("reference", None)
    return fake


def saved_dependency_flow(
    fake: ProviderClient, evidence: dict[str, Any]
) -> dict[str, Any]:
    flow = pill_flow()
    saved = calls.step(
        flow,
        copy.deepcopy(fake.definitions[OWNER]),
        {"requester": RECORD, "fields": supplied()},
        subflow=False,
        uid="saved-dynamic",
        schema_evidence=[evidence],
    ).raw
    saved.update(id="e" * 32, parent="", order=2, isNew=False)
    flow["actionInstances"][1] = saved
    return flow


@pytest.mark.parametrize("existing", [False, True], ids=["insert", "update"])
async def test_static_schema_evidence_does_not_authorize_linked_input_scripts(
    settings: Any, existing: bool
) -> None:
    fake = provider()
    evidence = await resolved(fake)
    code = "return '" + RECORD + "';"
    if existing:
        flow = saved_dependency_flow(fake, evidence)
        edit = {
            "op": "update_inputs",
            "step": "saved-dynamic",
            "inputs": {},
            "input_scripts": {"requester": code},
        }
    else:
        flow = pill_flow()
        edit = {
            "op": "insert_action",
            "action_type_sys_id": OWNER,
            "after": "ui-1",
            "inputs": {"requester": RECORD, "fields": supplied()},
            "input_scripts": {"requester": code},
        }
    original = copy.deepcopy(flow)
    authoring_routes(settings, flow, fake.definitions[OWNER])
    with pytest.raises(CapabilityError) as error:
        await plan(settings, [edit], [evidence])
    assert error.value.code == "STALE_PREVIEW"
    assert error.value.outcome is not None
    assert error.value.outcome["provider_executed"] is False
    assert all(call.request.method == "GET" for call in http_mock.calls)
    assert flow == original


@pytest.mark.parametrize("existing", [False, True], ids=["insert", "update"])
async def test_unlinked_scripted_input_is_not_treated_as_a_computed_provider_dependency(
    settings: Any, existing: bool
) -> None:
    fake = provider()
    fake.definitions[OWNER]["inputs"].append(
        {"name": "counter", "label": "Counter", "type": "integer", "defaultValue": 0}
    )
    evidence = await resolved(fake)
    assert evidence["plan"]["inputs"]["counter"] == 0
    assert "counter" not in evidence["sibling_inputs"]
    code = "return 7;"
    if existing:
        flow = saved_dependency_flow(fake, evidence)
        edit = {
            "op": "update_inputs",
            "step": "saved-dynamic",
            "inputs": {},
            "input_scripts": {"counter": code},
        }
    else:
        flow = pill_flow()
        edit = {
            "op": "insert_action",
            "action_type_sys_id": OWNER,
            "after": "ui-1",
            "inputs": {"requester": RECORD, "fields": supplied()},
            "input_scripts": {"counter": code},
        }
    authoring_routes(settings, flow, fake.definitions[OWNER])
    _, result = await plan(settings, [edit], [evidence])
    saved = next(
        row
        for row in result["payload"]["actionInstances"]
        if row["actionTypeSysId"] == OWNER
    )
    counter = data.named(saved["inputs"], "counter")
    assert counter["value"] == 0
    assert counter["scriptActive"] is True
    assert counter["script"]["counter"]["script"] == code
    assert (
        json.loads(data.named(saved["inputs"], "fields")["value"])["quantity"]["value"]
        == "0"
    )
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("existing", [False, True], ids=["insert", "update"])
async def test_linked_input_transform_options_invalidate_static_schema_evidence(
    settings: Any, existing: bool
) -> None:
    fake = text_dependency_provider()
    evidence = await resolved(fake)
    option = {
        "requester": {
            "source": "{{ui-1.message}}",
            "transforms": [{"transform_sys_id": TRANSFORM}],
        }
    }
    if existing:
        flow = saved_dependency_flow(fake, evidence)
        edit = {
            "op": "update_inputs",
            "step": "saved-dynamic",
            "inputs": {},
            "input_transforms": option,
        }
    else:
        flow = pill_flow()
        edit = {
            "op": "insert_action",
            "action_type_sys_id": OWNER,
            "after": "ui-1",
            "inputs": {"fields": supplied()},
            "input_transforms": option,
        }
    authoring_routes(settings, flow, fake.definitions[OWNER])
    http_mock.get(f"{BASE}/api/now/transformdefinitions").respond(
        200,
        json={"result": {"data": [transform_definition()]}},
    )
    with pytest.raises(CapabilityError) as error:
        await plan(settings, [edit], [evidence])
    assert error.value.code == "STALE_PREVIEW"
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_native_transform_binding_cannot_be_hidden_by_restoring_the_static_dependency_value(
    settings: Any,
) -> None:
    fake = text_dependency_provider()
    evidence = await resolved(fake)
    flow = saved_dependency_flow(fake, evidence)
    authoring_routes(settings, flow, fake.definitions[OWNER])
    http_mock.get(f"{BASE}/api/now/transformdefinitions").respond(
        200,
        json={"result": {"data": [transform_definition()]}},
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        roots = tree.build_tree(flow)
        consumer = next(
            step for step in tree.walk(roots) if step.uid == "saved-dynamic"
        )
        resolver = editing.PillResolver(client, None, flow, roots)
        await input_transforms.apply(
            flow,
            consumer,
            {
                "requester": {
                    "source": "{{ui-1.message}}",
                    "transforms": [{"transform_sys_id": TRANSFORM}],
                },
            },
            resolver,
            seed="dynamic-provider-dependency",
        )
    requester = data.named(consumer.raw["inputs"], "requester")
    assert requester["value"].startswith("{{fd_transform:")
    with pytest.raises(CapabilityError) as error:
        dynamic_inputs.adopt_bindings(
            consumer.raw, fake.definitions[OWNER], {"requester": RECORD}, [evidence]
        )
    assert error.value.code == "STALE_PREVIEW"
    assert error.value.outcome is not None
    assert error.value.outcome["provider_executed"] is False
    assert all(call.request.method == "GET" for call in http_mock.calls)
