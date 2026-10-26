"""Source-derived table Choice declaration consumers; no authored flow execution."""

import copy
from typing import Any

import httpx2
import pytest

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import declarations
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_editing import BASE, FLOW, base_flow, fixtures
from tests.test_flow_fields import plan

SOURCE = {"table": "incident", "field": "state"}
STATIC = [{"label": "Queued", "value": "queued"}, {"label": "Done", "value": "done"}]


def revision(identity: int, **values: Any) -> dict[str, Any]:
    return {
        "sys_id": f"{identity:032x}",
        "sys_mod_count": "2",
        "sys_updated_on": "2026-10-01 12:00:00",
        **values,
    }


def native_choice(value: str, label: str, owner: str = "task") -> dict[str, Any]:
    return {
        "value": value,
        "label": label,
        "rawLabel": label,
        "used": False,
        "selected": False,
        "missing": False,
        "image": "",
        "reference": False,
        "parameters": {"name": owner, "dependent_values": [""]},
        "vendor": {"preserve": [False, 0, "native"]},
    }


def metadata() -> dict[str, Any]:
    inherited = [native_choice("1", "New"), native_choice("2", "In progress")]
    priorities = [native_choice("1", "Critical"), native_choice("2", "High")]
    tables = [
        revision(
            1, name="incident", super_class=f"{2:032x}", **{"super_class.name": "task"}
        ),
        revision(2, name="task", super_class="", **{"super_class.name": ""}),
        revision(
            3,
            name="change_request",
            super_class=f"{2:032x}",
            **{"super_class.name": "task"},
        ),
    ]
    dictionary = [
        revision(
            10 + index,
            name="task",
            element=field,
            internal_type="integer",
            choice="3",
            dependent="",
            choice_table="",
            choice_field="",
            reference_qual="",
            reference_qual_condition="",
            dynamic_ref_qual="",
            attributes="",
        )
        for index, field in enumerate(("state", "priority"))
    ]
    choices = [
        revision(
            20 + index * 10 + order,
            name="task",
            element=field,
            value=choice["value"],
            label=choice["label"],
            language="en",
            inactive="false",
            dependent_value="",
            sequence=str(order),
        )
        for index, (field, options) in enumerate(
            (("state", inherited), ("priority", priorities))
        )
        for order, choice in enumerate(options)
    ]
    return {
        "tables": tables,
        "dictionary": dictionary,
        "choices": choices,
        "schema": {
            "columns": [
                {
                    "name": field,
                    "label": field.title(),
                    "definition": {
                        "name": field,
                        "label": field.title(),
                        "type": "choice",
                        "choices": copy.deepcopy(options),
                        "vendor": {"native": True},
                    },
                }
                for field, options in (("state", inherited), ("priority", priorities))
            ],
        },
    }


def source_routes(observed):
    def schema(request):
        assert request.method == "GET"
        assert request.url.params["get_choices"] == "true"
        return httpx2.Response(
            200, json={"result": {"data": copy.deepcopy(observed["schema"])}}
        )

    for table in ("incident", "task", "change_request"):
        http_mock.get(f"{BASE}/api/now/processflow/table/{table}/schema").mock(
            side_effect=schema
        )

    def records(request):
        table = request.url.path.rsplit("/", 1)[1]
        query = request.url.params["sysparm_query"]
        if table == "sys_db_object":
            rows = [row for row in observed["tables"] if query == "name=" + row["name"]]
        else:
            owners = query.split("^", 1)[0].removeprefix("nameIN").split(",")
            field = query.split("^element=", 1)[1].split("^", 1)[0]
            key = "dictionary" if table == "sys_dictionary" else "choices"
            rows = [
                row
                for row in observed[key]
                if row["name"] in owners and row["element"] == field
            ]
        return httpx2.Response(200, json={"result": copy.deepcopy(rows)})

    for table in ("sys_db_object", "sys_dictionary", "sys_choice"):
        http_mock.get(f"{BASE}/api/now/table/{table}").mock(side_effect=records)


def subflow():
    flow = base_flow()
    flow.update(type="subflow", actionInstances=[], subFlowInstances=[])
    return flow


def create(kind="choice", collection="inputs", **settings):
    return {
        "op": "create_field",
        "collection": collection,
        "name": "status",
        "label": "Status",
        "type": kind,
        "choice_source": copy.deepcopy(SOURCE),
        "choice_option": "1",
        **copy.deepcopy(settings),
    }


