"""Source-derived catalog DTO projections; fixtures are not live-captured responses."""

import copy
import json
import uuid
from typing import Any

import pytest

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import catalog_schema, data, declarations

CHOICES = [{"value": "open", "label": "Open"}, {"value": "closed", "label": "Closed"}]


def native(kind: str, name: str = "result", **metadata: Any) -> dict[str, Any]:
    row = {
        "id": "d" * 32,
        "name": name,
        "label": "Catalog result",
        "type": kind,
        "table": "item_option_new",
        "children": [],
        "attributes": {"catalogType": "6"},
        **copy.deepcopy(metadata),
    }
    if kind == "array.reference":
        row.setdefault("reference", "sys_user")
    if kind in {"choice", "array.choice"}:
        row.setdefault("choices", copy.deepcopy(CHOICES))
    return row


def assert_unavailable(row: dict[str, Any]) -> None:
    before = copy.deepcopy(row)
    with pytest.raises(CapabilityError) as caught:
        catalog_schema.view(row)
    assert caught.value.code == "UNAVAILABLE"
    assert row == before


@pytest.mark.parametrize(
    "kind,child_type,type_label",
    [
        ("array.string", "string", ""),
        ("array.integer", "integer", ""),
        ("array.boolean", "boolean", ""),
        ("array.datetime", "glide_date_time", ""),
        ("array.choice", "choice", ""),
        ("array.reference", "reference", ""),
        ("array.array", "array", "Array"),
        ("array.object", "object", "Object"),
    ],
)
def test_catalog_arrays_without_ui_metadata_use_native_item_types(
    kind, child_type, type_label
):
    row = native(kind, value="{{producer.native_value}}", defaultValue=False)
    before = copy.deepcopy(row)
    projected = catalog_schema.view(row)
    item = data.children(projected)[0]
    assert item["type"] == child_type
    assert item["type_label"] == type_label
    assert item["name"] == "result.catalog_result_child0"
    assert item["label"] == "Catalog result_child0"
    assert item["parent"] == "result" and item["cannotBeDeleted"] is True
    assert data.children(item) == []
    assert "uiUniqueId" not in item and "fieldFacetMap" not in item
    assert projected["name"] == row["name"]
    assert projected["value"] == row["value"]
    if kind == "array.reference":
        assert data.value_type(item) == data.ValueType("reference", "sys_user")
    if kind == "array.choice":
        assert item["choices"] == CHOICES and item["defaultValue"] is False
    declarations.validate(projected)
    assert row == before


@pytest.mark.parametrize(
    "label,item_label,item_name",
    [
        ("9 Ports", "_9 Ports_child0", "_9_ports_child0"),
        (" Ports", "_ Ports_child0", "_ports_child0"),
        ("\tPorts", "_\tPorts_child0", "_ports_child0"),
        ("Ångström", "Ångström_child0", "_ngstr_m_child0"),
        ("__ A...B", "__ A...B_child0", "_a_b_child0"),
        ("Host\n Name", "Host\n Name_child0", "host_name_child0"),
        ("  ", "_  _child0", "_child0"),
        ("\u0085A", "\u0085A_child0", "_a_child0"),
        ("\ufeffA", "_\ufeffA_child0", "_a_child0"),
    ],
)
def test_catalog_default_item_codec_matches_native_ascii_and_javascript_whitespace(
    label, item_label, item_name
):
    projected = catalog_schema.view(
        native("array.string", name="ExactParent", label=label)
    )
    item = data.children(projected)[0]
    assert projected["name"] == "ExactParent"
    assert item["name"] == "ExactParent." + item_name
    assert item["label"] == item_label
    declarations.validate(projected)


@pytest.mark.parametrize("storage", ["attributes", "fieldFacetMap"])
def test_catalog_optional_packed_fields_survive_without_synthetic_ui_identity(storage):
    packed = {
        "child_name": "ignored.owner.NativeItem",
        "child_label": "Native child label",
        "child_type": "string",
        "child_type_label": "Localized native type",
        "child_defaultValue": "0007\r\n{{untouched.source}}",
        "child_hint": "Native hint",
        "child_maxsize": "00080",
        "child_uiUniqueId": "opaque-child-id",
        "child_vendor_extension": {"native": [False, 0]},
        "vendor": {"keep": [False, 0]},
    }
    row = native("array.string", attributes={})
    row[storage] = copy.deepcopy(packed)
    before = copy.deepcopy(row)
    projected = catalog_schema.view(row)
    item = data.children(projected)[0]
    assert item["name"] == "result.NativeItem"
    assert item["label"] == packed["child_label"]
    for key in ("type_label", "defaultValue", "hint", "maxsize"):
        assert item[key] == packed["child_" + key]
    assert "uiUniqueId" not in item and "fieldFacetMap" not in item
    assert projected[storage] == {"vendor": packed["vendor"]}
    declarations.validate(projected)
    assert row == before


