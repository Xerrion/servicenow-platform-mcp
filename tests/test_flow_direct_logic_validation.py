"""Local validation consumes native direct-block metadata without saving or running it."""

import copy
import json
from typing import Any

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.flows import (
    data,
    decisions,
    editing,
    lifecycle,
    logic_blocks,
    tree,
)
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.test_flow_decision_blocks import (
    CATALOG,
    DECISION,
    TABLE_METADATA,
    MetadataClient,
)
from tests.test_flow_editing import BASE, DEFS, FLOW, base_flow
from tests.test_flow_loopback_blocks import (
    NATIVE as LOOPBACK_NATIVE,
)
from tests.test_flow_loopback_blocks import go_back, logic, native_flow
from tests.test_flow_utility_blocks import (
    CONTEXT,
    EXPLICIT,
    NATIVE_DEFINITIONS,
    SCHEDULE,
    TARGET,
    TARGET_SNAPSHOT,
    TEMPLATE,
    TEMPLATE_SNAPSHOT,
    UtilityStudioModel,
    dynamic_inputs,
    workflow_inputs,
)


def _serialized(*values: Any) -> bytes:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()


@pytest.fixture(autouse=True)
def shared_native_fixtures_are_not_mutated():
    before = _serialized(
        DEFS, LOOPBACK_NATIVE, NATIVE_DEFINITIONS, CATALOG, TABLE_METADATA
    )
    yield
    assert (
        _serialized(DEFS, LOOPBACK_NATIVE, NATIVE_DEFINITIONS, CATALOG, TABLE_METADATA)
        == before
    )


def _empty_subflow() -> dict[str, Any]:
    flow = base_flow()
    flow.update(
        type="subflow",
        actionInstances=[],
        flowLogicInstances=[],
        subFlowInstances=[],
        stages={},
        fixture_native_extension={"preserve": [None, False, 0, "æøå"]},
    )
    return flow


def _saved(flow: dict[str, Any], roots: list[tree.Step]) -> dict[str, Any]:
    for index, step in enumerate(tree.walk(roots), start=100):
        step.raw.update(id=f"{index:032x}", isNew=False)
    return tree.flatten(flow, roots)


async def _validate(settings, flow, *dependencies):
    before = _serialized(flow, *dependencies)
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await lifecycle.validate_definition(flow, client)
    assert _serialized(flow, *dependencies) == before
    assert not http_mock.unexpected
    assert all(call.request.method == "GET" for call in http_mock.calls)
    return result


def _assert_valid(result):
    assert result["valid"] is True, result
    assert result["complete"] is True, result
    assert result["diagnostics"] == [], result
    assert result["dependency_revision"], result


def _assert_rejected(result):
    assert result["valid"] is (False if result["complete"] else None), result
    assert result["diagnostics"], result
    assert any(
        issue["path"].startswith("flowLogicInstances")
        for issue in result["diagnostics"]
    ), result
    assert all(
        issue["code"] != "UNAVAILABLE_FAMILY" for issue in result["diagnostics"]
    ), result
    assert result["dependency_revision"], result


async def _utility_flow(
    settings, kind: str, inputs: dict[str, Any]
) -> tuple[Any, dict[str, Any]]:
    model: Any = UtilityStudioModel()
    model.install()
    flow = _empty_subflow()
    definition = copy.deepcopy(next(row for row in model.logic if row["type"] == kind))
    step = logic_blocks.new_step(flow, definition, copy.deepcopy(inputs))
    step.raw["fixture_native_instance"] = {"preserve": [False, 0, {"opaque": "æøå"}]}
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        await logic_blocks.prepare(
            step,
            flow,
            client,
            changed_inputs=set(inputs),
            resolver=editing.PillResolver(client, None, flow, [step]),
            definition=copy.deepcopy(definition),
        )
    flow = _saved(flow, [step])
    model.flow = flow
    return model, flow


@pytest.mark.parametrize(
    "kind,inputs",
    [
        ("TIMER", {**EXPLICIT, "timer_schedule": SCHEDULE}),
        ("RUNWORKFLOW", workflow_inputs()),
        ("RUNDYNAMICFLOW", dynamic_inputs()),
        ("GETFLOWOUTPUTS", {"flow_template": TEMPLATE, "context": CONTEXT}),
        ("GETFLOWOUTPUTS", {"context": CONTEXT}),
    ],
)
async def test_saved_native_utilities_validate_through_local_adapters(
    settings, kind, inputs
):
    model, flow = await _utility_flow(settings, kind, inputs)

    result = await _validate(
        settings, flow, model.logic, model.snapshots, model.workflow_config
    )

    _assert_valid(result)
    assert not model.bodies