def declare(flow, collection="inputs", nested=False):
    row = flow[collection][0]
    return row["children"][0] if nested else row


@pytest.mark.parametrize("kind", ["choice", "array.choice"])
@pytest.mark.parametrize("collection", ["inputs", "outputs"])
@pytest.mark.parametrize("nested", [False, True])
async def test_table_choice_create_preserves_native_picker_options_and_array_registration(
    settings, kind, collection, nested
):
    flow, observed = subflow(), metadata()
    fixtures(settings, flow)
    source_routes(observed)
    edits = []
    if nested:
        edits.append(
            {
                "op": "create_field",
                "collection": collection,
                "name": "payload",
                "label": "Payload",
                "type": "object",
            }
        )
    edits.append(
        create(
            kind,
            collection,
            default_value="2",
            **({"parent": "payload"} if nested else {}),
        )
    )
    planned = await plan(settings, flow, edits)
    row = declare(planned["payload"], collection, nested)
    expected = observed["schema"]["columns"][0]["definition"]["choices"]
    assert row["choiceType"] == "2"
    assert (row["table"], row["columnName"]) == ("incident", "state")
    assert row["choices"] == []
    assert row["defaultChoices"] == expected
    assert row["defaultValue"] == "2" and row["choiceOption"] == "1"
    assert declarations.choices(row) == expected
    if kind == "array.choice":
        storage = "fieldFacetMap" if nested else "attributes"
        assert row[storage]["child_defaultValue"] == "2"
        item = declarations.unpack(row)["children"][0]
        assert item["type"] == "choice" and item["cannotBeDeleted"] is True
        for key in declarations.CHOICE_FIELDS:
            assert item[key] == row[key]
        assert declarations.pack(declarations.unpack(row)) == row
    assert all(call.request.method == "GET" for call in http_mock.calls)
    assert not any(
        "action_execution" in str(call.request.url) for call in http_mock.calls
    )


@pytest.mark.parametrize(
    "kind,target",
    [("choice", "root"), ("array.choice", "root"), ("array.choice", "item")],
)
async def test_existing_table_choice_label_and_default_edits_refresh_native_metadata_without_flattening(
    settings, kind, target
):
    flow, observed = subflow(), metadata()
    fixtures(settings, flow)
    source_routes(observed)
    saved = copy.deepcopy(
        (await plan(settings, flow, [create(kind, default_value="1")]))["payload"]
    )
    saved["inputs"][0]["vendor"] = {"preserve": [False, 0]}
    fixtures(settings, saved)
    source_routes(observed)
    name = "status.status_child0" if target == "item" else "status"
    changed = await plan(
        settings,
        saved,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": name,
                "label": "Current status",
                "default_value": "2",
            }
        ],
    )
    row = changed["payload"]["inputs"][0]
    assert row["defaultValue"] == "2" and row["choices"] == []
    assert row["table"] == "incident" and row["columnName"] == "state"
    assert (
        row["defaultChoices"]
        == observed["schema"]["columns"][0]["definition"]["choices"]
    )
    assert row["vendor"] == saved["inputs"][0]["vendor"]
    if kind == "array.choice":
        assert row["attributes"]["child_defaultValue"] == "2"
        assert (
            row["attributes"]["child_uiUniqueId"]
            == saved["inputs"][0]["attributes"]["child_uiUniqueId"]
        )
    fixtures(settings, changed["payload"])
    source_routes(observed)
    retained = await plan(
        settings,
        changed["payload"],
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "status",
                "hint": "Retain default",
            }
        ],
    )
    assert retained["payload"]["inputs"][0]["defaultValue"] == "2"


