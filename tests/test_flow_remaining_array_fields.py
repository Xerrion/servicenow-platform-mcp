"""Source-derived remaining Array declaration contracts; no live save fixtures."""

import copy

import pytest

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import data, declarations, fields
from tests._mock_transport import http_mock
from tests.test_flow_data_edits import typed_flow
from tests.test_flow_editing import fixtures
from tests.test_flow_fields import plan
from tests.test_flow_output_assignments import routes

CHOICES = [
    {"label": "Open", "value": "open"},
    {"label": "Closed", "value": "closed"},
]
REPLACEMENT = [
    {"label": "Queued", "value": "queued"},
    {"label": "Finished", "value": "finished"},
]


def subflow():
    flow = copy.deepcopy(typed_flow())
    flow["type"] = "subflow"
    return flow


def create(name, kind, collection="inputs", *, parent=None, **settings):
    return {
        "op": "create_field",
        "collection": collection,
        "name": name,
        "label": name.title(),
        "type": kind,
        **({"parent": parent} if parent else {}),
        **(
            {"choices": copy.deepcopy(CHOICES)}
            if kind in {"choice", "array.choice"}
            else {}
        ),
        **copy.deepcopy(settings),
    }


def array_row(flow, collection, nested):
    root = flow[collection][0]
    return root["children"][0] if nested else root


@pytest.mark.parametrize("collection", ["inputs", "outputs"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize(
    "kind,item_type,item_label",
    [
        ("array.choice", "choice", "Choice"),
        ("array.datetime", "glide_date_time", "Date/Time"),
        ("array.array", "array", "Array"),
    ],
)
async def test_remaining_array_declarations_pack_and_edit_native_items(
    settings, collection, nested, kind, item_type, item_label
):
    flow = subflow()
    fixtures(settings, flow)
    edits = [create("payload", "object", collection)] if nested else []
    edits.append(
        create("items", kind, collection, parent="payload" if nested else None)
    )
    saved = copy.deepcopy((await plan(settings, flow, edits))["payload"])
    row = array_row(saved, collection, nested)
    storage = "fieldFacetMap" if nested else "attributes"
    assert row[storage]["child_type"] == item_type
    assert row[storage]["child_type_label"] == item_label
    assert row[storage]["child_name"] == "items_child0"
    assert row[storage]["child_uiUniqueId"] != row["uiUniqueId"]
    assert row["children"] == []
    child = data.children(row)[0]
    assert child["type"] == item_type
    assert child["type_label"] == item_label
    assert child["cannotBeDeleted"] is True
    assert declarations.pack(declarations.unpack(row)) == row
    if kind == "array.choice":
        assert row["choices"] == child["choices"] == CHOICES
        assert not {"defaultValue", "defaultChoices", "choiceOption"} & row.keys()
        described = fields.summary(saved)[collection][0]
        if nested:
            described = described["children"][0]
        assert described["choices"] == described["children"][0]["choices"] == CHOICES

    fixtures(settings, saved)
    changed = await plan(
        settings,
        saved,
        [
            {
                "op": "update_field",
                "collection": collection,
                "name": row["name"] + ".items_child0",
                "hint": "Native item schema",
            }
        ],
    )
    updated = array_row(changed["payload"], collection, nested)
    assert updated[storage] == {**row[storage], "child_hint": "Native item schema"}
    assert declarations.pack(declarations.unpack(updated)) == updated


@pytest.mark.parametrize("collection", ["inputs", "outputs"])
@pytest.mark.parametrize("nested", [False, True])
async def test_scalar_choice_and_datetime_interface_declarations(
    settings, collection, nested
):
    flow = subflow()
    fixtures(settings, flow)
    edits = [create("payload", "object", collection)] if nested else []
    edits.extend(
        [
            create(
                "status",
                "choice",
                collection,
                parent="payload" if nested else None,
                default_value="open",
            ),
            create(
                "timestamp",
                "glide_date_time",
                collection,
                parent="payload" if nested else None,
            ),
        ]
    )
    saved = copy.deepcopy((await plan(settings, flow, edits))["payload"])
    rows = saved[collection][0]["children"] if nested else saved[collection]
    choice, timestamp = rows
    assert choice["type"] == "choice" and choice["type_label"] == "Choice"
    assert choice["choices"] == CHOICES and choice["defaultValue"] == "open"
    assert timestamp["type"] == "glide_date_time"
    assert timestamp["type_label"] == "Date/Time"
    assert not choice.get("children") and not timestamp.get("children")
    assert "choice" not in fields.TYPES and "glide_date_time" in fields.TYPES
    described = fields.summary(saved)[collection]
    if nested:
        described = described[0]["children"]
    assert described[0]["choices"] == CHOICES
    fixtures(settings, saved)
    renamed = await plan(
        settings,
        saved,
        [
            {
                "op": "update_field",
                "collection": collection,
                "name": choice["name"],
                "label": "Current status",
            }
        ],
    )
    changed_rows = renamed["payload"][collection]
    if nested:
        changed_rows = changed_rows[0]["children"]
    assert changed_rows[0] == {**choice, "label": "Current status"}
    assert changed_rows[1] == timestamp


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("target", ["parent", "item"])
async def test_choice_item_choices_and_defaults_survive_authoritative_parent_packing(
    settings, nested, target
):
    flow = subflow()
    fixtures(settings, flow)
    edits = [create("payload", "object")] if nested else []
    edits.append(
        create(
            "items",
            "array.choice",
            parent="payload" if nested else None,
            default_value="open",
        )
    )
    saved = copy.deepcopy((await plan(settings, flow, edits))["payload"])
    original = array_row(saved, "inputs", nested)
    original["defaultChoices"] = [
        {**copy.deepcopy(choice), "order": position}
        for position, choice in enumerate(CHOICES)
    ]
    original["choiceOption"] = "3"
    original["choiceType"] = "1"
    original["vendor"] = {"keep": [False, 0, "opaque"]}
    before = copy.deepcopy(original)
    name = original["name"] + (".items_child0" if target == "item" else "")
    fixtures(settings, saved)
    changed = await plan(
        settings,
        saved,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": name,
                "choices": copy.deepcopy(REPLACEMENT),
                "default_value": "queued",
            }
        ],
    )
    updated = array_row(changed["payload"], "inputs", nested)
    storage = "fieldFacetMap" if nested else "attributes"
    assert updated["choices"] == REPLACEMENT
    assert updated["defaultValue"] == updated[storage]["child_defaultValue"] == "queued"
    assert updated["defaultChoices"] == before["defaultChoices"]
    assert updated["choiceOption"] == "3" and updated["choiceType"] == "1"
    assert updated["vendor"] == before["vendor"]
    assert updated[storage]["child_uiUniqueId"] == before[storage]["child_uiUniqueId"]
    child = declarations.unpack(updated)["children"][0]
    for key in declarations.CHOICE_FIELDS:
        assert child.get(key) == updated.get(key)
    assert declarations.pack(declarations.unpack(updated)) == updated
    assert saved["inputs"] != changed["payload"]["inputs"]
    assert original == before

    saved_again = copy.deepcopy(changed["payload"])
    fixtures(settings, saved_again)
    reloaded = await plan(
        settings,
        saved_again,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": updated["name"],
                "hint": "Retained",
            }
        ],
    )
    retained = array_row(reloaded["payload"], "inputs", nested)
    for key in declarations.CHOICE_FIELDS:
        assert retained.get(key) == updated.get(key)


