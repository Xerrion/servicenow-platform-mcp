"""Consumer tests for the native Decision metadata adapter; no platform effects.

Definitions below are synthetic copies of the installed Decision/Decision Block
contracts (Studio 27), not a claim of captured Decision Table response fixtures.
Every consumer receives deep copies, including the mocked metadata transport.
"""

import copy
import json
from typing import Any

import pytest

from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import data, decisions, editing, tree
from tests.test_flow_editing import DEFS

FLOW = "a" * 32
DECISION = "b" * 32
OTHER_DECISION = "c" * 32
FIRST = "1" * 32
SECOND = "2" * 32
RECORD = "3" * 32
DECISION_DEFINITION = "578e90270b2313009002cab937673a7d"
BRANCH_DEFINITION = "b1f542d673331300612c273ffff6a7c7"


def parameter(name: str, kind: str, **changes: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": f"{sum(name.encode()):032x}",
        "name": name,
        "label": name.replace("_", " ").title(),
        "type": kind,
        "reference": "",
        "readonly": False,
        "mandatory": False,
        "maxsize": 255,
        "defaultValue": "",
        "children": [],
        "attributes": {"fixture_native_schema": {"preserve": [False, 0, "æøå"]}},
    }
    result.update(copy.deepcopy(changes))
    return result


CATALOG: list[dict[str, Any]] = [
    {
        "id": DECISION_DEFINITION,
        "name": "Make a decision",
        "type": "DECISION",
        "connectedTo": "",
        "description": "Fixture Decision Table block",
        "quiescence": "never",
        "compilationClass": "com.glide.flow.compiler.logic_block.DecisionTreeCompiler",
        "order": 7,
        "visible": True,
        "category": "",
        "userCanRead": True,
        "attributes": {
            "allow_children": "false",
            "allow_siblings": "true",
            "can_be_nested": "false",
            "line_type": "q_curve",
        },
        "fixture_definition_extension": {"preserve": [None, "native"]},
        "inputs": [
            parameter("decision_label", "string"),
            parameter(
                "decision_table",
                "reference",
                reference="sys_decision",
                maxsize=32,
                mandatory=True,
            ),
            parameter(
                "execution",
                "choice",
                defaultValue="first_match",
                choices=[
                    {"value": "first_match", "label": "First decision that matches"},
                    {"value": "run_all", "label": "Run all decisions that match"},
                ],
            ),
            parameter("answer_table", "table_name", readonly=True),
            parameter("has_branches", "boolean", defaultValue="1"),
            parameter("include_otherwise", "boolean"),
        ],
        "variables": [
            {
                "name": name,
                "display_name": name.replace("_", " ").title(),
                "type": kind,
                "reference": "",
                "read_only": "false",
                "mandatory": "false",
                "order": "100",
                "attributes": {"native_output": True},
                "internal_link": "" if name == "count" else "answer_table",
            }
            for name, kind in [
                ("answer", "document_id"),
                ("table_name", "table_name"),
                ("ordered_ids", "glide_list"),
                ("records", "records"),
                ("count", "integer"),
            ]
        ],
    },
    {
        "id": BRANCH_DEFINITION,
        "name": "Decision",
        "type": "DECISIONBLOCK",
        "connectedTo": DECISION_DEFINITION,
        "description": "Fixture answer branch",
        "quiescence": "never",
        "compilationClass": "",
        "order": -1,
        "visible": True,
        "category": "",
        "userCanRead": True,
        "attributes": {
            "allow_children": "true",
            "allow_siblings": "false",
            "can_delete": "false",
            "can_move": "false",
            "visible_in_picker": "false",
            "pills_draggable_outside_block": "false",
        },
        "fixture_definition_extension": {"preserve": [False, 0]},
        "inputs": [
            parameter("label", "string"),
            parameter("answer", "document_id", maxsize=32, dependent_on="table"),
        ],
        "variables": [],
    },
]


def native_input(name: str, kind: str, value: Any, **changes: Any) -> dict[str, Any]:
    declared = parameter(name, kind, **changes)
    declared["defaultValue"] = copy.deepcopy(value)
    declared["fixture_parameter_extension"] = {"preserve": ["opaque", False]}
    if "children" not in changes:
        declared.pop("children")
    return declared


TABLE_METADATA: dict[str, Any] = {
    "name": "Insurance decision",
    "active": True,
    "inputs": [
        native_input("age", "integer", "", mandatory=True),
        native_input("is_active", "boolean", False),
        native_input("request", "reference", "", reference="task", maxsize=32),
        native_input(
            "level",
            "choice",
            "basic",
            choices=[
                {"value": "basic", "label": "Basic"},
                {"value": "premium", "label": "Premium"},
            ],
        ),
    ],
    "answerTable": {
        "value": "x_insurance_answer",
        "displayValue": "Insurance answer",
        "native_extra": [False, 0],
    },
    "distinctAnswers": [
        {
            "value": FIRST,
            "label": "Basic",
            "valid": True,
            "native_answer": {"preserve": [0, False]},
        },
        {
            "value": SECOND,
            "label": "Premium",
            "valid": True,
            "native_answer": {"preserve": ["æøå"]},
        },
    ],
    "fixture_response_extension": {"preserve": [None, False, {"zero": 0}]},
}


def serialized(*values):
    return json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@pytest.fixture(autouse=True)
def shared_fixtures_are_immutable():
    before = serialized(CATALOG, TABLE_METADATA)
    yield
    assert serialized(CATALOG, TABLE_METADATA) == before


