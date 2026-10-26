"""Native scalar authoring contracts through call bindings and saved flow payloads."""

import copy
import json
import math
from typing import Any

import httpx2
import pytest

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import calls, data, widgets
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_editing import BASE, FLOW, base_flow, fixtures, persist
from tests.test_flow_fields import plan

_PARAMETER: dict[str, Any] = {
    "id": "8" * 32,
    "name": "native_value",
    "label": "Native value",
    "type": "string",
    "mandatory": False,
    "readonly": False,
    "maxsize": 4000,
    "defaultValue": "",
    "defaultDisplayValue": "",
    "children": [],
    "reference": "",
    "ref_qual": "",
    "use_dependent": False,
    "dependent_on": "",
    "attributes": {"opaque_native_attribute": "preserve"},
    "opaque_native_metadata": {"preserve": True},
}


def parameter(kind: str, **metadata: Any) -> dict[str, Any]:
    result = copy.deepcopy(_PARAMETER)
    result.update(copy.deepcopy(metadata))
    result["type"] = kind
    return result


def binding(kind: str, **metadata: Any) -> dict[str, Any]:
    declared = parameter(kind, **metadata)
    return {
        **copy.deepcopy(declared),
        "parameter": copy.deepcopy(declared),
        "value": "",
        "displayValue": "",
        "opaque_instance_metadata": {"preserve": True},
        "children": [],
    }


def widget_flow(kind: str, **metadata: Any) -> dict[str, Any]:
    flow = copy.deepcopy(base_flow())
    flow["actionInstances"][0]["actionType"]["outputs"] = [
        {"name": "text", "type": "string", "children": []},
        {"name": "number", "type": "float", "children": []},
        {"name": "flag", "type": "boolean", "children": []},
        {"name": "date", "type": "glide_date_time", "children": []},
        {"name": "object", "type": "object", "children": []},
        {"name": "list", "type": "records", "reference": "sc_req_item", "children": []},
    ]
    flow["actionInstances"][1]["inputs"] = [binding(kind, **metadata)]
    return flow


def deadline(
    action: str = "none", date_type: str = "actual", date: str = "{{}}", **fields: Any
) -> str:
    return json.dumps(
        {
            "action": action,
            "date_type": date_type,
            "date": date,
            "duration": 1,
            "duration_type": "days",
            "schedule": "",
            "schedule_label": "",
            **fields,
        }
    )


@pytest.mark.parametrize("subflow", [False, True])
@pytest.mark.parametrize(
    "kind,value",
    [
        ("string_full_utf8", "Grøn 🙂"),
        ("text", "a\nb"),
        ("longtext", "line one\nline two"),
        ("multi_small", "<body>native text</body>"),
        ("html", '<p class="native">HTML &amp; text</p>'),
        ("translated_html", "<p>Oversat tekst</p>"),
        ("float", "-0.125"),
        ("decimal", "12.3400"),
        ("double", 0.125),
        ("number", "1e3"),
        ("integer", "0002"),
        ("longint", -2),
        ("name_values", '[ {"name":"header","value":"literal"} ]'),
        ("simple_name_values", '[{"name":"query","value":"literal"}]'),
        ("reference", "1" * 32),
        ("glide_list", "1" * 32 + "," + "2" * 32),
        ("records", "1" * 32 + "," + "2" * 32),
    ],
)
def test_native_call_bindings_preserve_value_type_and_authoritative_metadata(
    subflow, kind, value
):
    definition = {
        "id": "9" * 32,
        "name": "Native scalar call",
        "inputs": [parameter(kind)],
        "outputs": [],
    }
    before = copy.deepcopy(definition)
    step = calls.step(
        copy.deepcopy(base_flow()),
        definition,
        {"native_value": value},
        subflow=subflow,
        uid="native-call",
    )
    saved = step.raw["inputs"][0]
    assert saved["value"] == value and type(saved["value"]) is type(value)
    assert saved["displayValue"] == value
    assert saved["parameter"] == before["inputs"][0]
    assert step.raw["subFlow" if subflow else "actionType"] == before
    assert definition == before