def test_catalog_projection_never_generates_uuids_or_changes_raw_values(monkeypatch):
    def forbidden_uuid():
        raise AssertionError("Catalog read schemas must not generate UI identities")

    monkeypatch.setattr(uuid, "uuid4", forbidden_uuid)
    row = native(
        "array.object",
        uiUniqueId="opaque-parent-not-a-uuid",
        attributes={
            "uiUniqueId": "opaque-parent-not-a-uuid",
            "child_uiUniqueId": "opaque-packed-item",
            "vendor": {"keep": [False, 0, "0001"]},
        },
        value="{{source.rows}}",
        defaultValue="javascript:never_execute()",
        children=[
            native("string", "message", defaultValue="0007\r\n{{source.default}}")
        ],
    )
    wire = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
    projected = catalog_schema.view(row)
    generated = data.children(projected)[0]
    message = data.child(generated, "message")
    assert projected["uiUniqueId"] == "opaque-parent-not-a-uuid"
    assert projected["attributes"]["uiUniqueId"] == "opaque-parent-not-a-uuid"
    assert "uiUniqueId" not in generated
    assert message["defaultValue"] == "0007\r\n{{source.default}}"
    assert projected["defaultValue"] == "javascript:never_execute()"
    projected["attributes"]["vendor"]["keep"].append("local")
    message["defaultValue"] = "local"
    assert json.dumps(row, ensure_ascii=False, separators=(",", ":")) == wire


@pytest.mark.parametrize("qualified", [False, True])
def test_catalog_object_children_recurse_with_native_synthetic_parent_paths(qualified):
    prefix = "payload.rows.entry." if qualified else ""
    tags = native("array.choice", prefix + "tags", parent="payload.rows.entry")
    users = native("array.reference", prefix + "users", parent="payload.rows.entry")
    row = native(
        "object",
        "payload",
        children=[
            native(
                "array.object",
                "rows",
                parent="payload",
                fieldFacetMap={
                    "child_name": "entry",
                    "child_type": "object",
                    "vendor": {"keep": True},
                },
                children=[
                    tags,
                    users,
                    native(
                        "object",
                        prefix + "details",
                        children=[native("string", "message")],
                    ),
                ],
            )
        ],
    )
    before = copy.deepcopy(row)
    projected = catalog_schema.view(row)
    rows = data.child(projected, "rows")
    entry = data.child(rows, "entry")
    projected_tags = data.child(entry, "tags")
    tag = data.children(projected_tags)[0]
    user = data.children(data.child(entry, "users"))[0]
    assert rows["name"] == "rows"
    assert data.child(entry, "tags")["name"] == tags["name"]
    assert tag["name"] == "payload.rows.entry.tags.catalog_result_child0"
    assert tag["parent"] == "payload.rows.entry.tags"
    assert tag["choices"] == CHOICES
    assert data.value_type(user) == data.ValueType("reference", "sys_user")
    assert (
        data.value_type(data.child(data.child(entry, "details"), "message")).kind
        == "string"
    )
    assert rows["fieldFacetMap"] == {"vendor": {"keep": True}}
    declarations.validate(projected)
    assert row == before


@pytest.mark.parametrize(
    "table", ["item_option_new", "item_option_new_set", "sys_flow_cat_variable"]
)
def test_catalog_record_table_provenance_is_not_a_recursive_choice_provider(table):
    row = native(
        "object",
        table=table,
        children=[
            native("choice", "status", table=table),
            native("array.choice", "statuses", table=table),
            native("reference", "owner", table=table, reference="sys_user"),
        ],
    )
    projected = catalog_schema.view(row)
    assert "table" not in projected
    for child in data.children(projected):
        assert "table" not in child
    status = data.child(projected, "status")
    statuses = data.child(projected, "statuses")
    assert declarations.choice_source(status) is None
    assert declarations.choices(status) == CHOICES
    assert declarations.choices(data.children(statuses)[0]) == CHOICES
    assert data.child(projected, "owner")["reference"] == "sys_user"
    assert row["table"] == table and all(
        child["table"] == table for child in row["children"]
    )
    declarations.validate(projected)