class MetadataClient:
    def __init__(self) -> None:
        self.catalog = copy.deepcopy(CATALOG)
        self.metadata: dict[str, dict[str, Any]] = {
            DECISION: copy.deepcopy(TABLE_METADATA)
        }
        self.records = {DECISION: self.record(DECISION, TABLE_METADATA["name"])}
        self.table_rows: dict[str, list[dict[str, Any]]] = {}
        self.descriptors: dict[str, dict[str, Any]] = {}
        self.schemas: dict[str, dict[str, Any]] = {}
        self.reads: list[tuple[str, str]] = []

    @staticmethod
    def record(identity: str, name: str) -> dict[str, Any]:
        return {
            "sys_id": identity,
            "name": name,
            "active": "true",
            "sys_class_name": "sys_decision",
            "sys_scope": {"value": "global", "display_value": "Global"},
            "sys_mod_count": "7",
            "sys_updated_on": "2026-10-01 12:00:00",
        }

    async def studio_definitions(self, kind):
        assert kind == "flow_logic"
        self.reads.append(("definitions", kind))
        return copy.deepcopy(self.catalog)

    async def studio_decision_table(self, identity):
        self.reads.append(("decision_metadata", identity))
        return copy.deepcopy(self.metadata[identity])

    async def get_record(self, table, identity, *, fields):
        assert table == "sys_decision"
        assert {"active", "sys_class_name", "sys_scope", "sys_mod_count"}.issubset(
            fields
        )
        self.reads.append(("record", identity))
        return copy.deepcopy(self.records[identity])

    async def query_records(self, table, query, *, fields, limit):
        assert table == "sys_db_object"
        assert query.startswith("name=")
        assert fields == ["name", "label", "sys_id"]
        assert limit == 2
        name = query.removeprefix("name=")
        self.reads.append(("table", name))
        return {
            "records": copy.deepcopy(
                self.table_rows.get(
                    name, [{"name": name, "label": name, "sys_id": "d" * 32}]
                )
            )
        }

    async def studio_table_descriptor(self, table):
        self.reads.append(("descriptor", table))
        return copy.deepcopy(
            self.descriptors.get(
                table,
                {
                    "displayField": "number",
                    "native_descriptor": {"preserve": [0, False]},
                },
            )
        )

    async def studio_table_schema(self, table):
        self.reads.append(("schema", table))
        return copy.deepcopy(self.schemas[table])


def new_decision(client: Any, **inputs: Any) -> tuple[dict[str, Any], tree.Step]:
    flow = {"id": FLOW, "scope": "global"}
    step = tree.new_logic_step(
        FLOW, copy.deepcopy(client.catalog[0]), {"decision_table": DECISION, **inputs}
    )
    step.raw["uiUniqueIdentifier"] = "decision-ui"
    step.raw["fixture_instance_extension"] = {"preserve": [None, "æøå", False]}
    return flow, step


async def configure(
    client: Any,
    flow: dict[str, Any],
    step: tree.Step,
    *,
    decision_inputs=None,
    branches=None,
    delete_children=False,
    seed="fixture-seed",
    new=True,
    previous: tree.Step | None = None,
    script_inputs: set[str] | None = None,
):
    return await decisions.configure(
        step,
        flow,
        copy.deepcopy(client.catalog),
        client,
        decision_inputs=decision_inputs,
        branches=branches,
        delete_children=delete_children,
        seed=seed,
        new=new,
        previous=previous,
        script_inputs=script_inputs,
    )


def ordinary_input(step, name, value):
    data.set_value(data.named(step.raw["inputs"], name), value)
    step.raw.setdefault("data", {})[name] = value


def answer_ids(step):
    return [
        data.named(branch.raw["inputs"], "answer")["value"] for branch in step.children
    ]


def outputs(
    step: tree.Step, roots: list[tree.Step] | None = None
) -> list[dict[str, Any]]:
    result = decisions.output_schema(step, roots)
    assert result is not None
    return result


async def authored(
    *, has_branches=True, include_otherwise=False, execution="first_match"
) -> tuple[Any, dict[str, Any], tree.Step, dict[str, Any]]:
    client = MetadataClient()
    flow, step = new_decision(
        client,
        has_branches=has_branches,
        include_otherwise=include_otherwise,
        execution=execution,
    )
    result = await configure(client, flow, step, decision_inputs={"age": 42})
    return client, flow, step, result


async def test_create_uses_native_input_bindings_derived_table_and_ordered_answer_refs():
    client: Any = MetadataClient()
    flow, step = new_decision(client, include_otherwise=True)
    catalog_before = serialized(client.catalog, client.metadata)
    result = await configure(
        client,
        flow,
        step,
        decision_inputs={
            "age": 42,
            "is_active": False,
            "request": RECORD,
            "level": "premium",
        },
        branches=[
            {"answer": FIRST, "label": "Basic policy", "ref": "basic"},
            {"answer": SECOND, "ref": "premium"},
            {"answer": "", "ref": "unmatched"},
        ],
    )

    assert serialized(client.catalog, client.metadata) == catalog_before
    assert answer_ids(step) == [FIRST, SECOND, ""]
    assert result["created"] == [branch.uid for branch in step.children]
    assert result["removed"] == []
    assert [branch.raw["_alias"] for branch in step.children] == [
        "basic",
        "premium",
        "unmatched",
    ]
    assert [branch.raw["flowLogicDefinition"]["name"] for branch in step.children] == [
        "Basic",
        "Premium",
        "Otherwise",
    ]
    assert all(branch.raw["parent"] == step.uid for branch in step.children)
    assert (
        data.named(step.children[0].raw["inputs"], "label")["value"] == "Basic policy"
    )
    assert [row["value"] for row in step.raw["decisionTableInputs"]] == [
        42,
        False,
        RECORD,
        "premium",
    ]
    assert all(
        row["parameter"] == native
        for row, native in zip(
            step.raw["decisionTableInputs"], TABLE_METADATA["inputs"], strict=True
        )
    )
    assert all(
        row["parameter"]["fixture_parameter_extension"]
        == {"preserve": ["opaque", False]}
        for row in step.raw["decisionTableInputs"]
    )
    assert all(
        set(row) == {"id", "name", "parameter", "value", "displayValue", "scriptActive"}
        for row in step.raw["decisionTableInputs"]
    )
    source = data.named(step.raw["inputs"], "answer_table")
    assert all(
        source[key] == "x_insurance_answer"
        for key in ("value", "reference", "table", "data_format")
    )
    assert source["displayValue"] == source["reference_display"] == "Insurance answer"
    assert source["parameter"]["readonly"] is True
    assert step.raw["data"]["answer_table"] == "x_insurance_answer"
    assert step.raw["decisionTableReference"] == DECISION
    assert (
        step.raw["decisionTableInformation"]["decision_answers"]
        == TABLE_METADATA["distinctAnswers"]
    )
    assert step.raw["childBlockDefinition"] == client.catalog[1]
    assert result["evidence"]["metadata"] == TABLE_METADATA
    before = serialized(step.raw, [branch.raw for branch in step.children])
    validation = await decisions.validate(step, flow, client)
    assert validation["record"]["sys_mod_count"] == "7"
    assert serialized(step.raw, [branch.raw for branch in step.children]) == before