@pytest.mark.parametrize("kind", ["choice", "array.choice"])
@pytest.mark.parametrize(
    "value", [None, "unknown", "New", 1, True, ["1"], {"value": "1"}]
)
async def test_table_choice_defaults_use_native_values_and_strict_string_types(
    settings, kind, value
):
    flow, observed = subflow(), metadata()
    fixtures(settings, flow)
    source_routes(observed)
    with pytest.raises(CapabilityError):
        await plan(settings, flow, [create(kind, default_value=copy.deepcopy(value))])
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("kind", ["choice", "array.choice"])
@pytest.mark.parametrize("switch", ["field", "table", "static"])
async def test_choice_source_switch_requires_explicit_default_and_preserves_requested_native_mode(
    settings, kind, switch
):
    flow, observed = subflow(), metadata()
    fixtures(settings, flow)
    source_routes(observed)
    saved = copy.deepcopy(
        (await plan(settings, flow, [create(kind, default_value="1")]))["payload"]
    )
    target: dict[str, Any] = (
        {"choices": copy.deepcopy(STATIC)}
        if switch == "static"
        else {"choice_source": {"table": "incident", "field": "priority"}}
        if switch == "field"
        else {"choice_source": {"table": "change_request", "field": "state"}}
    )
    fixtures(settings, saved)
    source_routes(observed)
    update = {"op": "update_field", "collection": "inputs", "name": "status", **target}
    with pytest.raises(CapabilityError, match="explicit default_value"):
        await plan(settings, saved, [copy.deepcopy(update)])
    changed = await plan(
        settings, saved, [{**update, "default_value": "", "choice_option": "1"}]
    )
    row = changed["payload"]["inputs"][0]
    assert row["defaultValue"] == "" and row["choiceOption"] == "1"
    if switch == "static":
        assert row["choiceType"] == "1" and row["choices"] == STATIC
        assert not row["table"] and not row["columnName"]
        assert row["defaultChoices"] == STATIC
    else:
        assert row["choiceType"] == "2" and row["choices"] == []
        assert row["table"] == target["choice_source"]["table"]
        assert row["columnName"] == target["choice_source"]["field"]
    if kind == "array.choice":
        assert declarations.pack(declarations.unpack(row)) == row
        assert row["attributes"]["child_defaultValue"] == ""
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("kind", ["choice", "array.choice"])
async def test_static_to_table_switch_requires_explicit_default(settings, kind):
    flow, observed = subflow(), metadata()
    fixtures(settings, flow)
    source_routes(observed)
    static = create(kind, choices=copy.deepcopy(STATIC), default_value="queued")
    static.pop("choice_source")
    saved = copy.deepcopy((await plan(settings, flow, [static]))["payload"])
    fixtures(settings, saved)
    source_routes(observed)
    update = {
        "op": "update_field",
        "collection": "inputs",
        "name": "status",
        "choice_source": copy.deepcopy(SOURCE),
    }
    with pytest.raises(CapabilityError, match="explicit default_value"):
        await plan(settings, saved, [copy.deepcopy(update)])
    changed = await plan(settings, saved, [{**update, "default_value": "2"}])
    row = changed["payload"]["inputs"][0]
    assert (
        row["choiceType"] == "2" and row["choices"] == [] and row["defaultValue"] == "2"
    )


@pytest.mark.parametrize("option", ["1", "3"])
async def test_native_choice_option_controls_explicit_none_default(settings, option):
    flow, observed = subflow(), metadata()
    fixtures(settings, flow)
    source_routes(observed)
    if option == "3":
        with pytest.raises(CapabilityError):
            await plan(settings, flow, [create(choice_option=option, default_value="")])
    else:
        planned = await plan(
            settings, flow, [create(choice_option=option, default_value="")]
        )
        assert planned["payload"]["inputs"][0]["defaultValue"] == ""
    selected = await plan(
        settings, flow, [create(choice_option=option, default_value="1")]
    )
    assert selected["payload"]["inputs"][0]["choiceOption"] == option