def test_catalog_genuine_table_choice_provider_is_preserved_on_parent_and_item():
    row = native(
        "array.choice",
        table="incident",
        columnName="state",
        choiceType="2",
        choices=[],
        defaultChoices=CHOICES,
        defaultValue="closed",
        choiceOption="1",
    )
    projected = catalog_schema.view(row)
    item = data.children(projected)[0]
    for schema in (projected, item):
        assert declarations.choice_source(schema) == {
            "table": "incident",
            "field": "state",
        }
        assert declarations.choices(schema) == CHOICES
        assert schema["defaultValue"] == "closed" and schema["choiceOption"] == "1"
    assert projected["defaultChoices"] is not item["defaultChoices"]
    declarations.validate(projected)


@pytest.mark.parametrize(
    "wrapper,attributes",
    [
        ("$COCollectionField", {"catalogType": "6"}),
        (
            "native_row_schema",
            {"co_type_name": "native_row_schema", "catalogType": "6"},
        ),
    ],
)
def test_catalog_array_object_unwraps_only_native_collection_wrappers(
    wrapper, attributes
):
    row = native(
        "array.object",
        attributes=attributes,
        children=[
            {
                "name": wrapper,
                "children": [
                    native("array.datetime", "times"),
                    native("integer", "count"),
                ],
            }
        ],
    )
    projected = catalog_schema.view(row)
    item = data.children(projected)[0]
    assert (
        data.value_type(data.children(data.child(item, "times"))[0]).kind
        == "glide_date_time"
    )
    assert data.value_type(data.child(item, "count")).kind == "integer"
    with pytest.raises(CapabilityError):
        data.child(item, wrapper)
    declarations.validate(projected)


def test_catalog_parameter_wrapper_is_authoritative_for_root_and_descendants():
    source = native(
        "array.object",
        children=[
            {
                "name": "wrong_outer_name",
                "type": "integer",
                "children": "not a schema",
                "parameter": native("array.string", "words"),
            }
        ],
    )
    wrapped = {"name": "wrong_outer_name", "type": "integer", "parameter": source}
    before = copy.deepcopy(wrapped)
    projected = catalog_schema.view(wrapped)
    assert projected["name"] == "result" and projected["type"] == "array.object"
    words = data.child(data.children(projected)[0], "words")
    assert words["type"] == "array.string"
    assert data.children(words)[0]["parent"] == "result.catalog_result_child0.words"
    declarations.validate(projected)
    assert wrapped == before


def test_catalog_read_view_exposes_pill_types_without_inferring_or_rewriting_values():
    row = native(
        "array.object",
        value="{{producer.rows}}",
        children=[
            native(
                "array.string", "messages", value=[{"not": "an inferred object schema"}]
            ),
            native("glide_date_time", "when", value="0000-native-date-bytes"),
        ],
    )
    projected = catalog_schema.view(row)
    item = data.children(projected)[0]
    messages = data.child(item, "messages")
    assert data.whole_pill(projected["value"]) == "producer.rows"
    assert data.value_type(data.children(messages)[0]).kind == "string"
    assert messages["value"] == [{"not": "an inferred object schema"}]
    assert data.child(item, "when")["value"] == "0000-native-date-bytes"
    data.compatible(
        data.value_type(messages), data.ValueType("array.string"), interpolated=False
    )
    data.compatible(
        data.value_type(data.child(item, "when")),
        data.ValueType("glide_date_time"),
        interpolated=False,
    )
    declarations.validate(projected)