async def test_branch_uids_are_seed_and_answer_deterministic_independent_of_native_order():
    first = MetadataClient()
    flow, step = new_decision(first)
    await configure(first, flow, step, decision_inputs={"age": 18})
    identities = dict(
        zip(answer_ids(step), [branch.uid for branch in step.children], strict=True)
    )
    other = MetadataClient()
    other.metadata[DECISION]["distinctAnswers"].reverse()
    other_flow, other_step = new_decision(other)
    await configure(other, other_flow, other_step, decision_inputs={"age": 19})
    assert (
        dict(
            zip(
                answer_ids(other_step),
                [branch.uid for branch in other_step.children],
                strict=True,
            )
        )
        == identities
    )
    different_flow, different = new_decision(other)
    await configure(
        other,
        different_flow,
        different,
        decision_inputs={"age": 20},
        seed="another-edit",
    )
    assert not set(identities.values()).intersection(
        branch.uid for branch in different.children
    )


async def test_incremental_configuration_preserves_branch_identity_children_values_and_raw_metadata():
    client, flow, step, _ = await authored()
    branch = step.children[0]
    branch.raw["id"] = "4" * 32
    branch.raw["fixture_native_instance"] = {"preserve": [False, 0, "æøå"]}
    child = tree.Step(
        {"uiUniqueIdentifier": "nested-action", "native_opaque": [None, False]},
        "action",
    )
    branch.children.append(child)
    step.raw["decisionTableInformation"]["fixture_saved_information"] = {
        "preserve": [0]
    }
    untouched = copy.deepcopy(step.children[1].raw)
    age = data.named(step.raw["decisionTableInputs"], "age")
    age["fixture_saved_binding"] = {"preserve": [False, 0]}
    result = await configure(
        client,
        flow,
        step,
        decision_inputs={"age": 43},
        branches=[
            {"answer": FIRST, "label": "Renewed label", "ref": "renewed"},
            {"answer": SECOND},
        ],
        new=False,
    )
    assert result["created"] == result["removed"] == []
    assert step.children[0] is branch
    assert branch.children == [child]
    assert branch.children[0] is child
    assert branch.raw["id"] == "4" * 32
    assert branch.raw["fixture_native_instance"] == {"preserve": [False, 0, "æøå"]}
    assert branch.raw["_alias"] == "renewed"
    assert step.children[1].raw == untouched
    assert data.named(step.raw["decisionTableInputs"], "age")[
        "fixture_saved_binding"
    ] == {"preserve": [False, 0]}
    assert data.named(step.raw["decisionTableInputs"], "age")["value"] == 43
    assert step.raw["decisionTableInformation"]["fixture_saved_information"] == {
        "preserve": [0]
    }
    await decisions.validate(step, flow, client)


async def test_otherwise_toggle_is_last_and_requires_explicit_child_deletion():
    client, flow, step, _ = await authored()
    original = step.children.copy()
    ordinary_input(step, "include_otherwise", True)
    result = await configure(client, flow, step, new=False)
    otherwise = step.children[-1]
    assert step.children[:2] == original
    assert answer_ids(step) == [FIRST, SECOND, ""]
    assert result["created"] == [otherwise.uid]
    descendant = tree.Step({"uiUniqueIdentifier": "otherwise-action"}, "action")
    otherwise.children.append(descendant)
    ordinary_input(step, "include_otherwise", False)
    before = copy.deepcopy(step)
    with pytest.raises(CapabilityError, match="children='delete'"):
        await configure(client, flow, step, new=False)
    assert step == before
    removed = await configure(client, flow, step, new=False, delete_children=True)
    assert removed["removed"] == [otherwise, descendant]
    assert step.children == original
    assert removed["created"] == []


async def test_disabling_populated_branches_requires_explicit_descendant_deletion():
    client, flow, step, _ = await authored()
    original = step.children.copy()
    descendant = tree.Step({"uiUniqueIdentifier": "populated-answer-action"}, "action")
    original[0].children.append(descendant)
    ordinary_input(step, "has_branches", False)
    with pytest.raises(CapabilityError, match="children='delete'"):
        await configure(client, flow, step, new=False)
    assert step.children == original
    result = await configure(client, flow, step, new=False, delete_children=True)
    assert result["removed"] == [original[0], descendant, original[1]]
    assert step.children == []
    assert [row["name"] for row in outputs(step)] == ["answer", "table_name"]
    previous = copy.deepcopy(step)
    ordinary_input(step, "has_branches", True)
    recreated = await configure(client, flow, step, new=False, previous=previous)
    assert recreated["created"] == [branch.uid for branch in original]
    assert answer_ids(step) == [FIRST, SECOND]


@pytest.mark.parametrize(
    "delete_children,values",
    [(False, None), (True, None), (False, {"age": 8}), (True, {"age": 8})],
)
async def test_table_change_requires_explicit_deletion_and_complete_new_rebinding(
    delete_children, values
):
    client, flow, step, _ = await authored()
    client.metadata[OTHER_DECISION] = copy.deepcopy(TABLE_METADATA)
    client.metadata[OTHER_DECISION]["name"] = "Another decision"
    client.records[OTHER_DECISION] = client.record(OTHER_DECISION, "Another decision")
    ordinary_input(step, "decision_table", OTHER_DECISION)
    before = copy.deepcopy(step)
    with pytest.raises(CapabilityError, match="explicit|rebind"):
        await configure(
            client,
            flow,
            step,
            new=False,
            delete_children=delete_children,
            decision_inputs=values,
        )
    assert step == before


async def test_table_replacement_uses_only_new_schema_values_and_reports_old_subtrees():
    client, flow, step, _ = await authored()
    original = step.children.copy()
    descendant = tree.Step({"uiUniqueIdentifier": "old-action"}, "action")
    original[0].children.append(descendant)
    new_metadata = copy.deepcopy(TABLE_METADATA)
    new_metadata.update(
        name="Different decision",
        inputs=[native_input("new_amount", "integer", "", mandatory=True)],
        distinctAnswers=[{"value": RECORD, "label": "Replacement", "valid": True}],
    )
    client.metadata[OTHER_DECISION] = new_metadata
    client.records[OTHER_DECISION] = client.record(OTHER_DECISION, new_metadata["name"])
    ordinary_input(step, "decision_table", OTHER_DECISION)
    result = await configure(
        client,
        flow,
        step,
        new=False,
        delete_children=True,
        decision_inputs={"new_amount": 0},
    )
    assert result["removed"] == [original[0], descendant, original[1]]
    assert [row["name"] for row in step.raw["decisionTableInputs"]] == ["new_amount"]
    assert step.raw["decisionTableInputs"][0]["value"] == 0
    assert answer_ids(step) == [RECORD]
    await decisions.validate(step, flow, client)