@pytest.mark.parametrize("kind", ["float", "decimal", "double", "number"])
@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        {},
        [],
        math.inf,
        -math.inf,
        math.nan,
        "NaN",
        "Infinity",
        " 1",
        "1.2.3",
    ],
)
def test_numeric_inputs_reject_nonfinite_and_non_numeric_values(kind, value):
    row = binding(kind)
    before = copy.deepcopy(row)
    with pytest.raises(CapabilityError):
        data.set_value(row, value)
    assert row == before


@pytest.mark.parametrize("kind", ["integer", "longint"])
@pytest.mark.parametrize("value", [-2, "-2", 2, "2"])
def test_integer_native_metadata_boundaries_preserve_wire_values(kind, value):
    row = binding(kind, attributes={"min_value": "-2", "max_value": "2"})
    before = copy.deepcopy(row["parameter"])
    data.set_value(row, value)
    assert row["value"] == value and type(row["value"]) is type(value)
    assert row["parameter"] == before


@pytest.mark.parametrize("value", [-3, "3", 1.5, "1.5", True])
def test_integer_rejects_out_of_bounds_or_fractional_native_values(value):
    with pytest.raises(CapabilityError):
        data.set_value(
            binding("integer", attributes={"min_value": "-2", "max_value": "2"}), value
        )


def test_integer_does_not_invent_a_global_machine_word_boundary():
    row = binding("integer")
    data.set_value(row, 2147483648)
    assert row["value"] == 2147483648


@pytest.mark.parametrize("kind", ["name_values", "simple_name_values"])
@pytest.mark.parametrize(
    "value",
    [
        [{"name": "x", "value": "y"}],
        {"x": "y"},
        '{"x":"y"}',
        '[{"name":"x","value":false}]',
        '[{"name":"x","value":"y","arbitrary":"field"}]',
        '[{"name":"x"}]',
        "not JSON",
    ],
)
def test_basic_name_values_rejects_arbitrary_dtos_and_malformed_native_rows(
    kind, value
):
    with pytest.raises(CapabilityError):
        data.set_value(binding(kind), copy.deepcopy(value))


def advanced_attributes(**fields):
    return {
        "advanced_name_values": "true",
        "advanced_inputs_config": "OMIT",
        "name_candrop": True,
        "value_candrop": True,
        **fields,
    }


def advanced_value(**fields):
    row = {
        "name": "header",
        "value": "text",
        "attributes": {"omit_if_empty": False, "mandatory": True},
    }
    row.update(copy.deepcopy(fields))
    return json.dumps({"type": "ADV_NV", "value": [row]})


def test_advanced_name_values_preserves_native_envelope_and_separate_schema():
    row = binding("name_values", attributes=advanced_attributes())
    before = copy.deepcopy(row["parameter"])
    value = advanced_value()
    data.set_value(row, value)
    assert row["value"] == value and row["displayValue"] == value
    assert row["parameter"] == before
    assert row["children"] == []


@pytest.mark.parametrize(
    "value",
    [
        '{"type":"OTHER","value":[]}',
        '[{"name":"header","value":"text"}]',
        '{"type":"ADV_NV","value":{}}',
        advanced_value(value=""),
        advanced_value(attributes={"mandatory": "true"}),
        advanced_value(attributes={"arbitrary": True}),
    ],
)
def test_advanced_name_values_rejects_wrong_native_format_and_empty_required_entry(
    value,
):
    with pytest.raises(CapabilityError):
        data.set_value(binding("name_values", attributes=advanced_attributes()), value)


@pytest.mark.parametrize("key", ["name", "value"])
def test_advanced_name_values_obeys_each_declared_drop_permission(key):
    attributes = advanced_attributes(**{key + "_candrop": False})
    with pytest.raises(CapabilityError):
        data.set_value(
            binding("name_values", attributes=attributes),
            advanced_value(**{key: "{{ui-1.text}}"}),
        )