@pytest.mark.parametrize(
    "kind,defect",
    [
        ("TIMER", "schedule_identity"),
        ("RUNWORKFLOW", "workflow_metadata"),
        ("RUNWORKFLOW", "workflow_input_schema"),
        ("RUNWORKFLOW", "unpublished_workflow"),
        ("RUNDYNAMICFLOW", "template_input_schema"),
        ("RUNDYNAMICFLOW", "template_output_schema"),
        ("RUNDYNAMICFLOW", "callable_owner"),
        ("GETFLOWOUTPUTS", "context_identity"),
    ],
)
async def test_saved_utility_dependencies_cannot_drift_or_resolve_to_another_record(
    settings, kind, defect
):
    inputs = {
        "TIMER": {**EXPLICIT, "timer_schedule": SCHEDULE},
        "RUNWORKFLOW": workflow_inputs(),
        "RUNDYNAMICFLOW": dynamic_inputs(),
        "GETFLOWOUTPUTS": {"flow_template": TEMPLATE, "context": CONTEXT},
    }[kind]
    model, flow = await _utility_flow(settings, kind, inputs)
    if defect == "schedule_identity":
        model.schedule["sys_id"] = "8" * 32
    elif defect == "workflow_metadata":
        model.workflow["name"] = "Changed independently"
    elif defect == "workflow_input_schema":
        model.workflow_config[0]["inputs"][0]["type"] = "integer"
    elif defect == "unpublished_workflow":
        model.workflow_versions = []
    elif defect == "template_input_schema":
        model.snapshots[TEMPLATE_SNAPSHOT]["inputs"][0]["type"] = "integer"
    elif defect == "template_output_schema":
        model.snapshots[TEMPLATE_SNAPSHOT]["outputs"][0]["type"] = "integer"
    elif defect == "callable_owner":
        model.snapshots[TARGET_SNAPSHOT]["parentFlow"] = TEMPLATE
    else:
        model.context_record["sys_id"] = TARGET

    result = await _validate(
        settings, flow, model.logic, model.snapshots, model.workflow_config
    )

    _assert_rejected(result)
    assert not model.bodies


async def test_workflow_native_dependency_metadata_changes_validation_revision(
    settings,
):
    model, flow = await _utility_flow(settings, "RUNWORKFLOW", workflow_inputs())
    before = await _validate(settings, flow, model.workflow_config)
    _assert_valid(before)
    model.workflow_config[0]["native_extension"]["version"] = "changed independently"

    after = await _validate(settings, flow, model.workflow_config)

    _assert_valid(after)
    assert after["dependency_revision"] != before["dependency_revision"]
    assert not model.bodies


async def _decision_flow(settings):
    catalog = copy.deepcopy(CATALOG)
    metadata = copy.deepcopy(TABLE_METADATA)
    record = MetadataClient.record(DECISION, metadata["name"])
    tables = ("task", metadata["answerTable"]["value"])
    descriptors: dict[str, dict[str, Any]] = {
        table: {
            "displayField": "number",
            "fixture_native_descriptor": {"preserve": [0, False]},
        }
        for table in tables
    }
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").mock(
        side_effect=lambda _: httpx2.Response(
            200, json={"result": copy.deepcopy(catalog)}
        )
    )
    http_mock.get(f"{BASE}/api/now/table/sys_decision/{DECISION}").mock(
        side_effect=lambda _: httpx2.Response(
            200, json={"result": copy.deepcopy(record)}
        )
    )
    http_mock.get(f"{BASE}/api/now/decisiontable/decisions/{DECISION}").mock(
        side_effect=lambda _: httpx2.Response(
            200, json={"result": {"data": copy.deepcopy(metadata)}}
        )
    )

    def table_record(request):
        table = request.url.params["sysparm_query"].removeprefix("name=")
        assert table in tables
        return httpx2.Response(
            200,
            json={"result": [{"sys_id": "d" * 32, "name": table, "label": table}]},
        )

    def descriptor(request):
        table = request.url.path.split("/")[-2]
        return httpx2.Response(
            200, json={"result": {"data": copy.deepcopy(descriptors[table])}}
        )

    http_mock.get(f"{BASE}/api/now/table/sys_db_object").mock(side_effect=table_record)
    for table in tables:
        http_mock.get(f"{BASE}/api/now/processflow/table/{table}/descriptor").mock(
            side_effect=descriptor
        )
    flow = _empty_subflow()
    step = tree.new_logic_step(
        FLOW,
        copy.deepcopy(catalog[0]),
        {
            "decision_table": DECISION,
            "execution": "first_match",
            "has_branches": True,
            "include_otherwise": True,
        },
    )
    step.raw["uiUniqueIdentifier"] = "decision-ui"
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        await decisions.configure(
            step,
            flow,
            copy.deepcopy(catalog),
            client,
            decision_inputs={"age": 42},
            branches=None,
            delete_children=False,
            seed="local-validation-fixture",
            new=True,
        )
    return _saved(flow, [step]), catalog, metadata, record, descriptors