@pytest.mark.parametrize(
    "has_branches,execution,names",
    [
        (False, "first_match", ["answer", "table_name"]),
        (False, "run_all", ["table_name", "ordered_ids", "records", "count"]),
        (True, "first_match", []),
        (True, "run_all", []),
    ],
)
async def test_output_projection_matches_native_execution_and_branch_mode(
    has_branches, execution, names
):
    client, flow, step, _ = await authored(
        has_branches=has_branches, execution=execution
    )
    before = serialized(step.raw, client.catalog)
    projected = outputs(step)
    assert [row["name"] for row in projected] == names
    for output in projected:
        if output["name"] != "count":
            assert output["reference"] == "x_insurance_answer"
        if output["name"] == "answer":
            assert output["type"] == "reference"
            assert output["attributes"]["decision_table"] == DECISION
            assert output["attributes"]["native_output"] is True
    if has_branches:
        for branch in step.children:
            branch_outputs = outputs(branch, [step])
            assert [row["name"] for row in branch_outputs] == ["answer", "table_name"]
            assert branch_outputs[0]["attributes"]["decision_table"] == DECISION
            assert branch_outputs[0]["reference"] == "x_insurance_answer"
        with pytest.raises(CapabilityError, match="parent"):
            decisions.output_schema(step.children[0])
    assert serialized(step.raw, client.catalog) == before
    await decisions.validate(step, flow, client)


async def test_readback_parameter_wrappers_and_redundant_fields_are_not_schema_drift():
    client, flow, step, _ = await authored()
    for row in step.raw["decisionTableInputs"]:
        row.update(copy.deepcopy(row["parameter"]))
        row["fixture_readback_metadata"] = {"preserve": [False, 0]}
    # Native parameter rows can also be returned flat instead of wrapped.
    flat = step.raw["decisionTableInputs"][1]
    flat.pop("parameter")
    flat.pop("fixture_readback_metadata")
    before = copy.deepcopy(step)
    await decisions.validate(step, flow, client)
    assert step == before
    await configure(client, flow, step, decision_inputs={"age": 44}, new=False)
    assert step.raw["decisionTableInputs"][0]["fixture_readback_metadata"] == {
        "preserve": [False, 0]
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("type", "string"),
        ("mandatory", False),
        ("maxsize", 12),
        ("reference", "incident"),
        ("attributes", {"changed": True}),
    ],
)
async def test_current_table_input_schema_drift_is_rejected_without_repair(
    field, value
):
    client, flow, step, _ = await authored()
    native = client.metadata[DECISION]["inputs"][0]
    native[field] = copy.deepcopy(value)
    before = copy.deepcopy(step)
    with pytest.raises(CapabilityError, match="schema"):
        await decisions.validate(step, flow, client)
    assert step == before
    with pytest.raises(CapabilityError, match="schema"):
        await configure(
            client,
            flow,
            step,
            new=False,
            delete_children=True,
            decision_inputs={"age": 18},
        )
    assert step == before


@pytest.mark.parametrize(
    "defect",
    [
        "missing_answer",
        "new_answer",
        "changed_label",
        "missing_saved_input",
        "missing_reference",
        "wrong_answer_table",
        "missing_branch",
        "reordered_branch",
        "stale_branch_answer",
        "branch_answer_table",
        "foreign_child",
        "definition_drift",
    ],
)
async def test_disappearing_or_stale_saved_metadata_is_not_silently_cleared(defect):
    client, flow, step, _ = await authored()
    if defect == "missing_answer":
        client.metadata[DECISION]["distinctAnswers"].pop()
    elif defect == "new_answer":
        client.metadata[DECISION]["distinctAnswers"].append(
            {"value": RECORD, "label": "New answer"}
        )
    elif defect == "changed_label":
        client.metadata[DECISION]["distinctAnswers"][0]["label"] = "Renamed answer"
    elif defect == "missing_saved_input":
        step.raw["decisionTableInputs"].pop()
    elif defect == "missing_reference":
        step.raw.pop("decisionTableReference")
    elif defect == "wrong_answer_table":
        data.named(step.raw["inputs"], "answer_table")["value"] = "incident"
    elif defect == "missing_branch":
        step.children.pop()
    elif defect == "reordered_branch":
        step.children.reverse()
    elif defect == "stale_branch_answer":
        data.named(step.children[0].raw["inputs"], "answer")["value"] = RECORD
    elif defect == "branch_answer_table":
        data.named(step.children[0].raw["inputs"], "answer")["reference"] = "incident"
    elif defect == "foreign_child":
        step.children.append(
            tree.Step({"uiUniqueIdentifier": "foreign-action"}, "action")
        )
    else:
        client.catalog[0]["fixture_definition_extension"]["preserve"].append("drift")
    before = copy.deepcopy(step)
    with pytest.raises(CapabilityError):
        await decisions.validate(step, flow, client)
    assert step == before
    with pytest.raises(CapabilityError):
        await configure(client, flow, step, new=False, delete_children=True)
    assert step == before