@pytest.mark.parametrize(
    "defect",
    [
        "native_type",
        "native_duplicate",
        "native_dynamic",
        "native_data_structure",
        "dictionary_incomplete",
        "choice_missing",
        "choice_owner",
        "choice_bound",
        "table_revision",
        "dictionary_revision",
        "choice_revision",
        "unreadable_parent",
        "cycle",
    ],
)
async def test_native_choice_sources_fail_closed_without_independent_complete_evidence(
    settings, defect
):
    flow, observed = subflow(), metadata()
    if defect == "native_type":
        observed["schema"]["columns"][0]["definition"]["type"] = "integer"
    elif defect == "native_duplicate":
        observed["schema"]["columns"].append(
            copy.deepcopy(observed["schema"]["columns"][0])
        )
    elif defect == "native_dynamic":
        observed["schema"]["columns"][0]["definition"]["dynamic"] = {
            "provider": "unresolved"
        }
    elif defect == "native_data_structure":
        observed["schema"]["columns"][0]["definition"]["data_structure"] = "unresolved"
    elif defect == "dictionary_incomplete":
        observed["dictionary"][0].pop("internal_type")
    elif defect == "choice_missing":
        observed["choices"] = []
    elif defect == "choice_owner":
        observed["schema"]["columns"][0]["definition"]["choices"][0]["parameters"][
            "name"
        ] = "unrelated_table"
    elif defect == "choice_bound":
        prototype = copy.deepcopy(observed["choices"][0])
        observed["choices"] = [
            {**copy.deepcopy(prototype), "sys_id": f"{100 + index:032x}"}
            for index in range(5001)
        ]
    elif defect.endswith("_revision"):
        key = {
            "table_revision": "tables",
            "dictionary_revision": "dictionary",
            "choice_revision": "choices",
        }[defect]
        observed[key][0].pop("sys_mod_count")
    elif defect == "unreadable_parent":
        observed["tables"] = [observed["tables"][0]]
    elif defect == "cycle":
        observed["tables"][1].update(
            super_class=f"{1:032x}", **{"super_class.name": "incident"}
        )
    fixtures(settings, flow)
    source_routes(observed)
    with pytest.raises(CapabilityError) as error:
        await plan(settings, flow, [create(default_value="1")])
    assert error.value.code == "UNAVAILABLE"
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("kind", ["choice", "array.choice"])
@pytest.mark.parametrize("drift", ["tables", "dictionary", "choices", "native"])
@pytest.mark.parametrize("operation", ["create", "update"])
async def test_preview_apply_rejects_choice_source_revision_drift_before_any_save(
    settings, kind, drift, operation
):
    flow, observed = subflow(), metadata()
    fixtures(settings, flow)
    source_routes(observed)
    edit = create(kind, default_value="1")
    if operation == "update":
        flow = copy.deepcopy((await plan(settings, flow, [edit]))["payload"])
        edit = {
            "op": "update_field",
            "collection": "inputs",
            "name": "status",
            "hint": "Native source remains bound",
        }
    _, tools = fixtures(settings, flow)
    source_routes(observed)
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[edit],
        )
    )
    assert preview["status"] == "success", preview
    if drift == "native":
        observed["schema"]["columns"][0]["definition"]["vendor"]["native"] = False
    else:
        observed[drift][0]["sys_mod_count"] = "3"
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["error"]["code"] == "STALE_PREVIEW", applied
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_native_inheritance_selection_is_not_flattened_or_reconstructed(settings):
    flow, observed = subflow(), metadata()
    observed["choices"].append(
        revision(
            50,
            name="incident",
            element="state",
            value="3",
            label="Child-only choice",
            language="en",
            inactive="false",
            dependent_value="",
            sequence="3",
        )
    )
    # Native schema intentionally omits this readable child choice. The picker,
    # not a local parent/child union, determines defaultChoices.
    fixtures(settings, flow)
    source_routes(observed)
    planned = await plan(settings, flow, [create(default_value="2")])
    row = planned["payload"]["inputs"][0]
    assert row["table"] == "incident"
    assert {choice["value"] for choice in row["defaultChoices"]} == {"1", "2"}
    assert row["choices"] == []
    with pytest.raises(CapabilityError):
        await plan(settings, flow, [create(default_value="3")])


@pytest.mark.parametrize(
    "kind,target", [("choice", "status"), ("array.choice", "status.status_child0")]
)
async def test_same_source_edit_preserves_default_but_refreshes_cached_picker_metadata(
    settings, kind, target
):
    flow, observed = subflow(), metadata()
    fixtures(settings, flow)
    source_routes(observed)
    saved = copy.deepcopy(
        (await plan(settings, flow, [create(kind, default_value="1")]))["payload"]
    )
    saved["inputs"][0]["defaultChoices"][0]["vendor"] = {"stale": True}
    fixtures(settings, saved)
    source_routes(observed)
    changed = await plan(
        settings,
        saved,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": target,
                "choice_source": copy.deepcopy(SOURCE),
            }
        ],
    )
    row = changed["payload"]["inputs"][0]
    assert row["defaultValue"] == "1" and row["choices"] == []
    assert (
        row["defaultChoices"]
        == observed["schema"]["columns"][0]["definition"]["choices"]
    )
    assert row["defaultChoices"] != saved["inputs"][0]["defaultChoices"]
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "replacement",
    [
        {
            "choice_source": {"table": "incident", "field": "priority"},
            "default_value": "2",
        },
        {"choices": STATIC, "default_value": "queued"},
    ],
)
async def test_array_choice_source_switch_on_synthetic_item_updates_authoritative_parent(
    settings, replacement
):
    flow, observed = subflow(), metadata()
    fixtures(settings, flow)
    source_routes(observed)
    saved = copy.deepcopy(
        (await plan(settings, flow, [create("array.choice", default_value="1")]))[
            "payload"
        ]
    )
    fixtures(settings, saved)
    source_routes(observed)
    changed = await plan(
        settings,
        saved,
        [
            {
                "op": "update_field",
                "collection": "inputs",
                "name": "status.status_child0",
                **copy.deepcopy(replacement),
            }
        ],
    )
    row = changed["payload"]["inputs"][0]
    assert (
        row["defaultValue"]
        == row["attributes"]["child_defaultValue"]
        == replacement["default_value"]
    )
    if "choices" in replacement:
        assert row["choiceType"] == "1" and row["choices"] == STATIC
        assert row["table"] == row["columnName"] == ""
    else:
        assert row["choiceType"] == "2" and row["columnName"] == "priority"
        assert row["choices"] == []
    assert declarations.pack(declarations.unpack(row)) == row