@pytest.mark.parametrize("kind", ["choice", "array.choice"])
@pytest.mark.parametrize("operation", ["create_field", "update_field"])
@pytest.mark.parametrize(
    "choices",
    [
        None,
        [],
        {"label": "Open", "value": "open"},
        ["open"],
        [{"value": "open"}],
        [{"label": "Open"}],
        [{"label": "", "value": "open"}],
        [{"label": "Open", "value": True}],
        [{"label": "Open", "value": 1}],
        [{"label": "First", "value": "open"}, {"label": "Second", "value": "open"}],
    ],
)
async def test_explicit_choices_reject_malformed_or_duplicate_lists(
    settings, kind, operation, choices
):
    flow = subflow()
    fixtures(settings, flow)
    if operation == "create_field":
        edit = create("items", kind, choices=copy.deepcopy(choices))
    else:
        flow = copy.deepcopy(
            (await plan(settings, flow, [create("items", kind)]))["payload"]
        )
        fixtures(settings, flow)
        edit = {
            "op": "update_field",
            "collection": "inputs",
            "name": "items",
            "choices": copy.deepcopy(choices),
        }
    with pytest.raises(CapabilityError):
        await plan(settings, flow, [edit])
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("kind", ["choice", "array.choice"])
@pytest.mark.parametrize(
    "metadata",
    [
        {"choiceType": "2"},
        {"choiceOption": "dynamic"},
        {"dynamic": {"provider": "unresolved"}},
        {"table": "task"},
        {"columnName": "state"},
        {"dependent_on": "scope"},
    ],
)
async def test_existing_dynamic_choice_metadata_cannot_be_replaced_by_static_options(
    settings, kind, metadata
):
    flow = subflow()
    fixtures(settings, flow)
    saved = copy.deepcopy(
        (await plan(settings, flow, [create("items", kind)]))["payload"]
    )
    saved["inputs"][0].update(copy.deepcopy(metadata))
    before = copy.deepcopy(saved)
    fixtures(settings, saved)
    with pytest.raises(CapabilityError) as error:
        await plan(
            settings,
            saved,
            [
                {
                    "op": "update_field",
                    "collection": "inputs",
                    "name": "items",
                    "choices": copy.deepcopy(REPLACEMENT),
                }
            ],
        )
    assert error.value.code == "UNAVAILABLE"
    assert saved == before
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("target", ["items", "items.items_child0"])
async def test_choice_set_changes_do_not_infer_replacement_defaults(settings, target):
    flow = subflow()
    fixtures(settings, flow)
    saved = copy.deepcopy(
        (
            await plan(
                settings, flow, [create("items", "array.choice", default_value="open")]
            )
        )["payload"]
    )
    fixtures(settings, saved)
    with pytest.raises(CapabilityError, match="allowed choice"):
        await plan(
            settings,
            saved,
            [
                {
                    "op": "update_field",
                    "collection": "inputs",
                    "name": target,
                    "choices": copy.deepcopy(REPLACEMENT),
                }
            ],
        )