@pytest.mark.parametrize(
    "defect",
    [
        "wrapped_response",
        "missing_inputs",
        "duplicate_input",
        "bad_parameter",
        "bad_children",
        "runtime_binding",
        "missing_id",
        "scripted",
        "bad_table",
        "missing_table_display",
        "duplicate_answer",
        "bad_answer_id",
        "missing_answer_label",
        "invalid_answer",
        "invalid_answer_element",
        "inactive",
        "inactive_api",
        "delta",
        "missing_revision",
        "missing_scope",
        "ambiguous_table",
        "missing_descriptor",
    ],
)
async def test_malformed_native_metadata_is_rejected_before_creating_branches(defect):
    client = MetadataClient()
    flow, step = new_decision(client)
    metadata = client.metadata[DECISION]
    if defect == "wrapped_response":
        client.metadata[DECISION] = {"data": metadata}
    elif defect == "missing_inputs":
        metadata.pop("inputs")
    elif defect == "duplicate_input":
        metadata["inputs"].append(copy.deepcopy(metadata["inputs"][0]))
    elif defect == "bad_parameter":
        metadata["inputs"][0]["parameter"] = []
    elif defect == "bad_children":
        metadata["inputs"][0]["children"] = {}
    elif defect == "runtime_binding":
        metadata["inputs"][0]["value"] = ""
    elif defect == "missing_id":
        metadata["inputs"][0].pop("id")
    elif defect == "scripted":
        metadata["inputs"][0]["scriptActive"] = True
    elif defect == "bad_table":
        metadata["answerTable"]["value"] = "task^ORactive=true"
    elif defect == "missing_table_display":
        metadata["answerTable"].pop("displayValue")
    elif defect == "duplicate_answer":
        metadata["distinctAnswers"].append(
            copy.deepcopy(metadata["distinctAnswers"][0])
        )
    elif defect == "bad_answer_id":
        metadata["distinctAnswers"][0]["value"] = "not-a-sys-id"
    elif defect == "missing_answer_label":
        metadata["distinctAnswers"][0].pop("label")
    elif defect == "invalid_answer":
        metadata["distinctAnswers"][0]["valid"] = False
    elif defect == "invalid_answer_element":
        metadata["distinctAnswers"][0]["answerElementValues"] = [{"valid": False}]
    elif defect == "inactive_api":
        metadata["active"] = False
    elif defect == "inactive":
        client.records[DECISION]["active"] = "false"
    elif defect == "delta":
        client.records[DECISION]["sys_class_name"] = "sys_decision_delta"
    elif defect == "missing_revision":
        client.records[DECISION].pop("sys_mod_count")
    elif defect == "missing_scope":
        client.records[DECISION].pop("sys_scope")
    elif defect == "ambiguous_table":
        client.table_rows["x_insurance_answer"] = []
    else:
        client.descriptors["x_insurance_answer"] = {}
    before = copy.deepcopy(step)
    with pytest.raises((CapabilityError, ValueError)):
        await configure(client, flow, step, decision_inputs={"age": 18})
    assert step == before
    assert step.children == []


@pytest.mark.parametrize(
    "values",
    [
        {"unknown": 1},
        {"age": True},
        {"age": 18, "is_active": "false"},
        {"age": 18, "request": "not-a-sys-id"},
        {"age": 18, "level": "not-a-choice"},
        {},
    ],
)
async def test_native_typed_inputs_reject_unknown_wrong_type_and_missing_required_values(
    values,
):
    client = MetadataClient()
    flow, step = new_decision(client)
    before = copy.deepcopy(step)
    with pytest.raises((CapabilityError, ValueError)):
        await configure(client, flow, step, decision_inputs=values)
    assert step == before


@pytest.mark.parametrize(
    "branches",
    [
        [],
        [{"answer": SECOND}, {"answer": FIRST}],
        [{"answer": FIRST}],
        [{"answer": FIRST}, {"answer": SECOND}, {"answer": ""}],
        [{"answer": FIRST, "ref": "same"}, {"answer": SECOND, "ref": "same"}],
        [{"answer": FIRST, "unknown": True}, {"answer": SECOND}],
        [{"answer": FIRST, "label": "x" * 256}, {"answer": SECOND}],
    ],
)
async def test_branch_configuration_requires_complete_native_order_and_valid_labels_refs(
    branches,
):
    client = MetadataClient()
    flow, step = new_decision(client)
    before = copy.deepcopy(step)
    with pytest.raises(CapabilityError):
        await configure(
            client, flow, step, decision_inputs={"age": 18}, branches=branches
        )
    assert step == before


async def test_run_all_incremental_execution_change_reprojects_outputs_without_branches():
    client, flow, step, _ = await authored(has_branches=False)
    ordinary_input(step, "execution", "run_all")
    result = await configure(client, flow, step, new=False)
    assert result["created"] == result["removed"] == []
    assert {row["name"] for row in outputs(step)} == {
        "table_name",
        "ordered_ids",
        "records",
        "count",
    }
    await decisions.validate(step, flow, client)


async def test_reference_table_dependency_is_native_typed_and_cannot_reuse_stale_record_value():
    client: Any = MetadataClient()
    client.metadata[DECISION]["inputs"] = [
        native_input("table", "table_name", "task"),
        native_input("record", "document_id", "", dependent_on="table"),
    ]
    flow, step = new_decision(client)
    await configure(client, flow, step, decision_inputs={"record": RECORD})
    dependent = data.named(step.raw["decisionTableInputs"], "record")
    assert all(
        dependent[key] == "task" for key in ("reference", "table", "data_format")
    )
    before = copy.deepcopy(step)
    with pytest.raises(CapabilityError, match="rebinding"):
        await configure(
            client, flow, step, decision_inputs={"table": "incident"}, new=False
        )
    assert step == before
    result = await configure(
        client,
        flow,
        step,
        decision_inputs={"table": "incident", "record": FIRST},
        new=False,
    )
    assert "incident" in result["evidence"]["dependencies"]
    assert (
        data.named(step.raw["decisionTableInputs"], "record")["reference"] == "incident"
    )
    await decisions.validate(step, flow, client)
    data.named(step.raw["decisionTableInputs"], "record")["reference"] = "task"
    with pytest.raises(CapabilityError, match="dependent"):
        await decisions.validate(step, flow, client)


async def test_revision_and_opaque_metadata_changes_are_in_dependency_evidence_without_mutation():
    client, flow, step, _ = await authored()
    first = await decisions.validate(step, flow, client)
    client.records[DECISION]["sys_mod_count"] = "8"
    client.metadata[DECISION]["fixture_response_extension"]["preserve"].append("drift")
    second = await decisions.validate(step, flow, client)
    assert first != second
    assert second["record"]["sys_mod_count"] == "8"
    assert second["metadata"]["fixture_response_extension"]["preserve"][-1] == "drift"
    assert first["record"]["sys_mod_count"] == "7"


async def test_only_decision_parents_can_generate_native_branches():
    client, flow, step, _ = await authored()
    branch = step.children[0]
    before = copy.deepcopy(branch)
    with pytest.raises(CapabilityError, match="Only a native Decision"):
        await configure(client, flow, branch, decision_inputs={"age": 18}, new=False)
    assert branch == before
    assert (
        decisions.output_schema(
            tree.Step({"uiUniqueIdentifier": "action-ui"}, "action")
        )
        is None
    )