def test_catalog_projection_does_not_relax_generic_packed_declarations():
    row = native(
        "array.string", attributes={"child_name": "native_item", "child_type": "string"}
    )
    with pytest.raises(CapabilityError):
        declarations.unpack(row)
    with pytest.raises(CapabilityError):
        data.children(row)
    projected = catalog_schema.view(row)
    assert data.children(projected)[0]["name"] == "result.native_item"
    assert (
        "child_name" in row["attributes"]
        and "child_name" not in projected["attributes"]
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"children": None},
        {"children": {}},
        {"children": ["not a field"]},
        {"children": [{}]},
        {"children": [{"name": "field"}]},
        {
            "children": [
                {
                    "name": "field",
                    "type": "string",
                    "children": [native("string", "nested")],
                }
            ]
        },
        {"children": [native("string", "field", parent="other_parent")]},
        {"children": [native("string", "another.item.field")]},
        {"children": [native("string", "field"), native("string", "field")]},
        {"attributes": "not metadata"},
        {"fieldFacetMap": []},
        {"attributes": {"child_type": "string"}},
        {"attributes": {"child_name": ""}},
        {"attributes": {"child_name": "owner."}},
        {"attributes": {"child_name": 1}},
        {"attributes": {"child_label": False}},
        {"attributes": {"child_type_label": None}},
        {
            "attributes": {"child_type": "object", "child_name": "one"},
            "fieldFacetMap": {"child_type": "object", "child_name": "two"},
        },
    ],
)
def test_catalog_projection_rejects_malformed_children_and_contradictory_metadata(
    changes,
):
    assert_unavailable(native("array.object", **changes))


@pytest.mark.parametrize(
    "kind",
    [
        "array.string",
        "array.integer",
        "array.boolean",
        "array.datetime",
        "array.reference",
        "array.choice",
        "array.array",
    ],
)
def test_catalog_primitive_arrays_reject_raw_child_registrations(kind):
    assert_unavailable(native(kind, children=[native("string", "unexpected")]))


@pytest.mark.parametrize(
    "changes",
    [
        {"type": "array.unsupported"},
        {"type": ""},
        {"name": ""},
        {"label": None},
        {"attributes": {"child_type": "integer"}},
        {"reference": ""},
    ],
)
def test_catalog_reference_arrays_reject_unknown_or_contradictory_schema(changes):
    assert_unavailable(native("array.reference", **changes))


def test_catalog_projection_requires_complete_complex_children():
    row = native("array.object")
    del row["children"]
    assert_unavailable(row)


def test_catalog_projection_rejects_scalar_children_and_contradictory_known_wrappers():
    assert_unavailable(native("string", children=[native("string", "nested")]))
    assert_unavailable(
        native(
            "array.object",
            children=[
                {
                    "name": "$COCollectionField",
                    "type": "string",
                    "children": [],
                }
            ],
        )
    )
    assert_unavailable(
        native(
            "array.object",
            children=[
                {
                    "name": "$COCollectionField",
                    "type": "object",
                    "children": "not fields",
                }
            ],
        )
    )


def test_catalog_projection_bounds_schema_depth_and_rejects_cycles():
    row = native("object")
    current = row
    for index in range(10):
        child = native("object", "level" + str(index))
        current["children"] = [child]
        current = child
    declarations.validate(catalog_schema.view(row))
    current["children"] = [native("object", "too_deep")]
    assert_unavailable(row)
    cyclic = native("object")
    cyclic["children"] = [cyclic]
    with pytest.raises(CapabilityError) as caught:
        catalog_schema.view(cyclic)
    assert caught.value.code == "UNAVAILABLE"
    assert cyclic["children"][0] is cyclic


@pytest.mark.parametrize("parent_default", [None, "open", False, ""])
def test_catalog_choice_item_defaults_belong_to_the_source_parent(parent_default):
    row = native(
        "array.choice",
        attributes={"child_defaultValue": "packed-but-not-authoritative"},
    )
    if parent_default is not None:
        row["defaultValue"] = parent_default
    projected = catalog_schema.view(row)
    item = data.children(projected)[0]
    if parent_default is None:
        assert "defaultValue" not in item
    else:
        assert item["defaultValue"] == parent_default
    assert item["choices"] == projected["choices"] == CHOICES
    assert item["choices"] is not projected["choices"]


def test_catalog_packed_metadata_uses_native_root_and_nested_storage_precedence():
    root = native(
        "array.object",
        attributes={"child_name": "from_root_attributes"},
        children=[
            native(
                "array.string",
                "words",
                attributes={"child_label": "not the nested item label"},
                fieldFacetMap={"child_name": "from_nested_facets"},
            )
        ],
    )
    projected = catalog_schema.view(root)
    root_item = data.children(projected)[0]
    nested_item = data.children(data.child(root_item, "words"))[0]
    assert root_item["name"] == "result.from_root_attributes"
    assert nested_item["name"] == "result.from_root_attributes.words.from_nested_facets"
    assert nested_item["label"] == "Catalog result_child0"
    declarations.validate(projected)