@pytest.mark.parametrize("kind", ["reference", "document_id"])
def test_reference_picker_native_json_is_preserved_without_treating_envelope_as_sys_id_length(
    kind,
):
    value = json.dumps(
        {
            "display": "Fixture record",
            "value": "1" * 32,
            "table": "sc_req_item",
            "sys_id": "1" * 32,
        }
    )
    row = binding(kind, reference="sc_req_item", maxsize=32)
    before = copy.deepcopy(row["parameter"])
    data.set_value(row, value)
    assert row["value"] == value
    assert row["parameter"] == before


@pytest.mark.parametrize(
    "kind,value",
    [
        ("reference", {"value": "1" * 32, "display": "Fixture"}),
        ("reference", '{"display":"Fixture"}'),
        (
            "reference",
            json.dumps({"display": "Fixture", "value": "1" * 32, "sys_id": "2" * 32}),
        ),
        (
            "reference",
            json.dumps({"display": "Fixture", "value": "1" * 32, "arbitrary": True}),
        ),
        (
            "reference",
            json.dumps(
                {"display": "Fixture", "value": "1" * 32, "data_source": "other_table"}
            ),
        ),
        ("records", ["1" * 32]),
        ("records", "1" * 32 + ",not-an-id"),
        ("glide_list", "1" * 32 + "," + "1" * 32),
        ("table_name", "task.name"),
    ],
)
def test_reference_table_and_record_list_reject_non_native_values(kind, value):
    with pytest.raises((CapabilityError, ValueError)):
        data.set_value(binding(kind), copy.deepcopy(value))


@pytest.mark.parametrize("flag", ["readonly", "readOnly", "read_only"])
@pytest.mark.parametrize("location", ["parameter", "binding"])
def test_native_readonly_flags_cannot_be_overridden(flag, location):
    row = binding("string_full_utf8")
    if location == "parameter":
        row["parameter"][flag] = True
    else:
        row[flag] = True
    with pytest.raises(CapabilityError):
        data.set_value(row, "changed")


def test_hidden_input_does_not_lose_mandatory_or_dependency_metadata():
    row = binding(
        "string_full_utf8",
        mandatory=True,
        dependent_on="inputs.table",
        use_dependent=True,
        attributes={"visible_in_ui": "false"},
    )
    before = copy.deepcopy(row)
    assert widgets.input_state(row) == {
        "visible": False,
        "mandatory": True,
        "readonly": False,
    }
    with pytest.raises(CapabilityError):
        data.set_value(row, "")
    assert row == before


def test_native_text_maximum_counts_utf16_units_without_changing_unicode():
    row = binding("string_full_utf8", maxsize=4)
    data.set_value(row, "🙂🙂")
    assert row["value"] == "🙂🙂"
    with pytest.raises(CapabilityError):
        data.set_value(copy.deepcopy(row), "🙂🙂x")


@pytest.mark.parametrize(
    "action,date_type,fields",
    [
        ("none", "actual", {}),
        ("approve", "actual", {"date": "{{ui-1.date}}"}),
        (
            "reject",
            "relative",
            {
                "date": "{{ui-1.date}}",
                "duration": "2",
                "duration_type": "hours",
                "schedule": "3" * 32,
                "schedule_label": "Fixture schedule",
            },
        ),
        ("cancel", "actual", {"date": "{{ui-1.date}}"}),
    ],
)
async def test_native_deadline_forms_reach_saved_payload_without_rewriting_metadata(
    settings, action, date_type, fields
):
    flow = widget_flow(
        "schedule_date_time", attributes={"ui_approval": "true"}, maxsize=40
    )
    before = copy.deepcopy(flow["actionInstances"][1]["inputs"][0]["parameter"])
    _, tools = fixtures(settings, copy.deepcopy(flow))
    value = deadline(action, date_type, **fields)
    saved = {}

    def save(request):
        payload = json.loads(request.content)
        saved.update(copy.deepcopy(payload))
        persist(copy.deepcopy(payload))
        return httpx2.Response(200, json={"result": {"data": copy.deepcopy(payload)}})

    http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
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
                    "inputs": {"native_value": value},
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    actual = saved["actionInstances"][1]["inputs"][0]
    assert actual["value"] == value and actual["displayValue"] == ""
    assert actual["parameter"] == before
    assert actual["opaque_instance_metadata"] == {"preserve": True}
    assert flow["actionInstances"][1]["inputs"][0]["value"] == ""