async def test_otherwise_without_branches_and_dynamic_decision_identity_are_rejected():
    client = MetadataClient()
    flow, step = new_decision(client, has_branches=False, include_otherwise=True)
    with pytest.raises(CapabilityError, match="Use Branches"):
        await configure(client, flow, step, decision_inputs={"age": 18})
    flow, step = new_decision(client)
    data.named(step.raw["inputs"], "decision_table")["value"] = "{{producer.decision}}"
    with pytest.raises((CapabilityError, ValueError)):
        await configure(client, flow, step, decision_inputs={"age": 18})


@pytest.mark.parametrize("mode", ["branches", "otherwise"])
async def test_empty_native_wrappers_can_be_removed_without_authorizing_action_deletion(
    mode,
):
    client, flow, step, _ = await authored(include_otherwise=mode == "otherwise")
    previous = copy.deepcopy(step)
    original = step.children.copy()
    ordinary_input(
        step, "has_branches" if mode == "branches" else "include_otherwise", False
    )
    result = await configure(client, flow, step, new=False, previous=previous)
    assert result["removed"] == (original if mode == "branches" else [original[-1]])
    assert all(not branch.children for branch in result["removed"])
    await decisions.validate(step, flow, client)


async def test_enabling_branches_uses_explicit_previous_mode_instead_of_repairing_lost_children():
    client, flow, step, _ = await authored(has_branches=False)
    previous = copy.deepcopy(step)
    ordinary_input(step, "has_branches", True)
    with pytest.raises(CapabilityError, match="prior Decision snapshot"):
        await configure(client, flow, step, new=False)
    result = await configure(client, flow, step, new=False, previous=previous)
    assert result["created"] == [branch.uid for branch in step.children]
    assert answer_ids(step) == [FIRST, SECOND]
    step.children.clear()
    malformed_previous = copy.deepcopy(step)
    with pytest.raises(CapabilityError, match="prior decision branches"):
        await configure(client, flow, step, new=False, previous=malformed_previous)
    assert step.children == []


async def test_empty_table_replacement_needs_rebinding_but_not_child_deletion_permission():
    client, flow, step, _ = await authored()
    previous = copy.deepcopy(step)
    original = step.children.copy()
    client.metadata[OTHER_DECISION] = copy.deepcopy(TABLE_METADATA)
    client.metadata[OTHER_DECISION]["name"] = "Other decision"
    client.records[OTHER_DECISION] = client.record(OTHER_DECISION, "Other decision")
    ordinary_input(step, "decision_table", OTHER_DECISION)
    result = await configure(
        client,
        flow,
        step,
        new=False,
        previous=previous,
        seed="replacement-edit",
        decision_inputs={
            "age": 0,
            "is_active": False,
            "request": "",
            "level": "basic",
        },
    )
    assert result["removed"] == original
    assert data.named(step.raw["decisionTableInputs"], "age")["value"] == 0
    await decisions.validate(step, flow, client)


async def test_native_complex_inputs_use_typed_children_and_reject_readback_value_drift():
    client: Any = MetadataClient()
    client.metadata[DECISION]["inputs"] = [
        native_input(
            "profile",
            "object",
            "",
            children=[parameter("enabled", "boolean", mandatory=True)],
        ),
        native_input(
            "tags", "array.string", "", children=[parameter("item", "string")]
        ),
    ]
    flow, step = new_decision(client)
    await configure(
        client,
        flow,
        step,
        decision_inputs={"profile": {"enabled": False}, "tags": ["one", "æøå"]},
    )
    profile = data.named(step.raw["decisionTableInputs"], "profile")
    tags = data.named(step.raw["decisionTableInputs"], "tags")
    assert profile["value"] == tags["value"] == ""
    assert profile["children"][0]["value"] is False
    assert [item["value"] for item in tags["children"]] == ["one", "æøå"]
    await decisions.validate(step, flow, client)
    profile["children"][0]["value"] = "false"
    with pytest.raises(CapabilityError, match="Boolean"):
        await decisions.validate(step, flow, client)


@pytest.mark.parametrize(
    "kind,default,expected", [("integer", 0, 0), ("boolean", "false", False)]
)
async def test_readonly_native_input_values_are_preserved_and_not_caller_rebound(
    kind, default, expected
):
    client: Any = MetadataClient()
    client.metadata[DECISION]["inputs"] = [
        native_input("locked", kind, default, readonly=True)
    ]
    flow, step = new_decision(client)
    await configure(client, flow, step)
    assert step.raw["decisionTableInputs"][0]["value"] == expected
    with pytest.raises(CapabilityError, match="read-only"):
        await configure(client, flow, step, new=False, decision_inputs={"locked": 1})
    await decisions.validate(step, flow, client)


async def test_new_script_target_exempts_mandatory_basic_value_without_faking_a_default():
    client: Any = MetadataClient()
    flow, step = new_decision(client)
    await configure(client, flow, step, script_inputs={"age"})
    age = data.named(step.raw["decisionTableInputs"], "age")
    assert age["value"] == ""
    assert age["parameter"]["mandatory"] is True
    # The parent common-input adapter applies this native map after configure.
    age.update(
        scriptActive=True,
        script={
            "age": {"script": "return 42;", "scriptActive": True, "showScript": True}
        },
    )
    before = copy.deepcopy(step)
    await decisions.validate(step, flow, client)
    await configure(client, flow, step, new=False)
    assert step == before
    age = data.named(step.raw["decisionTableInputs"], "age")
    age["script"] = {}
    with pytest.raises(CapabilityError, match="script map"):
        await decisions.validate(step, flow, client)


@pytest.mark.parametrize(
    "name",
    [
        "decision_table",
        "execution",
        "has_branches",
        "include_otherwise",
        "answer_table",
        "branch_answer",
    ],
)
async def test_static_topology_inputs_cannot_hide_runtime_scripts_behind_basic_values(
    name,
):
    client, flow, step, _ = await authored()
    item = (
        data.named(step.children[0].raw["inputs"], "answer")
        if name == "branch_answer"
        else data.named(step.raw["inputs"], name)
    )
    item.update(
        scriptActive=True,
        script={item["name"]: {"script": "return 'different';", "scriptActive": True}},
    )
    with pytest.raises(CapabilityError, match="static|cannot be scripted"):
        await decisions.validate(step, flow, client)