@pytest.mark.parametrize(
    "extra",
    [
        {"choice_source": None},
        {"choice_source": {"table": "incident"}},
        {"choice_source": {"table": "incident", "field": "state", "extra": True}},
        {"choice_source": {"table": "incident^ORname=task", "field": "state"}},
        {"choice_source": {"table": "incident", "field": "parent.state"}},
        {"choices": STATIC},
        {"choice_option": []},
        {"choice_option": "2"},
    ],
)
async def test_choice_source_settings_reject_ambiguous_or_malformed_configuration(
    settings, extra
):
    flow = subflow()
    fixtures(settings, flow)
    with pytest.raises(CapabilityError):
        await plan(settings, flow, [create(**copy.deepcopy(extra))])
    assert not any("/schema" in str(call.request.url) for call in http_mock.calls)
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("kind", ["choice", "array.choice"])
@pytest.mark.parametrize(
    "metadata_kind",
    [
        "dependent",
        "dependent_duplicates",
        "qualified",
        "redirect_flags",
        "optional_absence",
    ],
)
async def test_offered_native_choice_metadata_is_preserved_not_reinterpreted(
    settings, kind, metadata_kind
):
    flow, observed = subflow(), metadata()
    definition = observed["schema"]["columns"][0]["definition"]
    if metadata_kind == "dependent":
        definition["dependent"] = "category"
        definition["choices"][0]["parameters"]["dependent_values"] = ["hardware"]
        observed["dictionary"][0]["dependent"] = "category"
        observed["choices"][0]["dependent_value"] = "hardware"
    elif metadata_kind == "dependent_duplicates":
        duplicate = copy.deepcopy(definition["choices"][0])
        duplicate.update(
            label="Another dependent label", rawLabel="Another dependent label"
        )
        duplicate["parameters"]["dependent_values"] = ["software"]
        definition["choices"].append(duplicate)
        observed["dictionary"][0]["dependent"] = "category"
        observed["choices"].append(
            revision(
                70,
                name="task",
                element="state",
                value="1",
                label=duplicate["label"],
                language="en",
                inactive="false",
                dependent_value="software",
                sequence="3",
            )
        )
    elif metadata_kind == "qualified":
        definition["ref_qual"] = "active=true"
        observed["dictionary"][0]["reference_qual"] = "active=true"
        observed["dictionary"][0]["reference_qual_condition"] = "active=true"
    elif metadata_kind == "redirect_flags":
        # The native option's exact owner remains authoritative, even when the
        # dictionary carries redirect metadata not used by these offered values.
        observed["dictionary"][0]["choice_table"] = "other_table"
    else:
        observed["dictionary"][0].pop("dependent")
    fixtures(settings, flow)
    source_routes(observed)
    planned = await plan(settings, flow, [create(kind, default_value="1")])
    row = planned["payload"]["inputs"][0]
    assert row["choices"] == [] and row["choiceType"] == "2"
    assert row["defaultChoices"] == definition["choices"]
    assert row["defaultValue"] == "1"
    assert all(call.request.method == "GET" for call in http_mock.calls)