@pytest.mark.parametrize(
    "value",
    [
        deadline("approve"),
        deadline("approve", date="2026-10-26 12:00:00"),
        deadline("approve", date="prefix {{ui-1.date}}"),
        deadline("approve", date="{{ui-1.date}}", duration=False),
        deadline("reject", "relative", date="{{ui-1.date}}", duration=0),
        deadline(
            "reject", "relative", date="{{ui-1.date}}", duration_type="fortnights"
        ),
    ],
)
def test_deadline_rejects_forms_not_emitted_by_native_controls(value):
    with pytest.raises(CapabilityError):
        data.set_value(
            binding("schedule_date_time", attributes={"ui_approval": "true"}), value
        )


@pytest.mark.parametrize(
    "kind,value,attributes",
    [
        ("name_values", '[{"name":"header","value":"prefix {{ui-1.text}}"}]', {}),
        ("simple_name_values", '[{"name":"header","value":"{{ui-1.text}}"}]', {}),
        (
            "name_values",
            advanced_value(
                value="{{ui-1.text}}",
                attributes={"omit_if_empty": "{{ui-1.flag}}", "mandatory": True},
            ),
            advanced_attributes(),
        ),
    ],
)
async def test_name_value_embedded_pills_use_native_leaf_types(
    settings, kind, value, attributes
):
    flow = widget_flow(kind, attributes=copy.deepcopy(attributes))
    fixtures(settings, copy.deepcopy(flow))
    result = await plan(
        settings,
        flow,
        [{"op": "update_inputs", "step": "ui-2", "inputs": {"native_value": value}}],
    )
    actual = result["payload"]["actionInstances"][1]["inputs"][0]
    assert actual["value"] == value
    assert actual["parameter"] == flow["actionInstances"][1]["inputs"][0]["parameter"]


@pytest.mark.parametrize(
    "kind,value,attributes",
    [
        ("float", "{{ui-1.text}}", {}),
        ("decimal", "{{ui-1.number}}", {}),
        ("string_full_utf8", "{{ui-1.object}}", {}),
        ("html", "prefix {{ui-1.list}}", {}),
        ("name_values", '[{"name":"header","value":"{{ui-1.object}}"}]', {}),
        (
            "name_values",
            advanced_value(
                attributes={"omit_if_empty": "{{ui-1.text}}", "mandatory": True}
            ),
            advanced_attributes(),
        ),
        (
            "schedule_date_time",
            deadline("approve", date="{{ui-1.text}}"),
            {"ui_approval": "true"},
        ),
    ],
)
async def test_native_widget_producer_type_boundaries_reject_wrong_bindings(
    settings, kind, value, attributes
):
    flow = widget_flow(kind, attributes=copy.deepcopy(attributes))
    fixtures(settings, copy.deepcopy(flow))
    with pytest.raises(CapabilityError):
        await plan(
            settings,
            flow,
            [
                {
                    "op": "update_inputs",
                    "step": "ui-2",
                    "inputs": {"native_value": value},
                }
            ],
        )


@pytest.mark.parametrize(
    "kind,value",
    [
        ("glide_duration", "1970-01-01 01:02:03"),
        ("duration", "1970-01-01 01:02:03"),
        ("glide_time", "1970-01-01 01:02:03"),
        ("glide_date_time", "2026-10-26 01:02:03"),
        ("datetime", "2026-10-26 01:02:03"),
        ("calendar_date_time", "2026-10-26 01:02:03"),
        ("schedule_date_time", "2026-10-26 01:02:03"),
        ("date", "2026-10-26"),
        ("glide_date", "2026-10-26"),
        ("due_date", "2026-10-26"),
    ],
)
def test_common_temporal_inputs_use_shared_native_scalar_contract(kind, value):
    row = binding(kind)
    data.set_value(row, value)
    assert row["value"] == value
    assert row["displayValue"] == value
    with pytest.raises(CapabilityError):
        data.set_value(copy.deepcopy(row), "prefix {{ui-1.date}}")