async def test_derived_answer_table_cannot_be_supplied_when_constructing_a_decision():
    client = MetadataClient()
    flow, step = new_decision(client, answer_table="incident")
    with pytest.raises(CapabilityError, match="derived"):
        await configure(client, flow, step, decision_inputs={"age": 18})


@pytest.mark.parametrize(
    "defect",
    [
        "missing_branch",
        "branch_movable",
        "unreadable",
        "wrong_parent",
        "wrong_reference",
        "writable_answer_table",
        "missing_outputs",
    ],
)
async def test_native_definition_contract_is_required_before_any_branch_is_constructed(
    defect,
):
    client = MetadataClient()
    if defect == "missing_branch":
        client.catalog.pop()
    elif defect == "branch_movable":
        client.catalog[1]["attributes"]["can_move"] = "true"
    elif defect == "unreadable":
        client.catalog[0]["userCanRead"] = False
    elif defect == "wrong_parent":
        client.catalog[1]["connectedTo"] = OTHER_DECISION
    elif defect == "wrong_reference":
        data.named(client.catalog[0]["inputs"], "decision_table")["reference"] = (
            "sys_user"
        )
    elif defect == "writable_answer_table":
        data.named(client.catalog[0]["inputs"], "answer_table")["readonly"] = False
    else:
        client.catalog[0]["variables"] = []
    flow, step = new_decision(client)
    with pytest.raises(CapabilityError):
        await configure(client, flow, step, decision_inputs={"age": 18})
    assert step.children == []


async def test_fresh_native_serializer_preserves_family_metadata_and_validates_sparse_readback():
    client, flow, step, _ = await authored(include_otherwise=True)
    step.raw["decisionTableInformation"]["fixture_api_only"] = {
        "preserve_in_editor": [False, 0]
    }
    original = copy.deepcopy(step)
    native_step = decisions.serialize_new(step)
    assert step == original
    assert native_step["decisionTableReference"] == DECISION
    assert native_step["decisionTableInputs"] == step.raw["decisionTableInputs"]
    assert native_step["childBlockDefinition"] == client.catalog[1]
    assert "table" not in native_step["decisionTableInformation"]
    assert native_step["decisionTableInformation"]["active"] == "true"
    assert all(
        answer["valid"] is True
        for answer in native_step["decisionTableInformation"]["decision_answers"]
    )
    assert all("id" not in row for row in native_step["inputs"])
    assert "fixture_api_only" not in native_step["decisionTableInformation"]
    assert all(
        "native_answer" not in answer
        for answer in native_step["decisionTableInformation"]["decision_answers"]
    )
    assert not {"data", "isNew", "isDirty", "validation_errors"}.intersection(
        native_step
    )
    assert (
        native_step["fixture_instance_extension"]
        == step.raw["fixture_instance_extension"]
    )
    assert all(
        not {"field_name", "data_format", "reference", "table"}.intersection(row)
        for row in native_step["inputs"]
    )
    step.raw = native_step
    for branch in step.children:
        before = copy.deepcopy(branch)
        branch.raw = decisions.serialize_new(branch)
        assert (
            data.named(branch.raw["inputs"], "answer")["value"]
            == data.named(before.raw["inputs"], "answer")["value"]
        )
        assert "value" not in data.named(
            branch.raw["flowLogicDefinition"]["inputs"], "answer"
        )
        assert "value" not in data.named(branch.raw["inputs"], "answer")["parameter"]
    before = copy.deepcopy(step)
    evidence = await decisions.validate(step, flow, client)
    assert evidence["metadata"]["distinctAnswers"] == TABLE_METADATA["distinctAnswers"]
    assert step == before
    assert outputs(step) == []
    assert outputs(step.children[0], [step])[0]["reference"] == "x_insurance_answer"


async def test_existing_sparse_readback_keeps_native_answer_extensions_and_absent_ui_mirrors():
    client, flow, step, _ = await authored()
    step.raw = decisions.serialize_new(step)
    for branch in step.children:
        branch.raw = decisions.serialize_new(branch)
        branch.raw["flowLogicDefinition"]["name"] = client.catalog[1]["name"]
    step.raw["id"] = "8" * 32
    information = step.raw["decisionTableInformation"]
    information["fixture_saved_information"] = {"opaque": [False, 0]}
    information["decision_answers"][0]["fixture_saved_answer"] = {"opaque": ["æøå"]}
    before_info = copy.deepcopy(information)
    await decisions.validate(step, flow, client)
    await configure(
        client,
        flow,
        step,
        new=False,
        previous=copy.deepcopy(step),
        decision_inputs={"age": 45},
        branches=[
            {"answer": FIRST, "label": "Updated label"},
            {"answer": SECOND},
        ],
    )
    assert step.raw["decisionTableInformation"] == before_info
    assert "data" not in step.raw
    assert "data" not in step.children[0].raw
    assert "reference" not in data.named(step.raw["inputs"], "answer_table")
    assert "reference" not in data.named(step.children[0].raw["inputs"], "answer")
    with pytest.raises(CapabilityError, match="newly created"):
        decisions.serialize_new(step)


async def test_switching_existing_script_to_basic_mode_validates_prior_then_new_value():
    client, flow, step, _ = await authored()
    age = data.named(step.raw["decisionTableInputs"], "age")
    age.update(
        value="",
        scriptActive=True,
        script={
            "age": {"script": "return 42;", "scriptActive": True, "showScript": True}
        },
    )
    previous = copy.deepcopy(step)
    # Native prepare disables the map before the Decision metadata setter runs.
    age["scriptActive"] = False
    age["script"]["age"].update(scriptActive=False, showScript=False)
    await configure(
        client, flow, step, new=False, previous=previous, decision_inputs={"age": 19}
    )
    assert data.named(step.raw["decisionTableInputs"], "age")["value"] == 19
    await decisions.validate(step, flow, client)


async def test_required_boolean_without_native_default_is_not_implicitly_false():
    client: Any = MetadataClient()
    client.metadata[DECISION]["inputs"] = [
        native_input("flag", "boolean", "", mandatory=True)
    ]
    flow, step = new_decision(client)
    with pytest.raises(CapabilityError, match="explicit input"):
        await configure(client, flow, step)
    await configure(client, flow, step, decision_inputs={"flag": False})
    assert step.raw["decisionTableInputs"][0]["value"] is False
    await decisions.validate(step, flow, client)