def redirected_metadata(*, native_element=False):
    observed = metadata()
    observed["tables"].append(
        revision(
            60,
            name="choice_origin",
            super_class="",
            **{"super_class.name": ""},
        )
    )
    observed["dictionary"][0].update(
        choice_table="choice_origin", choice_field="status_code"
    )
    for choice in observed["schema"]["columns"][0]["definition"]["choices"]:
        choice["parameters"]["name"] = "choice_origin"
        if native_element:
            choice["parameters"]["element"] = "status_code"
    for row in observed["choices"]:
        if row["element"] == "state":
            row.update(name="choice_origin", element="status_code")
    return observed


@pytest.mark.parametrize("kind", ["choice", "array.choice"])
@pytest.mark.parametrize("native_element", [False, True])
async def test_redirected_native_choice_origins_are_independently_bound_without_flattening(
    settings, kind, native_element
):
    flow, observed = subflow(), redirected_metadata(native_element=native_element)
    if native_element:
        observed["dictionary"][0]["choice_field"] = ""
    fixtures(settings, flow)
    source_routes(observed)
    planned = await plan(settings, flow, [create(kind, default_value="2")])
    row = planned["payload"]["inputs"][0]
    assert row["table"] == "incident" and row["columnName"] == "state"
    assert row["choices"] == [] and row["defaultValue"] == "2"
    assert (
        row["defaultChoices"]
        == observed["schema"]["columns"][0]["definition"]["choices"]
    )
    assert any(
        call.request.url.path.endswith("/sys_db_object")
        and call.request.url.params.get("sysparm_query") == "name=choice_origin"
        for call in http_mock.calls
    )
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("drift", ["table", "choices"])
async def test_redirected_native_choice_origin_revision_drift_blocks_apply(
    settings, drift
):
    flow, observed = subflow(), redirected_metadata()
    _, tools = fixtures(settings, flow)
    source_routes(observed)
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[create(default_value="1")],
        )
    )
    assert preview["status"] == "success", preview
    records = observed["tables"] if drift == "table" else observed["choices"]
    next(row for row in records if row["name"] == "choice_origin")["sys_mod_count"] = (
        "3"
    )
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["error"]["code"] == "STALE_PREVIEW", applied
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "facets",
    [
        {"table": "sys_flow_record_trigger", "columnName": "run_flow_in"},
        {"table": "", "columnName": "fixture_timezone"},
        {
            "use_dependent": True,
            "dependent_on": "duration_type",
            "attributes": {"dependency_value": "relative_duration"},
        },
        {"table": "task", "reference": "task", "ref_qual": "active=true"},
    ],
)
async def test_native_parameter_choices_bind_concrete_options_not_owner_facets_or_cached_defaults(
    settings, facets
):
    flow = base_flow()
    parameter = {
        "type": "choice",
        "choiceOption": "3",
        "choices": [native_choice("1", "First"), native_choice("2", "Second")],
        "defaultChoices": [native_choice("cached_only", "Not a current option")],
        **copy.deepcopy(facets),
    }
    flow["actionInstances"][0]["inputs"][0].update(
        value="1", parameter=copy.deepcopy(parameter)
    )
    fixtures(settings, flow)
    planned = await plan(
        settings,
        flow,
        [{"op": "update_inputs", "step": "ui-1", "inputs": {"log_message": "2"}}],
    )
    binding = planned["payload"]["actionInstances"][0]["inputs"][0]
    assert binding["value"] == "2" and binding["parameter"] == parameter
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "update_inputs",
                    "step": "ui-1",
                    "inputs": {"log_message": "cached_only"},
                }
            ],
        )
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "metadata",
    [
        {
            "choiceType": "2",
            "table": "task",
            "columnName": "state",
            "choices": [native_choice("1", "First")],
        },
        {"choiceType": "2", "table": "task", "columnName": "", "choices": []},
        {"table": "task", "columnName": "state", "choices": []},
        {"choices": [], "defaultValue": "1"},
    ],
)
async def test_empty_current_parameter_choices_never_fall_back_to_cached_default_choices(
    settings, metadata
):
    flow = base_flow()
    parameter = {
        "type": "choice",
        "defaultChoices": [native_choice("1", "Cached")],
        **copy.deepcopy(metadata),
    }
    flow["actionInstances"][0]["inputs"][0].update(value="", parameter=parameter)
    fixtures(settings, flow)
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [{"op": "update_inputs", "step": "ui-1", "inputs": {"log_message": "1"}}],
        )
    assert all(call.request.method == "GET" for call in http_mock.calls)