@pytest.mark.parametrize(
    "value",
    [
        "ApprovesAnyM",
        "RejectsAllM",
        "ApprovesRejectsResM",
        "Approves25%M",
        "Rejects500#M",
        "ApprovesResU[{{static." + "1" * 32 + "}}]G[{{static." + "2" * 32 + "}}]",
        "Approves100%U[{{static."
        + "1" * 32
        + "}}]&AllG[{{static."
        + "2" * 32
        + "}}]OrRejectsAnyM",
    ],
)
def test_native_approval_operator_manual_and_static_forms_preserve_wire_values(value):
    row = binding("approval_rules")
    before = copy.deepcopy(row["parameter"])
    data.set_value(row, value)
    assert row["value"] == value
    assert row["parameter"] == before
    assert widgets.pill_bindings(row, "approval_rules", row["parameter"]) == []
    assert widgets.approval_literal_pills(value) == {
        data_path for data_path, _ in widgets.approval_references(value)
    }


@pytest.mark.parametrize(
    "value",
    [
        "Approves0%M",
        "Approves101%M",
        "Approves0#M",
        "Approves501#M",
        "ApprovesAnyU[not-a-pill]",
        "ApprovesAll",
        "ApprovesUnknownM",
        "ApprovesAnyU[{{static.not-a-sys-id}}]",
    ],
)
def test_native_approval_rejects_malformed_or_out_of_control_values(value):
    with pytest.raises((CapabilityError, ValueError)):
        data.set_value(binding("approval_rules"), value)


def test_generic_native_slushbucket_preserves_order_and_declared_source_metadata():
    available = [
        {"label": "First option", "value": "first"},
        {"label": "Second option", "value": "second"},
    ]
    row = binding(
        "slushbucket",
        availableList=copy.deepcopy(available),
        attributes={"source_table": "native_source"},
    )
    before = copy.deepcopy(row["parameter"])
    value = [copy.deepcopy(available[1]), copy.deepcopy(available[0])]
    data.set_value(row, copy.deepcopy(value))
    assert row["value"] == value and row["displayValue"] == value
    assert row["parameter"] == before
    assert row["children"] == []


@pytest.mark.parametrize(
    "value",
    [
        [{"label": "Unknown", "value": "unknown"}],
        [{"label": "Wrong label", "value": "first"}],
        [{"label": "First option", "value": "first", "arbitrary": True}],
        [
            {"label": "First option", "value": "first"},
            {"label": "First option", "value": "first"},
        ],
    ],
)
def test_generic_slushbucket_rejects_values_not_emitted_from_declared_options(value):
    row = binding(
        "slushbucket", availableList=[{"label": "First option", "value": "first"}]
    )
    with pytest.raises(CapabilityError):
        data.set_value(row, copy.deepcopy(value))


def test_catalog_wrapper_native_composites_preserve_multiple_declared_table_sources():
    value = "1" * 32 + ":item_option_new," + "2" * 32 + ":item_option_new_set"
    row = binding(
        "slushbucket",
        attributes={
            "source_table": "item_option_new",
            "include_multirow_variablesets": "true",
        },
    )
    data.set_value(row, value)
    assert row["value"] == value
    assert widgets.selections(value, row["parameter"]) == [
        ("1" * 32, "item_option_new"),
        ("2" * 32, "item_option_new_set"),
    ]