@pytest.mark.parametrize("collection", ["inputs", "outputs"])
@pytest.mark.parametrize(
    "kind,item", [("choice", False), ("array.choice", False), ("array.choice", True)]
)
async def test_choice_changes_require_removing_consumers(
    settings, collection, kind, item
):
    flow = subflow()
    routes(settings, flow)
    edits = [
        create("source", kind),
        create("target", kind, "outputs"),
        {
            "op": "assign_outputs",
            "outputs": {"target": "{{subflow.source}}"},
            "ref": "return",
        },
    ]
    name = "source" if collection == "inputs" else "target"
    if item:
        name += "." + name + "_child0"
    change = {
        "op": "update_field",
        "collection": collection,
        "name": name,
        "choices": copy.deepcopy(REPLACEMENT),
    }
    with pytest.raises(CapabilityError, match="in use"):
        await plan(settings, flow, copy.deepcopy(edits + [change]))
    allowed = await plan(
        settings,
        flow,
        copy.deepcopy(edits)
        + [
            {"op": "clear_output_assignment", "step": "return", "name": "target"},
            copy.deepcopy(change),
        ],
    )
    assert allowed["payload"][collection][0]["choices"] == REPLACEMENT
    assert allowed["payload"]["flowLogicInstances"][0]["outputsToAssign"] == []
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "kind", ["choice", "array.choice", "array.datetime", "array.array"]
)
async def test_remaining_interface_types_are_not_flow_variable_types(settings, kind):
    flow = subflow()
    fixtures(settings, flow)
    with pytest.raises(CapabilityError) as error:
        await plan(settings, flow, [create("items", kind, "flowVariables")])
    assert error.value.code == "UNAVAILABLE"


@pytest.mark.parametrize("collection", ["inputs", "outputs"])
async def test_generic_array_is_only_a_fixed_synthetic_item(settings, collection):
    flow = subflow()
    fixtures(settings, flow)
    with pytest.raises(CapabilityError) as error:
        await plan(settings, flow, [create("items", "array", collection)])
    assert error.value.code == "UNAVAILABLE"
    saved = copy.deepcopy(
        (await plan(settings, flow, [create("items", "array.array", collection)]))[
            "payload"
        ]
    )
    fixtures(settings, saved)
    for update in (
        {"op": "delete_field"},
        {"op": "update_field", "type": "string"},
        {"op": "update_field", "default_value": [["not a native declaration default"]]},
    ):
        with pytest.raises(CapabilityError):
            await plan(
                settings,
                saved,
                [
                    {
                        **copy.deepcopy(update),
                        "collection": collection,
                        "name": "items.items_child0",
                    }
                ],
            )


@pytest.mark.parametrize("collection", ["inputs", "outputs", "flowVariables"])
async def test_non_choice_declarations_do_not_accept_choices(settings, collection):
    flow = subflow()
    fixtures(settings, flow)
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [create("items", "string", collection, choices=copy.deepcopy(CHOICES))],
        )