async def test_native_answer_element_validity_and_zero_values_survive_new_serialization():
    client: Any = MetadataClient()
    element = {
        "answerElementName": "premium",
        "label": "Premium",
        "table": "x_insurance_answer",
        "type": "integer",
        "value": 0,
        "valid": True,
        "fixture_api_only": [False, 0],
    }
    client.metadata[DECISION]["distinctAnswers"][0]["answerElementValues"] = [element]
    flow, step = new_decision(client)
    await configure(client, flow, step, decision_inputs={"age": 18})
    dto = decisions.serialize_new(step)
    saved = dto["decisionTableInformation"]["decision_answers"][0][
        "answerElementValues"
    ][0]
    assert saved["value"] == 0
    assert saved["valid"] is True
    assert "fixture_api_only" not in saved
    assert (
        step.raw["decisionTableInformation"]["decision_answers"][0][
            "answerElementValues"
        ][0]
        == element
    )
    step.raw = dto
    await decisions.validate(step, flow, client)


async def test_answer_fields_use_exact_native_result_elements_virtual_schema():
    client: Any = MetadataClient()
    virtual = "var__m_sys_decision_multi_result_element_" + DECISION
    schema: dict[str, Any] = {
        "table": virtual,
        "columns": [
            {
                "definition": parameter("premium", "integer"),
                "native_column": {"preserve": [False, 0]},
            },
            {"definition": parameter("owner", "reference", reference="sys_user")},
        ],
        "native_schema": {"preserve": [None, "æøå"]},
    }
    client.schemas[virtual] = copy.deepcopy(schema)
    result = await decisions.answer_fields(DECISION, client)
    assert client.reads == [("schema", virtual)]
    assert result["table"] == virtual
    assert result["fields"] == [column["definition"] for column in schema["columns"]]
    assert result["schema"] == schema
    assert data.value_type(result["fields"][0]).kind == "integer"
    assert data.value_type(result["fields"][1]).reference == "sys_user"
    result["fields"][0]["attributes"]["fixture_native_schema"]["preserve"].append(
        "changed"
    )
    assert client.schemas[virtual] == schema
    assert result["schema"] == schema


@pytest.mark.parametrize(
    "defect",
    [
        "wrong_table",
        "missing_columns",
        "missing_definition",
        "duplicate_name",
        "missing_type",
    ],
)
async def test_native_answer_field_schema_drift_is_not_guessed(defect):
    client: Any = MetadataClient()
    virtual = "var__m_sys_decision_multi_result_element_" + DECISION
    schema: dict[str, Any] = {
        "table": virtual,
        "columns": [{"definition": parameter("premium", "integer")}],
    }
    if defect == "wrong_table":
        schema["table"] = "var__m_sys_decision_multi_result_element_" + OTHER_DECISION
    elif defect == "missing_columns":
        schema.pop("columns")
    elif defect == "missing_definition":
        schema["columns"] = [{}]
    elif defect == "duplicate_name":
        schema["columns"].append(copy.deepcopy(schema["columns"][0]))
    else:
        schema["columns"][0]["definition"].pop("type")
    client.schemas[virtual] = schema
    with pytest.raises(CapabilityError):
        await decisions.answer_fields(DECISION, client)


async def resolver_decision(*, run_all: bool = False):
    client: Any = MetadataClient()
    client.metadata[DECISION]["answerTable"] = {
        "value": "sys_decision_multi_result",
        "displayValue": "Decision multi result",
    }
    virtual = "var__m_sys_decision_multi_result_element_" + DECISION
    client.schemas[virtual] = {
        "table": virtual,
        "columns": [
            {
                "name": "result",
                "label": "Result",
                "definition": parameter(
                    "result",
                    "choice",
                    choices=[{"value": "approved", "label": "Approved"}],
                ),
            },
            {
                "name": "owner",
                "label": "Owner",
                "definition": parameter("owner", "reference", reference="sys_user"),
            },
        ],
    }
    flow, step = new_decision(
        client,
        has_branches=not run_all,
        execution="run_all" if run_all else "first_match",
    )
    await configure(client, flow, step, decision_inputs={"age": 18})
    roots = [step]
    if run_all:
        definition = copy.deepcopy(
            next(row for row in DEFS["flow_logic"] if row["type"] == "FOREACH")
        )
        producer = tree.new_logic_step(
            FLOW, definition, {"items": "{{" + step.uid + ".records}}"}
        )
        producer.raw.update(uiUniqueIdentifier="answer-loop", order=2, parent="")
        roots.append(producer)
        prefix = producer.uid + ".item"
    else:
        producer = step.children[0]
        prefix = producer.uid + ".answer"
    step.raw["order"] = 1
    producer.raw["order"] = 2
    consumer = tree.Step(
        {"uiUniqueIdentifier": "answer-consumer", "order": 3, "parent": producer.uid},
        "action",
    )
    producer.children.append(consumer)
    for branch in step.children:
        branch.raw.setdefault("order", 2)
    return (
        client,
        editing.PillResolver(client, None, flow, roots),
        prefix,
        consumer,
        virtual,
    )


@pytest.mark.parametrize("run_all", [False, True])
async def test_native_branch_answer_and_run_all_loop_item_resolve_typed_result_fields(
    run_all,
):
    client, resolver, prefix, consumer, virtual = await resolver_decision(
        run_all=run_all
    )
    before = copy.deepcopy(client.schemas[virtual])
    choice = await resolver.resolve(prefix + ".result_elements.result", consumer)
    reference = await resolver.resolve(prefix + ".result_elements.owner", consumer)
    assert choice["type"] == "choice"
    assert choice["choices"] == [{"value": "approved", "label": "Approved"}]
    assert reference["type"] == "reference"
    assert reference["reference"] == "sys_user"
    assert resolver.definition_cache["decision/answers/" + DECISION]["schema"] == before
    assert client.schemas[virtual] == before
    assert ("schema", virtual) in client.reads


@pytest.mark.parametrize("run_all", [False, True])
@pytest.mark.parametrize(
    "suffix", [".result", ".result_elements", ".result_elements.missing"]
)
async def test_native_decision_result_fields_reject_direct_unknown_or_incomplete_paths(
    run_all, suffix
):
    _, resolver, prefix, consumer, _ = await resolver_decision(run_all=run_all)
    with pytest.raises(CapabilityError):
        await resolver.resolve(prefix + suffix, consumer)