@pytest.mark.parametrize(
    "kind,value,metadata",
    [
        ("string_full_utf8", "Grøn 🙂", {}),
        ("decimal", "12.3400", {}),
        ("name_values", advanced_value(), {"attributes": advanced_attributes()}),
        (
            "reference",
            json.dumps(
                {
                    "display": "Fixture",
                    "value": "1" * 32,
                    "table": "sc_req_item",
                    "sys_id": "1" * 32,
                }
            ),
            {"reference": "sc_req_item", "maxsize": 32},
        ),
        ("field_list", "number,short_description", {"reference": "sc_req_item"}),
        ("approval_rules", "ApprovesAnyM", {}),
        ("approval_rules", "Approves50%U[{{static." + "1" * 32 + "}}]", {}),
        (
            "slushbucket",
            [{"label": "Native option", "value": "native"}],
            {"availableList": [{"label": "Native option", "value": "native"}]},
        ),
    ],
)
async def test_common_widget_modes_survive_independent_native_save_readback(
    settings, kind, value, metadata
):
    flow = widget_flow(kind, **copy.deepcopy(metadata))
    before = copy.deepcopy(flow["actionInstances"][1]["inputs"][0]["parameter"])
    _, tools = fixtures(settings, copy.deepcopy(flow))
    if kind == "field_list":
        http_mock.get(f"{BASE}/api/now/table/sys_db_object").respond(
            200,
            json={
                "result": [
                    {
                        "name": "sc_req_item",
                        "label": "Requested Item",
                        "sys_id": "1" * 32,
                    }
                ]
            },
        )
        http_mock.get(
            f"{BASE}/api/now/processflow/table/sc_req_item/descriptor"
        ).respond(200, json={"result": {"data": {"displayField": "number"}}})
    saved = {}

    def save(request):
        payload = json.loads(request.content)
        saved.update(copy.deepcopy(payload))
        persist(copy.deepcopy(payload))
        return httpx2.Response(200, json={"result": {"data": copy.deepcopy(payload)}})

    http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
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
                    "inputs": {"native_value": copy.deepcopy(value)},
                }
            ],
        )
    )
    assert preview["status"] == "success", preview
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert applied["status"] == "success", applied
    actual = saved["actionInstances"][1]["inputs"][0]
    assert actual["value"] == value and type(actual["value"]) is type(value)
    assert actual["parameter"] == before
    assert actual["opaque_instance_metadata"] == {"preserve": True}


def test_declared_choice_schema_and_opaque_choice_metadata_remain_authoritative():
    choices = [
        {
            "label": "Allowed",
            "value": "allowed",
            "order": 7,
            "native_choice_metadata": "preserve",
        }
    ]
    row = binding(
        "choice",
        choices=copy.deepcopy(choices),
        defaultChoices=copy.deepcopy(choices),
        choiceOption="3",
        mandatory=True,
    )
    before = copy.deepcopy(row["parameter"])
    data.set_value(row, "allowed")
    assert row["value"] == "allowed" and row["parameter"] == before
    with pytest.raises(CapabilityError):
        data.set_value(copy.deepcopy(row), "not-allowed")


def test_non_choice_text_metadata_is_not_promoted_to_an_enum():
    row = binding(
        "string_full_utf8", choices=[{"label": "Schema metadata", "value": "one"}]
    )
    before = copy.deepcopy(row["parameter"])
    data.set_value(row, "two")
    assert row["value"] == "two" and row["parameter"] == before


def test_binding_mandatory_flag_is_not_lost_when_parameter_flag_is_false():
    row = binding("string_full_utf8", mandatory=False)
    row["mandatory"] = True
    with pytest.raises(CapabilityError):
        data.set_value(row, "")


@pytest.mark.parametrize(
    "field,value",
    [
        ("action", []),
        ("date_type", {}),
        ("date", []),
        ("duration", True),
        ("duration_type", {}),
    ],
)
def test_deadline_malformed_nested_field_types_raise_capability_errors(field, value):
    payload = json.loads(deadline())
    payload[field] = copy.deepcopy(value)
    with pytest.raises(CapabilityError):
        data.set_value(
            binding("schedule_date_time", attributes={"ui_approval": "true"}),
            json.dumps(payload),
        )


def test_advanced_name_value_options_require_the_native_configuration():
    row = binding("name_values", attributes={"advanced_name_values": "true"})
    with pytest.raises(CapabilityError):
        data.set_value(row, advanced_value())
    value = advanced_value(attributes={})
    data.set_value(row, value)
    assert row["value"] == value


def test_readonly_widget_defaults_can_be_inspected_but_cannot_be_replaced():
    row = binding("string_full_utf8", readonly=True, defaultValue="server-owned")
    widgets.validate("string_full_utf8", "server-owned", row["parameter"])
    with pytest.raises(CapabilityError):
        data.set_value(row, "caller-owned")