async def test_decision_and_native_answer_branches_validate_without_authoring_effects(
    settings,
):
    flow, catalog, metadata, record, descriptors = await _decision_flow(settings)
    roots = tree.build_tree(flow)
    assert roots[0].logic_type == "DECISION"
    assert roots[0].children
    assert all(branch.logic_type == "DECISIONBLOCK" for branch in roots[0].children)

    result = await _validate(settings, flow, catalog, metadata, record, descriptors)

    _assert_valid(result)


@pytest.mark.parametrize("name", ["decision_label", "label"])
async def test_decision_caller_labels_still_require_visible_pill_producers(
    settings, name
):
    flow, catalog, metadata, record, descriptors = await _decision_flow(settings)
    kind = "DECISION" if name == "decision_label" else "DECISIONBLOCK"
    step = next(
        row
        for row in flow["flowLogicInstances"]
        if row["flowLogicDefinition"]["type"] == kind
    )
    data.named(step["inputs"], name)["value"] = "{{missing.text}}"
    step["data"][name] = "{{missing.text}}"

    result = await _validate(settings, flow, catalog, metadata, record, descriptors)

    _assert_rejected(result)


@pytest.mark.parametrize(
    "defect",
    [
        "saved_information",
        "saved_input_schema",
        "branch_answer",
        "branch_definition",
        "current_input_schema",
        "inactive_record",
        "unreadable_metadata",
    ],
)
async def test_decision_validation_rejects_saved_or_current_native_metadata_mismatches(
    settings, defect
):
    flow, catalog, metadata, record, descriptors = await _decision_flow(settings)
    decision = tree.Step(flow["flowLogicInstances"][0], "flowlogic")
    branch = tree.Step(
        next(
            row for row in flow["flowLogicInstances"] if row["parent"] == decision.uid
        ),
        "flowlogic",
    )
    if defect == "saved_information":
        decision.raw["decisionTableInformation"]["table"] = "another_answer_table"
    elif defect == "saved_input_schema":
        data.named(decision.raw["decisionTableInputs"], "age")["parameter"][
            "attributes"
        ]["fixture_native_schema"]["preserve"].append("changed independently")
    elif defect == "branch_answer":
        data.named(branch.raw["inputs"], "answer")["value"] = "e" * 32
        branch.raw["data"]["answer"] = "e" * 32
    elif defect == "branch_definition":
        branch.raw["flowLogicDefinition"]["attributes"]["can_move"] = "true"
    elif defect == "current_input_schema":
        data.named(metadata["inputs"], "age")["mandatory"] = False
    elif defect == "inactive_record":
        record["active"] = "false"
    else:
        http_mock.get(f"{BASE}/api/now/decisiontable/decisions/{DECISION}").respond(
            403, json={"error": {}}
        )

    result = await _validate(settings, flow, catalog, metadata, record, descriptors)

    _assert_rejected(result)


@pytest.mark.parametrize("dependency", ["record_revision", "table_descriptor"])
async def test_decision_read_dependencies_participate_in_validation_revision(
    settings, dependency
):
    flow, catalog, metadata, record, descriptors = await _decision_flow(settings)
    before = await _validate(settings, flow, catalog, metadata, record, descriptors)
    _assert_valid(before)
    if dependency == "record_revision":
        record["sys_mod_count"] = str(int(record["sys_mod_count"]) + 1)
    else:
        descriptors[metadata["answerTable"]["value"]]["fixture_native_descriptor"][
            "preserve"
        ].append("changed independently")

    after = await _validate(settings, flow, catalog, metadata, record, descriptors)

    _assert_valid(after)
    assert after["dependency_revision"] != before["dependency_revision"]


@pytest.mark.parametrize("target", ["earlier", "future"])
async def test_local_validation_checks_native_loopback_target_topology(
    settings, target
):
    earlier = logic("IF", "earlier")
    branch = logic("IF", "branch")
    source = go_back("source", target)
    future = logic("IF", "future")
    flow = native_flow(
        [(earlier, "", 1), (branch, "", 2), (source, branch.uid, 3), (future, "", 4)]
    )
    flow.update(type="subflow", stages={})
    catalog = copy.deepcopy(list(LOOPBACK_NATIVE.values()))
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": catalog}
    )

    result = await _validate(settings, flow, catalog)

    if target == "earlier":
        _assert_valid(result)
    else:
        assert result["valid"] is (False if result["complete"] else None), result
        assert result["diagnostics"], result


async def test_native_until_sandwich_validates_with_its_saved_child_topology(settings):
    until = logic("DOUNTIL", "until", children=[logic("IF", "body")])
    flow = _saved(_empty_subflow(), [until])
    catalog = copy.deepcopy(list(LOOPBACK_NATIVE.values()))
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": catalog}
    )

    result = await _validate(settings, flow, catalog)

    _assert_valid(result)