def test_native_template_parser_preserves_escaped_delimiters_and_special_field_values():
    value = "{{ui-1.template}}^description=kept^^^short_description=fd-scripted^coalesce_fields=number,active"
    assert widgets.template_fields(value) == [
        ("description", "kept^"),
        ("short_description", "fd-scripted"),
        ("coalesce_fields", "number,active"),
    ]


@pytest.mark.parametrize(
    "value",
    [
        "^description=value",
        "description=value^",
        "description=prefix fd-scripted",
        "description=javascript:bad()",
        "{{ui-1.template}}^missing_equals",
    ],
)
def test_native_template_rejects_malformed_clauses_or_script_markers(value):
    with pytest.raises(CapabilityError):
        data.set_value(binding("template_value"), value)


@pytest.mark.parametrize(
    "source,target", [("duration", "glide_duration"), ("glide_duration", "duration")]
)
async def test_native_duration_alias_binds_the_same_temporal_family(
    settings, source, target
):
    flow = widget_flow(target)
    flow["actionInstances"][0]["actionType"]["outputs"].append(
        {"name": "duration_value", "type": source, "children": []}
    )
    fixtures(settings, copy.deepcopy(flow))
    value = "{{ui-1.duration_value}}"
    result = await plan(
        settings,
        flow,
        [{"op": "update_inputs", "step": "ui-2", "inputs": {"native_value": value}}],
    )
    actual = result["payload"]["actionInstances"][1]["inputs"][0]
    assert actual["value"] == value and actual["parameter"]["type"] == target


@pytest.mark.parametrize("value", ["{{ui-1.template}}", "{{ui-1.template}}^"])
def test_native_whole_template_with_no_overrides_preserves_emitted_value(value):
    row = binding("template_value")
    data.set_value(row, value)
    assert row["value"] == value
    assert widgets.template_fields(value) == []


@pytest.mark.parametrize(
    "metadata",
    [
        {"reference": "sc_req_item"},
        {"dependent_on": "inputs.table", "use_dependent": True},
    ],
)
def test_field_list_native_text_preserves_schema_dependency_and_value(metadata):
    row = binding("field_list", **copy.deepcopy(metadata))
    before = copy.deepcopy(row["parameter"])
    value = "number,short_description"
    data.set_value(row, value)
    assert row["value"] == value and row["displayValue"] == value
    assert row["parameter"] == before and row["children"] == []


@pytest.mark.parametrize("value", [["number"], {"fields": ["number"]}, 1, True])
def test_field_list_rejects_non_native_dtos(value):
    with pytest.raises(CapabilityError):
        data.set_value(
            binding("field_list", reference="sc_req_item"), copy.deepcopy(value)
        )


def test_nonempty_field_list_requires_its_native_table_metadata():
    with pytest.raises(CapabilityError):
        data.set_value(binding("field_list"), "number")


@pytest.mark.parametrize("producer_kind", ["field_list", "object"])
async def test_field_list_keeps_its_native_producer_type_boundary(
    settings, producer_kind
):
    flow = widget_flow("field_list", reference="sc_req_item")
    flow["actionInstances"][0]["actionType"]["outputs"].append(
        {
            "name": "field_names",
            "type": producer_kind,
            "reference": "sc_req_item",
            "children": [],
        }
    )
    fixtures(settings, copy.deepcopy(flow))
    http_mock.get(f"{BASE}/api/now/table/sys_db_object").respond(
        200,
        json={
            "result": [
                {"name": "sc_req_item", "label": "Requested Item", "sys_id": "1" * 32}
            ]
        },
    )
    http_mock.get(f"{BASE}/api/now/processflow/table/sc_req_item/descriptor").respond(
        200, json={"result": {"data": {"displayField": "number"}}}
    )
    edits = [
        {
            "op": "update_inputs",
            "step": "ui-2",
            "inputs": {"native_value": "{{ui-1.field_names}}"},
        }
    ]
    if producer_kind == "object":
        with pytest.raises(CapabilityError):
            await plan(settings, flow, edits)
    else:
        result = await plan(settings, flow, edits)
        actual = result["payload"]["actionInstances"][1]["inputs"][0]
        assert actual["value"] == "{{ui-1.field_names}}"
        assert actual["parameter"]["type"] == "field_list"
