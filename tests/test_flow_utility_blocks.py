"""Utility logic authoring consumers use installed definitions and offline Studio I/O."""

import copy
import json
from pathlib import Path
from typing import Any, override

import httpx2
import pytest
from mcp.server import MCPServer

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.flows import tree
from tests._complete_flow_fixture import ACTION, STUDIO, StudioModel
from tests.helpers import decode_response, get_tool_functions, register_mutations
from tests.test_flow_editing import FLOW, base_flow
from tests.test_flow_remaining_array_values import packed_array
from tests.test_flow_whole_pills import schema as object_schema

SCHEDULE = "9" * 32
TEMPLATE, TEMPLATE_SNAPSHOT = "10" * 16, "20" * 16
TARGET, TARGET_SNAPSHOT = "30" * 16, "40" * 16
CONTEXT = "60" * 16
WORKFLOW, RECORD = "50" * 16, "70" * 16
NATIVE_DEFINITIONS = json.loads(
    (Path(__file__).parent / "fixtures/flow_utility_definitions.json").read_text()
)
EXPLICIT = {
    "duration_type": "explicit_duration",
    "timer_duration": "1970-01-01 00:00:02",
}
PERCENTAGE = {
    "duration_type": "percentage_duration",
    "timer_percentage": 50,
    "percentage_timer_input_datetime": "2026-10-26 12:00:00",
}
RELATIVE = {
    "duration_type": "relative_duration",
    "timer_duration": "1970-01-01 00:05:00",
    "timer_relative_duration_operator": "before",
    "timer_relative_duration_datetime": "2026-10-26 12:00:00",
}


class UtilityStudioModel(StudioModel):
    def __init__(self) -> None:
        super().__init__()
        self.logic.extend(copy.deepcopy(NATIVE_DEFINITIONS))
        self.schedule = {"sys_id": SCHEDULE, "name": "Business hours"}
        self.schedules = [self.schedule]
        self.readback_defect: str | None = None
        self.flow_records = {
            identity: {
                "sys_id": identity,
                "name": name,
                "type": "flow",
                "active": "true",
                "master_snapshot": snapshot,
                "sys_scope": "global",
            }
            for identity, snapshot, name in (
                (TEMPLATE, TEMPLATE_SNAPSHOT, "Utility template"),
                (TARGET, TARGET_SNAPSHOT, "Utility target"),
            )
        }
        self.snapshots = {
            row["master_snapshot"]: {
                "id": row["master_snapshot"],
                "parentFlow": row["sys_id"],
                "name": row["name"],
                "type": row["type"],
                "snapshot": True,
                "masterSnapshot": True,
                "scope": "global",
                "access": "public",
                "isFlowEntitled": True,
                "security": {"can_read": True},
                "inputs": [
                    {
                        "id": row["sys_id"],
                        "name": "message",
                        "label": "Message",
                        "type": "string",
                        "mandatory": True,
                        "children": [],
                    }
                ],
                "outputs": [
                    {
                        "name": "answer",
                        "label": "Answer",
                        "type": "string",
                        "children": [],
                    }
                ],
            }
            for row in self.flow_records.values()
        }
        self.context_record = {
            "sys_id": CONTEXT,
            "flow": TARGET,
            "snapshot": TARGET_SNAPSHOT,
        }
        self.workflow = {
            "sys_id": WORKFLOW,
            "name": "Utility workflow",
            "sys_scope": "global",
            "table": "incident",
        }
        self.workflows = [self.workflow]
        self.workflow_versions = [
            {
                "sys_id": "80" * 16,
                "workflow": WORKFLOW,
                "published": "true",
                "active": "true",
                "table": "incident",
            }
        ]
        self.workflow_config = [
            {
                "inputs": [
                    {
                        "name": "message",
                        "element": "message",
                        "label": "Message",
                        "type": "string",
                        "children": [],
                        "mandatory": True,
                        "default_value": "",
                    }
                ],
                "native_extension": {"version": "fixture-version"},
            },
            {"table": "incident", "native_extension": {"published": True}},
        ]
        self.scope_records = [{"sys_id": "global", "scope": "global", "name": "Global"}]

    @override
    def handle(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if (
            request.method == "GET"
            and path == "/api/now/table/cmn_schedule/" + SCHEDULE
        ):
            return httpx2.Response(
                200, json={"result": copy.deepcopy(self.schedule)}, request=request
            )
        if request.method == "GET" and path == "/api/now/table/cmn_schedule":
            return httpx2.Response(
                200, json={"result": copy.deepcopy(self.schedules)}, request=request
            )
        if request.method == "GET" and path == "/api/now/table/sys_scope":
            return httpx2.Response(
                200, json={"result": copy.deepcopy(self.scope_records)}, request=request
            )
        if request.method == "GET" and path.startswith("/api/now/table/sys_hub_flow/"):
            identity = path.rsplit("/", 1)[1]
            if identity in self.flow_records:
                return httpx2.Response(
                    200,
                    json={"result": copy.deepcopy(self.flow_records[identity])},
                    request=request,
                )
        if request.method == "GET" and path == "/api/now/table/sys_hub_flow":
            query = request.url.params.get("sysparm_query", "")
            rows = [
                copy.deepcopy(row)
                for row in self.flow_records.values()
                if row["name"] in query or row["sys_id"] in query
            ]
            if rows:
                return httpx2.Response(200, json={"result": rows}, request=request)
        if request.method == "GET" and path.startswith(STUDIO + "/flow/"):
            identity = path.rsplit("/", 1)[1]
            if identity in self.snapshots:
                return httpx2.Response(
                    200,
                    json={"result": {"data": copy.deepcopy(self.snapshots[identity])}},
                    request=request,
                )
        if (
            request.method == "GET"
            and path == "/api/now/table/sys_flow_context/" + CONTEXT
        ):
            return httpx2.Response(
                200,
                json={"result": copy.deepcopy(self.context_record)},
                request=request,
            )
        if request.method == "GET" and path == "/api/now/table/wf_workflow/" + WORKFLOW:
            return httpx2.Response(
                200, json={"result": copy.deepcopy(self.workflow)}, request=request
            )
        if request.method == "GET" and path == "/api/now/table/wf_workflow":
            return httpx2.Response(
                200, json={"result": copy.deepcopy(self.workflows)}, request=request
            )
        if request.method == "GET" and path == "/api/now/table/wf_workflow_version":
            return httpx2.Response(
                200,
                json={"result": copy.deepcopy(self.workflow_versions)},
                request=request,
            )
        if (
            request.method == "GET"
            and path == "/api/now/v1/workflow/" + WORKFLOW + "/config"
        ):
            return httpx2.Response(
                200,
                json={"result": copy.deepcopy(self.workflow_config)},
                request=request,
            )
        if request.method == "GET" and path == "/api/now/table/incident/" + RECORD:
            return httpx2.Response(
                200, json={"result": {"sys_id": RECORD}}, request=request
            )
        response = super().handle(request)
        if (
            request.method == "PUT"
            and path == STUDIO + "/flow"
            and self.readback_defect
        ):
            assert self.flow is not None
            logic = self.flow["flowLogicInstances"][0]
            if self.readback_defect == "input":
                binding(logic, "timer_duration")["value"] = "1970-01-01 00:00:03"
            elif self.readback_defect == "parameter_metadata":
                binding(logic, "timer_duration")["parameter"]["attributes"][
                    "native_extension"
                ] = {"enabled": False}
            elif self.readback_defect == "definition_metadata":
                logic["flowLogicDefinition"]["variables"][0]["attributes"][
                    "pills_draggable_outside_block"
                ] = "false"
            elif self.readback_defect == "workflow_information":
                logic["workflowInformation"] = {"native_extension": {"enabled": False}}
            elif self.readback_defect == "dynamic_outputs":
                logic["dynamicOutputs"] = [{"name": "unexpected", "type": "string"}]
            elif self.readback_defect == "workflow_reference":
                logic["workflowReference"] = "51" * 16
            elif self.readback_defect == "workflow_input":
                logic["workflowInputs"][0]["value"] = "Changed independently"
        return response


def setup(settings, *, existing: bool = False):
    model = UtilityStudioModel()
    if existing:
        model.flow = base_flow()
        model.flow["stages"] = {}
    model.install()
    server = MCPServer("flow-utility-blocks")
    coordinator = register_mutations(
        server, settings, OAuthPKCEProvider(settings), frozenset({"flows"})
    )
    return model, coordinator, get_tool_functions(server)


def binding(step: dict[str, Any], name: str) -> dict[str, Any]:
    return next(row for row in step["inputs"] if row["name"] == name)


def logic_step(model: UtilityStudioModel, kind: str = "TIMER") -> dict[str, Any]:
    assert model.flow is not None
    return next(
        row
        for row in model.flow["flowLogicInstances"]
        if row["flowLogicDefinition"]["type"] == kind
    )


def create_request(inputs: dict[str, Any], *, kind: str = "TIMER") -> dict[str, Any]:
    return {
        "action": "create",
        "name": "Utility authoring",
        "scope": "global",
        "flow_type": "subflow",
        "expected_scope": "global",
        "edits": [
            {
                "op": "insert_logic",
                "logic": kind,
                "ref": "utility",
                "inputs": copy.deepcopy(inputs),
            }
        ],
    }


async def preview_edit(tools, edits):
    return decode_response(
        await tools["flow_preview"](action="edit", sys_id=FLOW, edits=edits)
    )


async def apply(tools, preview):
    assert preview["status"] == "success", preview
    return decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )


@pytest.mark.parametrize(
    "inputs",
    [
        EXPLICIT,
        {**EXPLICIT, "timer_duration": "1970-01-01 00:00:00"},
        {**EXPLICIT, "timer_duration": "1971-02-21 15:59:59"},
        RELATIVE,
        {**RELATIVE, "timer_relative_duration_operator": "after"},
        PERCENTAGE,
        {**PERCENTAGE, "timer_percentage": 0},
        {**PERCENTAGE, "timer_percentage": 100},
    ],
)
async def test_timer_complete_create_preserves_native_values_and_definition(
    settings, inputs
):
    model, _, tools = setup(settings)
    preview = decode_response(await tools["flow_preview"](**create_request(inputs)))
    assert preview["status"] == "success", preview
    assert model.flow is None and not model.bodies
    result = await apply(tools, preview)
    assert result["status"] == "success", result
    timer = logic_step(model)
    for name, value in inputs.items():
        assert binding(timer, name)["value"] == value
    native = next(row for row in NATIVE_DEFINITIONS if row["type"] == "TIMER")
    assert timer["definitionId"] == native["id"]
    assert timer["flowLogicDefinition"] == native
    assert result["data"]["refs"]["utility"]["sys_id"] == timer["id"]
    assert result["data"]["refs"]["utility"]["ui_id"] == timer["uiUniqueIdentifier"]


@pytest.mark.parametrize("schedule", [SCHEDULE, "Business hours", ""])
async def test_timer_schedule_resolves_native_identity(settings, schedule):
    model, _, tools = setup(settings)
    inputs = {**EXPLICIT, "timer_schedule": schedule}
    result = await apply(
        tools, decode_response(await tools["flow_preview"](**create_request(inputs)))
    )
    assert result["status"] == "success", result
    assert binding(logic_step(model), "timer_schedule")["value"] == (
        SCHEDULE if schedule else ""
    )


@pytest.mark.parametrize("defect", ["absent", "ambiguous", "wrong_identity"])
async def test_timer_schedule_requires_one_readable_matching_record(settings, defect):
    model, coordinator, tools = setup(settings)
    if defect == "absent":
        model.schedules = []
    elif defect == "ambiguous":
        model.schedules = [
            copy.deepcopy(model.schedule),
            {"sys_id": "8" * 32, "name": model.schedule["name"]},
        ]
    else:
        model.schedule["sys_id"] = "8" * 32
    result = decode_response(
        await tools["flow_preview"](
            **create_request(
                {
                    **EXPLICIT,
                    "timer_schedule": SCHEDULE
                    if defect == "wrong_identity"
                    else "Business hours",
                }
            )
        )
    )
    assert result["status"] == "error", result
    assert model.flow is None and not model.bodies and not len(coordinator.store)


@pytest.mark.parametrize(
    "inputs",
    [
        {},
        {"timer_duration": EXPLICIT["timer_duration"]},
        {"duration_type": "explicit_duration"},
        {**EXPLICIT, "duration_type": "explicit"},
        {**EXPLICIT, "timer_duration": -1},
        {**EXPLICIT, "timer_duration": "00:00:02"},
        {**EXPLICIT, "timer_duration": "1969-12-31 23:59:59"},
        {**EXPLICIT, "timer_duration": "1971-02-21 16:00:00"},
        {**EXPLICIT, "timer_duration": "1970-01-01 00:00:60"},
        {**EXPLICIT, "timer_duration": {"script": "return 2;"}},
        {
            "duration_type": "relative_duration",
            "timer_duration": EXPLICIT["timer_duration"],
        },
        {**RELATIVE, "timer_relative_duration_operator": "around"},
        {**RELATIVE, "timer_relative_duration_datetime": "2026-02-30 12:00:00"},
        {**RELATIVE, "timer_relative_duration_datetime": "2026-10-26T12:00:00Z"},
        {**RELATIVE, "timer_relative_duration_datetime": False},
        {"duration_type": "percentage_duration", "timer_percentage": 50},
        {**PERCENTAGE, "timer_percentage": -1},
        {**PERCENTAGE, "timer_percentage": 101},
        {**PERCENTAGE, "timer_percentage": True},
        {**PERCENTAGE, "timer_percentage": "50"},
        {**PERCENTAGE, "timer_percentage": 50.5},
        {**PERCENTAGE, "percentage_timer_input_datetime": ""},
        {**EXPLICIT, "unknown_input": "unexpected"},
    ],
)
async def test_malformed_timer_never_stages_or_creates_header(settings, inputs):
    model, coordinator, tools = setup(settings)
    result = decode_response(await tools["flow_preview"](**create_request(inputs)))
    assert result["status"] == "error", result
    assert model.flow is None and not model.bodies and not len(coordinator.store)


@pytest.mark.parametrize("inputs", [RELATIVE, PERCENTAGE])
async def test_timer_update_changes_mode_without_replacing_saved_identity(
    settings, inputs
):
    model, _, tools = setup(settings)
    created = await apply(
        tools, decode_response(await tools["flow_preview"](**create_request(EXPLICIT)))
    )
    assert created["status"] == "success", created
    before = copy.deepcopy(logic_step(model))
    result = await apply(
        tools,
        await preview_edit(
            tools,
            [
                {
                    "op": "update_inputs",
                    "step": before["id"],
                    "inputs": copy.deepcopy(inputs),
                }
            ],
        ),
    )
    assert result["status"] == "success", result
    after = logic_step(model)
    assert after["id"] == before["id"]
    assert after["uiUniqueIdentifier"] == before["uiUniqueIdentifier"]
    assert after["flowLogicDefinition"] == before["flowLogicDefinition"]
    for name, value in inputs.items():
        assert binding(after, name)["value"] == value


async def test_saved_timer_update_move_delete_preserves_other_steps_and_native_metadata(
    settings,
):
    model, _, tools = setup(settings, existing=True)
    assert model.flow is not None
    before = copy.deepcopy(model.flow)
    inserted = await apply(
        tools,
        await preview_edit(
            tools,
            [
                {
                    "op": "insert_logic",
                    "logic": "TIMER",
                    "ref": "timer",
                    "after": "ui-1",
                    "inputs": copy.deepcopy(EXPLICIT),
                }
            ],
        ),
    )
    assert inserted["status"] == "success", inserted
    timer = logic_step(model)
    timer["metadata"]["native_extension"] = {"keep": [False, 0, {"nested": "value"}]}
    original = copy.deepcopy(timer)
    modified = await apply(
        tools,
        await preview_edit(
            tools,
            [
                {
                    "op": "update_inputs",
                    "step": timer["id"],
                    "inputs": {"timer_duration": "1970-01-01 00:00:04"},
                },
                {"op": "move_step", "step": timer["id"], "after": "ui-3"},
            ],
        ),
    )
    assert modified["status"] == "success", modified
    timer = logic_step(model)
    assert timer["id"] == original["id"]
    assert timer["metadata"] == original["metadata"]
    assert binding(timer, "timer_duration")["value"] == "1970-01-01 00:00:04"
    assert model.flow is not None
    roots = tree.build_tree(model.flow)
    assert [step.uid for step in roots] == [
        "ui-1",
        "ui-2",
        "ui-3",
        timer["uiUniqueIdentifier"],
        "ui-4",
    ]
    deleted = await apply(
        tools, await preview_edit(tools, [{"op": "delete_step", "step": timer["id"]}])
    )
    assert deleted["status"] == "success", deleted
    assert model.flow["flowLogicInstances"] == []
    for section in ("actionInstances", "subFlowInstances"):
        actual = {row["id"]: row for row in model.flow[section]}
        assert set(actual) == {row["id"] for row in before[section]}
        for old in before[section]:
            assert {key: actual[old["id"]][key] for key in old} == old
    assert model.bodies[-1]["deletedFlowLogicInstances"] == [{"id": timer["id"]}]


def add_typed_producer(model):
    assert model.flow is not None
    model.flow["actionInstances"][0]["actionType"]["outputs"] = [
        {"name": "duration", "type": "glide_duration", "children": []},
        {"name": "when", "type": "glide_date_time", "children": []},
        {"name": "text", "type": "string", "children": []},
    ]


@pytest.mark.parametrize(
    "inputs",
    [
        {**EXPLICIT, "timer_duration": "{{ui-1.duration}}"},
        {**RELATIVE, "timer_relative_duration_datetime": "{{ui-1.when}}"},
        {**PERCENTAGE, "percentage_timer_input_datetime": "{{ui-1.when}}"},
    ],
)
async def test_timer_accepts_visible_typed_duration_and_datetime_pills(
    settings, inputs
):
    model, _, tools = setup(settings, existing=True)
    add_typed_producer(model)
    result = await apply(
        tools,
        await preview_edit(
            tools,
            [
                {
                    "op": "insert_logic",
                    "logic": "TIMER",
                    "after": "ui-1",
                    "inputs": copy.deepcopy(inputs),
                }
            ],
        ),
    )
    assert result["status"] == "success", result
    timer = logic_step(model)
    for name, value in inputs.items():
        assert binding(timer, name)["value"] == value


@pytest.mark.parametrize(
    "field", ["timer_duration", "timer_relative_duration_datetime"]
)
async def test_timer_rejects_untyped_or_wrong_type_pills(settings, field):
    model, coordinator, tools = setup(settings, existing=True)
    add_typed_producer(model)
    inputs = {**RELATIVE, field: "{{ui-1.text}}"}
    result = await preview_edit(
        tools,
        [{"op": "insert_logic", "logic": "TIMER", "after": "ui-1", "inputs": inputs}],
    )
    assert result["status"] == "error", result
    assert not model.bodies and not len(coordinator.store)


@pytest.mark.parametrize("edit", ["move_timer", "delete_producer"])
async def test_timer_dependency_moves_and_deletion_are_revalidated(settings, edit):
    model, coordinator, tools = setup(settings, existing=True)
    add_typed_producer(model)
    created = await apply(
        tools,
        await preview_edit(
            tools,
            [
                {
                    "op": "insert_logic",
                    "logic": "TIMER",
                    "after": "ui-1",
                    "inputs": {**EXPLICIT, "timer_duration": "{{ui-1.duration}}"},
                }
            ],
        ),
    )
    assert created["status"] == "success", created
    timer = logic_step(model)
    before = copy.deepcopy(model.flow)
    writes = copy.deepcopy(model.bodies)
    change = (
        {"op": "move_step", "step": timer["id"], "before": "ui-1"}
        if edit == "move_timer"
        else {"op": "delete_step", "step": "ui-1"}
    )
    result = await preview_edit(tools, [change])
    assert result["status"] == "error", result
    assert (
        model.flow == before and model.bodies == writes and not len(coordinator.store)
    )


@pytest.mark.parametrize("dependency", ["definition", "schedule", "flow"])
async def test_timer_drift_after_preview_blocks_first_write(settings, dependency):
    model, coordinator, tools = setup(settings, existing=True)
    preview = await preview_edit(
        tools,
        [
            {
                "op": "insert_logic",
                "logic": "TIMER",
                "inputs": {**EXPLICIT, "timer_schedule": SCHEDULE},
            }
        ],
    )
    assert preview["status"] == "success", preview
    if dependency == "definition":
        native = next(row for row in model.logic if row["type"] == "TIMER")
        native["attributes"]["native_extension"] = "changed"
    elif dependency == "schedule":
        model.schedule["name"] = "Changed schedule"
    else:
        assert model.flow is not None
        model.flow["serverSetting"] = {"native_extension": "changed"}
    result = await apply(tools, preview)
    assert result["status"] == "error", result
    assert not model.bodies and not len(coordinator.store)


@pytest.mark.parametrize(
    "defect",
    [
        "input",
        "parameter_metadata",
        "definition_metadata",
        "workflow_information",
        "dynamic_outputs",
    ],
)
async def test_timer_independent_readback_checks_extra_native_metadata(
    settings, defect
):
    model, coordinator, tools = setup(settings)
    preview = decode_response(await tools["flow_preview"](**create_request(EXPLICIT)))
    model.readback_defect = defect
    result = await apply(tools, preview)
    assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    assert result["error"]["outcome"]["sys_id"] == FLOW
    assert not len(coordinator.store)


@pytest.mark.parametrize(
    "inputs",
    [
        {"timer_duration": "not-a-duration"},
        {"duration_type": "percentage_duration", "timer_percentage": True},
        {"duration_type": "percentage_duration", "timer_percentage": 101},
        {
            "duration_type": "relative_duration",
            "timer_relative_duration_datetime": "2026-02-30 12:00:00",
        },
        {"unknown_input": "unexpected"},
    ],
)
async def test_saved_timer_rejects_malformed_input_edits_without_writes(
    settings, inputs
):
    model, coordinator, tools = setup(settings)
    created = await apply(
        tools, decode_response(await tools["flow_preview"](**create_request(EXPLICIT)))
    )
    assert created["status"] == "success", created
    timer = logic_step(model)
    before = copy.deepcopy(model.flow)
    writes = copy.deepcopy(model.bodies)
    result = await preview_edit(
        tools,
        [{"op": "update_inputs", "step": timer["id"], "inputs": copy.deepcopy(inputs)}],
    )
    assert result["status"] == "error", result
    assert (
        model.flow == before and model.bodies == writes and not len(coordinator.store)
    )


@pytest.mark.parametrize(
    ("inputs", "field", "output"),
    [
        (
            {**EXPLICIT, "timer_duration": "{{first.total_duration}}"},
            "timer_duration",
            "total_duration",
        ),
        (
            {
                **PERCENTAGE,
                "percentage_timer_input_datetime": "{{first.scheduled_end_date_time}}",
            },
            "percentage_timer_input_datetime",
            "scheduled_end_date_time",
        ),
    ],
)
async def test_timer_native_outputs_bind_downstream_consumers(
    settings, inputs, field, output
):
    model, _, tools = setup(settings)
    request = create_request(EXPLICIT)
    request["edits"][0]["ref"] = "first"
    request["edits"].append(
        {
            "op": "insert_logic",
            "logic": "TIMER",
            "ref": "second",
            "after": "first",
            "inputs": copy.deepcopy(inputs),
        }
    )
    result = await apply(tools, decode_response(await tools["flow_preview"](**request)))
    assert result["status"] == "success", result
    assert model.flow is not None
    first, second = sorted(
        model.flow["flowLogicInstances"], key=lambda row: row["order"]
    )
    assert (
        binding(second, field)["value"]
        == "{{" + first["uiUniqueIdentifier"] + "." + output + "}}"
    )
    before = copy.deepcopy(model.flow)
    writes = copy.deepcopy(model.bodies)
    invalid = await preview_edit(tools, [{"op": "delete_step", "step": first["id"]}])
    assert invalid["status"] == "error", invalid
    assert model.flow == before and model.bodies == writes


def add_hidden_template(model):
    assert model.flow is not None
    definition = copy.deepcopy(
        next(row for row in NATIVE_DEFINITIONS if row["type"] == "UPDATEFLOWTEMPLATE")
    )
    parameter = copy.deepcopy(definition["inputs"][0])
    raw = {
        "id": "7" * 32,
        "uiUniqueIdentifier": "hidden-template",
        "order": 5,
        "parent": "",
        "type": "flowlogic",
        "name": definition["name"],
        "definitionId": definition["id"],
        "flowLogicDefinition": definition,
        "inputs": [
            {
                "name": parameter["name"],
                "value": "6" * 32,
                "parameter": parameter,
                "children": [],
                "scriptActive": False,
            }
        ],
        "metadata": {"native_extension": {"keep": [False, 0, "opaque"]}},
        "updateFlowTemplate": True,
    }
    model.flow["flowLogicInstances"].append(raw)
    return raw


async def test_unrelated_edit_preserves_existing_hidden_update_flow_template(settings):
    model, _, tools = setup(settings, existing=True)
    hidden = copy.deepcopy(add_hidden_template(model))
    result = await apply(
        tools,
        await preview_edit(
            tools,
            [
                {
                    "op": "update_inputs",
                    "step": "ui-1",
                    "inputs": {"log_message": "Changed"},
                }
            ],
        ),
    )
    assert result["status"] == "success", result
    saved = logic_step(model, "UPDATEFLOWTEMPLATE")
    assert {key: saved[key] for key in hidden} == hidden


@pytest.mark.parametrize("operation", ["insert", "update", "move", "delete"])
async def test_hidden_update_flow_template_rejects_generic_authoring(
    settings, operation
):
    model, coordinator, tools = setup(settings, existing=True)
    hidden = add_hidden_template(model)
    edits = {
        "insert": {
            "op": "insert_logic",
            "logic": "UPDATEFLOWTEMPLATE",
            "inputs": {"flow": "6" * 32},
        },
        "update": {
            "op": "update_inputs",
            "step": hidden["id"],
            "inputs": {"flow": "5" * 32},
        },
        "move": {"op": "move_step", "step": hidden["id"], "before": "ui-1"},
        "delete": {"op": "delete_step", "step": hidden["id"]},
    }
    before = copy.deepcopy(model.flow)
    result = await preview_edit(tools, [copy.deepcopy(edits[operation])])
    assert result["status"] == "error", result
    assert model.flow == before and not model.bodies and not len(coordinator.store)


def dynamic_inputs(*, wait: bool = True):
    return {
        "flow_template": TEMPLATE,
        "flow": TARGET,
        "wait_for_completion": wait,
        "dynamic_inputs": {"message": "Dispatch"},
    }


def append_answer_consumer(request, producer: str = "utility"):
    request["edits"].append(
        {
            "op": "insert_action",
            "action_type_sys_id": ACTION,
            "after": producer,
            "inputs": {"message": "{{" + producer + ".answer}}"},
        }
    )


@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("wait", [False, True])
async def test_dynamic_flow_create_resolves_template_and_explicit_wait(
    settings, named, wait
):
    model, _, tools = setup(settings)
    inputs = dynamic_inputs(wait=wait)
    if named:
        inputs.update(flow_template="Utility template", flow="Utility target")
    result = await apply(
        tools,
        decode_response(
            await tools["flow_preview"](**create_request(inputs, kind="RUNDYNAMICFLOW"))
        ),
    )
    assert result["status"] == "success", result
    step = logic_step(model, "RUNDYNAMICFLOW")
    assert binding(step, "flow_template")["value"] == TEMPLATE
    assert binding(step, "flow")["value"] == inputs["flow"]
    assert binding(step, "wait_for_completion")["value"] is wait
    assert (
        next(row for row in step["dynamicInputs"] if row["name"] == "message")["value"]
        == "Dispatch"
    )
    outputs = {row["name"]: row for row in step["flowLogicDefinition"]["variables"]}
    assert outputs["context"]["reference"] == "sys_flow_context"
    assert ("answer" in outputs) is wait
    if wait:
        assert outputs["answer"]["type"] == "string"
        assert outputs["answer"]["output_type"] == "FLOW_TEMPLATE_DYNAMIC_OUTPUT"


@pytest.mark.parametrize("wait", [False, True])
async def test_dynamic_flow_wait_controls_template_output_consumption(settings, wait):
    model, coordinator, tools = setup(settings)
    request = create_request(dynamic_inputs(wait=wait), kind="RUNDYNAMICFLOW")
    append_answer_consumer(request)
    preview = decode_response(await tools["flow_preview"](**request))
    if not wait:
        assert preview["status"] == "error", preview
        assert model.flow is None and not model.bodies and not len(coordinator.store)
        return
    result = await apply(tools, preview)
    assert result["status"] == "success", result
    step = logic_step(model, "RUNDYNAMICFLOW")
    assert model.flow is not None
    assert (
        model.flow["actionInstances"][0]["inputs"][0]["value"]
        == "{{" + step["uiUniqueIdentifier"] + ".answer}}"
    )
    before = copy.deepcopy(model.flow)
    writes = copy.deepcopy(model.bodies)
    changed = await preview_edit(
        tools,
        [
            {
                "op": "update_inputs",
                "step": step["id"],
                "inputs": {"wait_for_completion": False},
            }
        ],
    )
    assert changed["status"] == "error", changed
    assert model.flow == before and model.bodies == writes


async def test_dynamic_flow_accepts_runtime_string_pill_without_concrete_target(
    settings,
):
    model, _, tools = setup(settings, existing=True)
    add_typed_producer(model)
    inputs = {**dynamic_inputs(), "flow": "{{ui-1.text}}"}
    result = await apply(
        tools,
        await preview_edit(
            tools,
            [
                {
                    "op": "insert_logic",
                    "logic": "RUNDYNAMICFLOW",
                    "after": "ui-1",
                    "inputs": inputs,
                }
            ],
        ),
    )
    assert result["status"] == "success", result
    assert (
        binding(logic_step(model, "RUNDYNAMICFLOW"), "flow")["value"] == "{{ui-1.text}}"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"wait_for_completion": None},
        {"wait_for_completion": "true"},
        {"wait_for_completion": 1},
        {"flow_template": ""},
        {"flow": ""},
        {"flow": "Missing flow"},
        {"dynamic_inputs": []},
        {"dynamic_inputs": {"unknown": "Dispatch"}},
        {"dynamic_inputs": {"message": 42}},
    ],
)
async def test_dynamic_flow_rejects_malformed_configuration_without_header(
    settings, change
):
    model, coordinator, tools = setup(settings)
    inputs = {**dynamic_inputs(), **copy.deepcopy(change)}
    if change.get("wait_for_completion", False) is None:
        inputs.pop("wait_for_completion")
    result = decode_response(
        await tools["flow_preview"](**create_request(inputs, kind="RUNDYNAMICFLOW"))
    )
    assert result["status"] == "error", result
    assert model.flow is None and not model.bodies and not len(coordinator.store)


@pytest.mark.parametrize(
    "defect",
    [
        "unpublished",
        "snapshot_identity",
        "snapshot_unreadable",
        "schema_mismatch",
        "ambiguous_template",
    ],
)
async def test_dynamic_flow_requires_published_readable_matching_schemas(
    settings, defect
):
    model, coordinator, tools = setup(settings)
    inputs = dynamic_inputs()
    if defect == "unpublished":
        model.flow_records[TEMPLATE]["master_snapshot"] = ""
    elif defect == "snapshot_identity":
        model.snapshots[TEMPLATE_SNAPSHOT]["parentFlow"] = TARGET
    elif defect == "snapshot_unreadable":
        model.snapshots[TEMPLATE_SNAPSHOT]["security"]["can_read"] = False
    elif defect == "schema_mismatch":
        model.snapshots[TARGET_SNAPSHOT]["inputs"][0]["type"] = "integer"
    else:
        duplicate = copy.deepcopy(model.flow_records[TEMPLATE])
        duplicate["sys_id"] = "11" * 16
        model.flow_records[duplicate["sys_id"]] = duplicate
        inputs["flow_template"] = "Utility template"
    result = decode_response(
        await tools["flow_preview"](**create_request(inputs, kind="RUNDYNAMICFLOW"))
    )
    assert result["status"] == "error", result
    assert model.flow is None and not model.bodies and not len(coordinator.store)


@pytest.mark.parametrize(
    "dependency",
    ["template_header", "template_snapshot", "target_header", "target_snapshot"],
)
async def test_dynamic_flow_dependency_drift_blocks_first_write(settings, dependency):
    model, coordinator, tools = setup(settings)
    preview = decode_response(
        await tools["flow_preview"](
            **create_request(dynamic_inputs(), kind="RUNDYNAMICFLOW")
        )
    )
    assert preview["status"] == "success", preview
    record = {
        "template_header": model.flow_records[TEMPLATE],
        "template_snapshot": model.snapshots[TEMPLATE_SNAPSHOT],
        "target_header": model.flow_records[TARGET],
        "target_snapshot": model.snapshots[TARGET_SNAPSHOT],
    }[dependency]
    if dependency.endswith("header"):
        record["name"] = "Changed native name"
    else:
        record["native_extension"] = {"version": "changed"}
    result = await apply(tools, preview)
    assert result["status"] == "error", result
    assert model.flow is None and not model.bodies and not len(coordinator.store)


@pytest.mark.parametrize("context", ["static", "dynamic_pill"])
async def test_get_flow_outputs_exposes_template_outputs_for_native_context(
    settings, context
):
    model, _, tools = setup(settings)
    inputs = {"flow_template": TEMPLATE, "context": CONTEXT}
    request = create_request(inputs, kind="GETFLOWOUTPUTS")
    if context == "dynamic_pill":
        request["edits"].insert(
            0,
            {
                "op": "insert_logic",
                "logic": "RUNDYNAMICFLOW",
                "ref": "dispatch",
                "inputs": dynamic_inputs(wait=False),
            },
        )
        request["edits"][1]["after"] = "dispatch"
        request["edits"][1]["inputs"]["context"] = "{{dispatch.context}}"
    append_answer_consumer(request)
    result = await apply(tools, decode_response(await tools["flow_preview"](**request)))
    assert result["status"] == "success", result
    step = logic_step(model, "GETFLOWOUTPUTS")
    assert binding(step, "flow_template")["value"] == TEMPLATE
    output = next(
        row
        for row in step["flowLogicDefinition"]["variables"]
        if row["name"] == "answer"
    )
    assert output["type"] == "string"
    assert output["output_type"] == "FLOW_TEMPLATE_DYNAMIC_OUTPUT"
    assert model.flow is not None
    assert (
        model.flow["actionInstances"][0]["inputs"][0]["value"]
        == "{{" + step["uiUniqueIdentifier"] + ".answer}}"
    )


@pytest.mark.parametrize(
    "inputs",
    [
        {"flow_template": TEMPLATE},
        {"flow_template": TEMPLATE, "context": "not-a-context"},
        {"flow_template": TEMPLATE, "context": True},
        {"flow_template": TEMPLATE, "context": {"script": "return context;"}},
    ],
)
async def test_get_flow_outputs_rejects_malformed_context_or_template(settings, inputs):
    model, coordinator, tools = setup(settings)
    result = decode_response(
        await tools["flow_preview"](
            **create_request(copy.deepcopy(inputs), kind="GETFLOWOUTPUTS")
        )
    )
    assert result["status"] == "error", result
    assert model.flow is None and not model.bodies and not len(coordinator.store)


@pytest.mark.parametrize("defect", ["workflow_information", "dynamic_outputs"])
@pytest.mark.parametrize("kind", ["RUNDYNAMICFLOW", "GETFLOWOUTPUTS"])
async def test_dynamic_utility_independent_readback_checks_extra_native_metadata(
    settings, defect, kind
):
    model, coordinator, tools = setup(settings)
    inputs = (
        dynamic_inputs()
        if kind == "RUNDYNAMICFLOW"
        else {"flow_template": TEMPLATE, "context": CONTEXT}
    )
    preview = decode_response(
        await tools["flow_preview"](**create_request(inputs, kind=kind))
    )
    model.readback_defect = defect
    result = await apply(tools, preview)
    assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    assert result["error"]["outcome"]["sys_id"] == FLOW
    assert not len(coordinator.store)


@pytest.mark.parametrize("kind", ["RUNDYNAMICFLOW", "GETFLOWOUTPUTS"])
async def test_saved_dynamic_utility_edit_move_delete_keeps_native_identity(
    settings, kind
):
    model, _, tools = setup(settings, existing=True)
    inputs = (
        dynamic_inputs()
        if kind == "RUNDYNAMICFLOW"
        else {"flow_template": TEMPLATE, "context": CONTEXT}
    )
    created = await apply(
        tools,
        await preview_edit(
            tools,
            [{"op": "insert_logic", "logic": kind, "after": "ui-1", "inputs": inputs}],
        ),
    )
    assert created["status"] == "success", created
    step = logic_step(model, kind)
    step["metadata"]["native_extension"] = {"keep": [False, 0, "opaque"]}
    before = copy.deepcopy(step)
    updates = (
        {"wait_for_completion": False, "dynamic_inputs": {"message": "Changed"}}
        if kind == "RUNDYNAMICFLOW"
        else {"flow_template": TARGET}
    )
    modified = await apply(
        tools,
        await preview_edit(
            tools,
            [
                {"op": "update_inputs", "step": step["id"], "inputs": updates},
                {"op": "move_step", "step": step["id"], "after": "ui-3"},
            ],
        ),
    )
    assert modified["status"] == "success", modified
    step = logic_step(model, kind)
    assert step["id"] == before["id"]
    assert step["uiUniqueIdentifier"] == before["uiUniqueIdentifier"]
    assert step["metadata"] == before["metadata"]
    if kind == "RUNDYNAMICFLOW":
        assert binding(step, "wait_for_completion")["value"] is False
        assert (
            next(row for row in step["dynamicInputs"] if row["name"] == "message")[
                "value"
            ]
            == "Changed"
        )
    else:
        assert binding(step, "flow_template")["value"] == TARGET
    assert model.flow is not None
    assert [row.uid for row in tree.build_tree(model.flow)] == [
        "ui-1",
        "ui-2",
        "ui-3",
        step["uiUniqueIdentifier"],
        "ui-4",
    ]
    deleted = await apply(
        tools, await preview_edit(tools, [{"op": "delete_step", "step": step["id"]}])
    )
    assert deleted["status"] == "success", deleted
    assert model.flow["flowLogicInstances"] == []
    assert model.bodies[-1]["deletedFlowLogicInstances"] == [{"id": step["id"]}]


def workflow_inputs(*, wait: bool = True):
    return {
        "workflow_reference": WORKFLOW,
        "workflow_inputs": {"message": "Dispatch"},
        "wait": wait,
        "current": RECORD,
    }


@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("wait", [False, True])
async def test_workflow_create_resolves_native_configuration_and_explicit_wait(
    settings, named, wait
):
    model, _, tools = setup(settings)
    inputs = workflow_inputs(wait=wait)
    if named:
        inputs["workflow_reference"] = "Utility workflow"
    result = await apply(
        tools,
        decode_response(
            await tools["flow_preview"](**create_request(inputs, kind="RUNWORKFLOW"))
        ),
    )
    assert result["status"] == "success", result
    step = logic_step(model, "RUNWORKFLOW")
    assert step["workflowReference"] == WORKFLOW
    assert step["workflowInformation"]["table"] == "incident"
    assert step["workflowInformation"]["display_value"] == "Utility workflow"
    assert binding(step, "wait")["value"] is wait
    assert binding(step, "current")["value"] == RECORD
    assert (
        next(row for row in step["workflowInputs"] if row["name"] == "message")["value"]
        == "Dispatch"
    )
    native = next(row for row in NATIVE_DEFINITIONS if row["type"] == "RUNWORKFLOW")
    assert step["flowLogicDefinition"]["variables"] == native["variables"]


@pytest.mark.parametrize("global_table", ["global", "absent"])
async def test_global_workflow_does_not_require_current_record(settings, global_table):
    model, _, tools = setup(settings)
    model.workflow["table"] = "global"
    model.workflow_versions[0]["table"] = "global"
    if global_table == "global":
        model.workflow_config[1]["table"] = "global"
    else:
        model.workflow_config = [copy.deepcopy(model.workflow_config[0])]
    inputs = workflow_inputs()
    inputs.pop("current")
    result = await apply(
        tools,
        decode_response(
            await tools["flow_preview"](**create_request(inputs, kind="RUNWORKFLOW"))
        ),
    )
    assert result["status"] == "success", result
    if global_table == "global":
        assert (
            logic_step(model, "RUNWORKFLOW")["workflowInformation"]["table"] == "global"
        )


async def test_workflow_current_accepts_visible_typed_record_pill(settings):
    model, _, tools = setup(settings, existing=True)
    add_typed_producer(model)
    assert model.flow is not None
    model.flow["actionInstances"][0]["actionType"]["outputs"].append(
        {"name": "record", "type": "reference", "reference": "incident", "children": []}
    )
    inputs = {**workflow_inputs(), "current": "{{ui-1.record}}"}
    result = await apply(
        tools,
        await preview_edit(
            tools,
            [
                {
                    "op": "insert_logic",
                    "logic": "RUNWORKFLOW",
                    "after": "ui-1",
                    "inputs": inputs,
                }
            ],
        ),
    )
    assert result["status"] == "success", result
    assert (
        binding(logic_step(model, "RUNWORKFLOW"), "current")["value"]
        == "{{ui-1.record}}"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"workflow_reference": ""},
        {"workflow_inputs": []},
        {"workflow_inputs": {"unknown": "Dispatch"}},
        {"workflow_inputs": {"message": 42}},
        {"wait": None},
        {"wait": "true"},
        {"wait": 1},
        {"current": ""},
        {"current": "not-a-record"},
        {"current": True},
        {"unknown_input": "unexpected"},
    ],
)
async def test_workflow_rejects_malformed_configuration_before_header(settings, change):
    model, coordinator, tools = setup(settings)
    inputs = {**workflow_inputs(), **copy.deepcopy(change)}
    if change.get("wait", False) is None:
        inputs.pop("wait")
    result = decode_response(
        await tools["flow_preview"](**create_request(inputs, kind="RUNWORKFLOW"))
    )
    assert result["status"] == "error", result
    assert model.flow is None and not model.bodies and not len(coordinator.store)


@pytest.mark.parametrize("dependency", ["workflow", "configuration", "input_schema"])
async def test_workflow_dependency_drift_blocks_first_write(settings, dependency):
    model, coordinator, tools = setup(settings)
    preview = decode_response(
        await tools["flow_preview"](
            **create_request(workflow_inputs(), kind="RUNWORKFLOW")
        )
    )
    assert preview["status"] == "success", preview
    if dependency == "workflow":
        model.workflow["name"] = "Changed workflow"
    elif dependency == "configuration":
        model.workflow_config[0]["native_extension"]["version"] = "changed"
    else:
        model.workflow_config[0]["inputs"][0]["type"] = "integer"
    result = await apply(tools, preview)
    assert result["status"] == "error", result
    assert model.flow is None and not model.bodies and not len(coordinator.store)


@pytest.mark.parametrize(
    "defect", ["workflow_reference", "workflow_input", "workflow_information"]
)
async def test_workflow_independent_readback_checks_native_configuration_metadata(
    settings, defect
):
    model, coordinator, tools = setup(settings)
    preview = decode_response(
        await tools["flow_preview"](
            **create_request(workflow_inputs(), kind="RUNWORKFLOW")
        )
    )
    model.readback_defect = defect
    result = await apply(tools, preview)
    assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    assert result["error"]["outcome"]["sys_id"] == FLOW
    assert not len(coordinator.store)


async def test_get_flow_outputs_context_only_retains_no_inferred_template_schema(
    settings,
):
    model, _, tools = setup(settings)
    result = await apply(
        tools,
        decode_response(
            await tools["flow_preview"](
                **create_request({"context": CONTEXT}, kind="GETFLOWOUTPUTS")
            )
        ),
    )
    assert result["status"] == "success", result
    step = logic_step(model, "GETFLOWOUTPUTS")
    assert binding(step, "context")["value"] == CONTEXT
    assert step["flowLogicDefinition"]["variables"] == []
    assert step["dynamicOutputs"] == []


@pytest.mark.parametrize(
    "defect",
    [
        "no_published_version",
        "conflicting_versions",
        "wrong_table",
        "excluded_catalog_table",
    ],
)
async def test_workflow_requires_matching_active_published_version_evidence(
    settings, defect
):
    model, coordinator, tools = setup(settings)
    if defect == "no_published_version":
        model.workflow_versions = []
    elif defect == "conflicting_versions":
        duplicate = copy.deepcopy(model.workflow_versions[0])
        duplicate.update(sys_id="81" * 16, table="problem")
        model.workflow_versions.append(duplicate)
    elif defect == "excluded_catalog_table":
        model.workflow["table"] = "sc_req_item"
        model.workflow_versions[0]["table"] = "sc_req_item"
        model.workflow_config[1]["table"] = "sc_req_item"
    else:
        model.workflow_versions[0]["table"] = "problem"
    result = decode_response(
        await tools["flow_preview"](
            **create_request(workflow_inputs(), kind="RUNWORKFLOW")
        )
    )
    assert result["status"] == "error", result
    assert model.flow is None and not model.bodies and not len(coordinator.store)


@pytest.mark.parametrize("dependency", ["context_source", "template_output_schema"])
async def test_get_flow_outputs_current_context_and_template_drift_block_first_write(
    settings, dependency
):
    model, coordinator, tools = setup(settings)
    request = create_request(
        {"flow_template": TEMPLATE, "context": CONTEXT}, kind="GETFLOWOUTPUTS"
    )
    append_answer_consumer(request)
    preview = decode_response(await tools["flow_preview"](**request))
    assert preview["status"] == "success", preview
    if dependency == "context_source":
        model.context_record.update(flow=TEMPLATE, snapshot=TEMPLATE_SNAPSHOT)
    else:
        model.snapshots[TEMPLATE_SNAPSHOT]["outputs"][0]["type"] = "integer"
    result = await apply(tools, preview)
    assert result["status"] == "error", result
    assert model.flow is None and not model.bodies and not len(coordinator.store)


async def test_workflow_root_with_multiple_eligible_versions_is_selectable(settings):
    model, _, tools = setup(settings)
    duplicate = copy.deepcopy(model.workflow_versions[0])
    duplicate["sys_id"] = "81" * 16
    model.workflow_versions.append(duplicate)
    result = await apply(
        tools,
        decode_response(
            await tools["flow_preview"](
                **create_request(workflow_inputs(), kind="RUNWORKFLOW")
            )
        ),
    )
    assert result["status"] == "success", result
    assert logic_step(model, "RUNWORKFLOW")["workflowReference"] == WORKFLOW


@pytest.mark.parametrize("wait", [False, True])
@pytest.mark.parametrize("output", ["state", "context", "result", "returnval"])
async def test_workflow_wait_controls_all_native_output_consumers(
    settings, wait, output
):
    model, coordinator, tools = setup(settings)
    if output == "context":
        model.action["inputs"] = [
            {
                "name": "message",
                "type": "reference",
                "reference": "wf_context",
                "children": [],
            }
        ]
    request = create_request(workflow_inputs(wait=wait), kind="RUNWORKFLOW")
    request["edits"].append(
        {
            "op": "insert_action",
            "action_type_sys_id": ACTION,
            "after": "utility",
            "inputs": {"message": "{{utility." + output + "}}"},
        }
    )
    preview = decode_response(await tools["flow_preview"](**request))
    if not wait:
        assert preview["status"] == "error", preview
        assert model.flow is None and not model.bodies and not len(coordinator.store)
        return
    result = await apply(tools, preview)
    assert result["status"] == "success", result
    step = logic_step(model, "RUNWORKFLOW")
    assert model.flow is not None
    assert (
        model.flow["actionInstances"][0]["inputs"][0]["value"]
        == "{{" + step["uiUniqueIdentifier"] + "." + output + "}}"
    )


async def test_saved_workflow_input_edit_move_delete_keeps_native_identity(settings):
    model, _, tools = setup(settings, existing=True)
    created = await apply(
        tools,
        await preview_edit(
            tools,
            [
                {
                    "op": "insert_logic",
                    "logic": "RUNWORKFLOW",
                    "after": "ui-1",
                    "inputs": workflow_inputs(),
                }
            ],
        ),
    )
    assert created["status"] == "success", created
    step = logic_step(model, "RUNWORKFLOW")
    step["metadata"]["native_extension"] = {"keep": [False, 0, "opaque"]}
    before = copy.deepcopy(step)
    modified = await apply(
        tools,
        await preview_edit(
            tools,
            [
                {
                    "op": "update_inputs",
                    "step": step["id"],
                    "inputs": {
                        "wait": False,
                        "workflow_inputs": {"message": "Changed"},
                    },
                },
                {"op": "move_step", "step": step["id"], "after": "ui-3"},
            ],
        ),
    )
    assert modified["status"] == "success", modified
    step = logic_step(model, "RUNWORKFLOW")
    assert step["id"] == before["id"]
    assert step["uiUniqueIdentifier"] == before["uiUniqueIdentifier"]
    assert step["metadata"] == before["metadata"]
    assert step["workflowReference"] == before["workflowReference"]
    assert binding(step, "wait")["value"] is False
    assert (
        next(row for row in step["workflowInputs"] if row["name"] == "message")["value"]
        == "Changed"
    )
    assert model.flow is not None
    assert [row.uid for row in tree.build_tree(model.flow)] == [
        "ui-1",
        "ui-2",
        "ui-3",
        step["uiUniqueIdentifier"],
        "ui-4",
    ]
    deleted = await apply(
        tools, await preview_edit(tools, [{"op": "delete_step", "step": step["id"]}])
    )
    assert deleted["status"] == "success", deleted
    assert model.flow["flowLogicInstances"] == []
    assert model.bodies[-1]["deletedFlowLogicInstances"] == [{"id": step["id"]}]


@pytest.mark.parametrize("kind", ["RUNDYNAMICFLOW", "GETFLOWOUTPUTS"])
@pytest.mark.parametrize(
    "value_type",
    ["object", "array.string", "array.choice", "array.datetime", "array.array"],
)
async def test_dynamic_utility_whole_object_and_array_outputs_bind_native_consumers(
    settings, kind, value_type
):
    model, _, tools = setup(settings)
    schema = copy.deepcopy(
        object_schema()
        if value_type == "object"
        else packed_array("payload", value_type)
    )
    model.snapshots[TEMPLATE_SNAPSHOT]["outputs"].append(copy.deepcopy(schema))
    model.snapshots[TARGET_SNAPSHOT]["outputs"].append(copy.deepcopy(schema))
    model.action["inputs"].append(copy.deepcopy(schema))
    inputs = (
        dynamic_inputs()
        if kind == "RUNDYNAMICFLOW"
        else {"flow_template": TEMPLATE, "context": CONTEXT}
    )
    request = create_request(inputs, kind=kind)
    request["edits"].append(
        {
            "op": "insert_action",
            "action_type_sys_id": ACTION,
            "after": "utility",
            "inputs": {"payload": "{{utility.payload}}"},
        }
    )
    result = await apply(tools, decode_response(await tools["flow_preview"](**request)))
    assert result["status"] == "success", result
    utility = logic_step(model, kind)
    assert model.flow is not None
    consumer = model.flow["actionInstances"][0]
    value = binding(consumer, "payload")
    assert value["value"] == "{{" + utility["uiUniqueIdentifier"] + ".payload}}"
    assert value["children"] == []
    before = copy.deepcopy(model.flow)
    writes = copy.deepcopy(model.bodies)
    invalid = await preview_edit(
        tools, [{"op": "move_step", "step": consumer["id"], "before": utility["id"]}]
    )
    assert invalid["status"] == "error", invalid
    assert model.flow == before and model.bodies == writes


@pytest.mark.parametrize("kind", ["RUNDYNAMICFLOW", "GETFLOWOUTPUTS"])
async def test_dynamic_utility_whole_output_current_template_schema_drift_blocks_first_write(
    settings, kind
):
    model, coordinator, tools = setup(settings)
    schema = copy.deepcopy(object_schema())
    model.snapshots[TEMPLATE_SNAPSHOT]["outputs"].append(copy.deepcopy(schema))
    model.snapshots[TARGET_SNAPSHOT]["outputs"].append(copy.deepcopy(schema))
    model.action["inputs"].append(copy.deepcopy(schema))
    inputs = (
        dynamic_inputs()
        if kind == "RUNDYNAMICFLOW"
        else {"flow_template": TEMPLATE, "context": CONTEXT}
    )
    request = create_request(inputs, kind=kind)
    request["edits"].append(
        {
            "op": "insert_action",
            "action_type_sys_id": ACTION,
            "after": "utility",
            "inputs": {"payload": "{{utility.payload}}"},
        }
    )
    preview = decode_response(await tools["flow_preview"](**request))
    assert preview["status"] == "success", preview
    payload = model.snapshots[TEMPLATE_SNAPSHOT]["outputs"][-1]
    payload["children"][0]["type"] = "boolean"
    result = await apply(tools, preview)
    assert result["status"] == "error", result
    assert model.flow is None and not model.bodies and not len(coordinator.store)


@pytest.mark.parametrize("kind", ["RUNDYNAMICFLOW", "RUNWORKFLOW"])
@pytest.mark.parametrize("value_type", ["object", "array.string"])
async def test_utility_complex_literals_resolve_same_preview_nested_output_aliases(
    settings, kind, value_type
):
    model, coordinator, tools = setup(settings)
    if value_type == "object":
        schema = {
            "name": "details",
            "label": "Details",
            "type": "object",
            "mandatory": True,
            "children": [
                {"name": "text", "label": "Text", "type": "string", "children": []}
            ],
        }
        literal = {"text": "{{source.answer}}"}
    else:
        schema = copy.deepcopy(packed_array("details", "array.string"))
        schema["mandatory"] = True
        literal = ["{{source.answer}}", "unchanged"]

    if kind == "RUNDYNAMICFLOW":
        root_id, snapshot_id = "90" * 16, "91" * 16
        root = copy.deepcopy(model.flow_records[TEMPLATE])
        root.update(
            sys_id=root_id,
            name="Complex input template",
            master_snapshot=snapshot_id,
        )
        snapshot = copy.deepcopy(model.snapshots[TEMPLATE_SNAPSHOT])
        snapshot.update(
            id=snapshot_id,
            parentFlow=root_id,
            name=root["name"],
            inputs=[copy.deepcopy(schema)],
        )
        model.flow_records[root_id] = root
        model.snapshots[snapshot_id] = snapshot
        inputs = {
            "flow_template": root_id,
            "flow": root_id,
            "wait_for_completion": True,
            "dynamic_inputs": {"details": copy.deepcopy(literal)},
        }
        collection = "dynamicInputs"
    else:
        workflow_schema = copy.deepcopy(schema)
        workflow_schema["element"] = "details"
        model.workflow_config[0]["inputs"] = [workflow_schema]
        inputs = {
            **workflow_inputs(),
            "workflow_inputs": {"details": copy.deepcopy(literal)},
        }
        collection = "workflowInputs"

    request = create_request(dynamic_inputs(), kind="RUNDYNAMICFLOW")
    request["edits"][0]["ref"] = "source"
    request["edits"].append(
        {
            "op": "insert_logic",
            "logic": kind,
            "ref": "utility",
            "after": "source",
            "inputs": inputs,
        }
    )
    preview = decode_response(await tools["flow_preview"](**request))
    assert preview["status"] == "success", preview
    assert model.flow is None and not model.bodies
    result = await apply(tools, preview)
    assert result["status"] == "success", result
    source = result["data"]["refs"]["source"]
    consumer_ref = result["data"]["refs"]["utility"]
    assert model.flow is not None
    consumer = next(
        row
        for row in model.flow["flowLogicInstances"]
        if row["uiUniqueIdentifier"] == consumer_ref["ui_id"]
    )
    details = next(row for row in consumer[collection] if row["name"] == "details")
    expected_pill = "{{" + source["ui_id"] + ".answer}}"
    assert details["value"] == ""
    assert [row["value"] for row in details["children"]] == (
        [expected_pill] if value_type == "object" else [expected_pill, "unchanged"]
    )
    before = copy.deepcopy(model.flow)
    writes = copy.deepcopy(model.bodies)
    invalid = await preview_edit(
        tools, [{"op": "delete_step", "step": source["sys_id"]}]
    )
    assert invalid["status"] == "error", invalid
    assert (
        model.flow == before and model.bodies == writes and not len(coordinator.store)
    )


async def test_dynamic_flow_scope_qualified_callable_preserves_runtime_selector(
    settings,
):
    model, _, tools = setup(settings)
    inputs = {**dynamic_inputs(), "flow": "global.Utility target"}
    result = await apply(
        tools,
        decode_response(
            await tools["flow_preview"](**create_request(inputs, kind="RUNDYNAMICFLOW"))
        ),
    )
    assert result["status"] == "success", result
    step = logic_step(model, "RUNDYNAMICFLOW")
    assert binding(step, "flow_template")["value"] == TEMPLATE
    assert binding(step, "flow")["value"] == "global.Utility target"


@pytest.mark.parametrize(
    "scopes",
    [
        [],
        [
            {"sys_id": "global", "scope": "global"},
            {"sys_id": "92" * 16, "scope": "global"},
        ],
    ],
)
async def test_dynamic_flow_scope_qualified_callable_requires_unique_scope(
    settings, scopes
):
    model, coordinator, tools = setup(settings)
    model.scope_records = copy.deepcopy(scopes)
    result = decode_response(
        await tools["flow_preview"](
            **create_request(
                {**dynamic_inputs(), "flow": "global.Utility target"},
                kind="RUNDYNAMICFLOW",
            )
        )
    )
    assert result["status"] == "error", result
    assert model.flow is None and not model.bodies and not len(coordinator.store)


def set_saved_native_wait(step, name, value):
    binding(step, name)["value"] = value
    if "data" in step:
        step["data"][name] = value


@pytest.mark.parametrize("kind", ["RUNWORKFLOW", "RUNDYNAMICFLOW"])
@pytest.mark.parametrize("native_wait", ["false", "0", "true", "1"])
async def test_saved_native_wait_strings_survive_unrelated_utility_input_edits(
    settings, kind, native_wait
):
    model, _, tools = setup(settings)
    wait = native_wait in {"true", "1"}
    inputs = (
        workflow_inputs(wait=wait)
        if kind == "RUNWORKFLOW"
        else dynamic_inputs(wait=wait)
    )
    created = await apply(
        tools,
        decode_response(
            await tools["flow_preview"](**create_request(inputs, kind=kind))
        ),
    )
    assert created["status"] == "success", created
    step = logic_step(model, kind)
    wait_name = "wait" if kind == "RUNWORKFLOW" else "wait_for_completion"
    set_saved_native_wait(step, wait_name, native_wait)
    field = "workflow_inputs" if kind == "RUNWORKFLOW" else "dynamic_inputs"
    result = await apply(
        tools,
        await preview_edit(
            tools,
            [
                {
                    "op": "update_inputs",
                    "step": step["id"],
                    "inputs": {field: {"message": "Changed"}},
                }
            ],
        ),
    )
    assert result["status"] == "success", result
    assert binding(logic_step(model, kind), wait_name)["value"] == native_wait


@pytest.mark.parametrize("native_wait", ["false", "0"])
@pytest.mark.parametrize("output", ["state", "context", "result", "returnval"])
async def test_saved_workflow_false_wait_strings_disable_all_native_output_consumers(
    settings, native_wait, output
):
    model, coordinator, tools = setup(settings)
    if output == "context":
        model.action["inputs"] = [
            {
                "name": "message",
                "type": "reference",
                "reference": "wf_context",
                "children": [],
            }
        ]
    created = await apply(
        tools,
        decode_response(
            await tools["flow_preview"](
                **create_request(workflow_inputs(wait=False), kind="RUNWORKFLOW")
            )
        ),
    )
    assert created["status"] == "success", created
    step = logic_step(model, "RUNWORKFLOW")
    set_saved_native_wait(step, "wait", native_wait)
    before = copy.deepcopy(model.flow)
    writes = copy.deepcopy(model.bodies)
    result = await preview_edit(
        tools,
        [
            {
                "op": "insert_action",
                "action_type_sys_id": ACTION,
                "after": step["id"],
                "inputs": {
                    "message": "{{" + step["uiUniqueIdentifier"] + "." + output + "}}"
                },
            }
        ],
    )
    assert result["status"] == "error", result
    assert (
        model.flow == before and model.bodies == writes and not len(coordinator.store)
    )


@pytest.mark.parametrize("native_wait", ["false", "0"])
async def test_saved_dynamic_false_wait_strings_expose_context_but_not_template_outputs(
    settings, native_wait
):
    model, coordinator, tools = setup(settings)
    model.action["inputs"] = [
        {
            "name": "message",
            "type": "reference",
            "reference": "sys_flow_context",
            "children": [],
        },
        {"name": "text", "type": "string", "children": []},
    ]
    created = await apply(
        tools,
        decode_response(
            await tools["flow_preview"](
                **create_request(dynamic_inputs(wait=False), kind="RUNDYNAMICFLOW")
            )
        ),
    )
    assert created["status"] == "success", created
    step = logic_step(model, "RUNDYNAMICFLOW")
    set_saved_native_wait(step, "wait_for_completion", native_wait)
    prefix = step["uiUniqueIdentifier"]
    accepted = await apply(
        tools,
        await preview_edit(
            tools,
            [
                {
                    "op": "insert_action",
                    "action_type_sys_id": ACTION,
                    "after": step["id"],
                    "inputs": {"message": "{{" + prefix + ".context}}"},
                }
            ],
        ),
    )
    assert accepted["status"] == "success", accepted
    assert (
        binding(logic_step(model, "RUNDYNAMICFLOW"), "wait_for_completion")["value"]
        == native_wait
    )
    before = copy.deepcopy(model.flow)
    writes = copy.deepcopy(model.bodies)
    rejected = await preview_edit(
        tools,
        [
            {
                "op": "insert_action",
                "action_type_sys_id": ACTION,
                "after": step["id"],
                "inputs": {"text": "{{" + prefix + ".answer}}"},
            }
        ],
    )
    assert rejected["status"] == "error", rejected
    assert (
        model.flow == before and model.bodies == writes and not len(coordinator.store)
    )


@pytest.mark.parametrize("value_type", ["object", "array.string"])
async def test_saved_dynamic_template_change_adopts_new_interface_and_keeps_complex_literals(
    settings, value_type
):
    model, _, tools = setup(settings)
    if value_type == "object":
        details = {
            "name": "details",
            "label": "Details",
            "type": "object",
            "mandatory": True,
            "children": [
                {"name": "text", "label": "Text", "type": "string", "children": []}
            ],
        }
        literal = {"text": "Keep this object value"}
        expected = ["Keep this object value"]
    else:
        details = copy.deepcopy(packed_array("details", "array.string"))
        details["mandatory"] = True
        literal = ["Keep", "these", "array values"]
        expected = copy.deepcopy(literal)
    old_inputs = [
        copy.deepcopy(details),
        {"name": "legacy", "label": "Legacy", "type": "string", "children": []},
    ]
    model.snapshots[TEMPLATE_SNAPSHOT]["inputs"] = copy.deepcopy(old_inputs)
    model.snapshots[TARGET_SNAPSHOT]["inputs"] = copy.deepcopy(old_inputs)
    inputs = {
        **dynamic_inputs(),
        "dynamic_inputs": {"details": copy.deepcopy(literal), "legacy": "Obsolete"},
    }
    created = await apply(
        tools,
        decode_response(
            await tools["flow_preview"](**create_request(inputs, kind="RUNDYNAMICFLOW"))
        ),
    )
    assert created["status"] == "success", created
    original = copy.deepcopy(logic_step(model, "RUNDYNAMICFLOW"))

    root_id, snapshot_id = "a0" * 16, "a1" * 16
    root = copy.deepcopy(model.flow_records[TEMPLATE])
    root.update(
        sys_id=root_id, name="Replacement template", master_snapshot=snapshot_id
    )
    snapshot = copy.deepcopy(model.snapshots[TEMPLATE_SNAPSHOT])
    snapshot.update(
        id=snapshot_id,
        parentFlow=root_id,
        name=root["name"],
        inputs=[
            copy.deepcopy(details),
            {
                "name": "confirmation",
                "label": "Confirmation",
                "type": "string",
                "mandatory": True,
                "children": [],
            },
        ],
        outputs=[
            {
                "name": "completion",
                "label": "Completion",
                "type": "string",
                "children": [],
            }
        ],
    )
    model.flow_records[root_id] = root
    model.snapshots[snapshot_id] = snapshot
    changed = await apply(
        tools,
        await preview_edit(
            tools,
            [
                {
                    "op": "update_inputs",
                    "step": original["id"],
                    "inputs": {
                        "flow_template": root_id,
                        "flow": root_id,
                        "dynamic_inputs": {"confirmation": "Changed"},
                    },
                }
            ],
        ),
    )
    assert changed["status"] == "success", changed
    step = logic_step(model, "RUNDYNAMICFLOW")
    assert step["id"] == original["id"]
    assert step["uiUniqueIdentifier"] == original["uiUniqueIdentifier"]
    assert binding(step, "flow_template")["value"] == root_id
    values = {row["name"]: row for row in step["dynamicInputs"]}
    assert set(values) == {"details", "confirmation"}
    assert [row["value"] for row in values["details"]["children"]] == expected
    assert values["confirmation"]["value"] == "Changed"
    assert {row["name"] for row in step["flowLogicDefinition"]["variables"]} == {
        "context",
        "completion",
    }


@pytest.mark.parametrize("referenced", [False, True])
async def test_get_flow_outputs_explicit_template_clear_removes_only_unreferenced_schema(
    settings, referenced
):
    model, coordinator, tools = setup(settings)
    request = create_request(
        {"flow_template": TEMPLATE, "context": CONTEXT}, kind="GETFLOWOUTPUTS"
    )
    if referenced:
        append_answer_consumer(request)
    created = await apply(
        tools, decode_response(await tools["flow_preview"](**request))
    )
    assert created["status"] == "success", created
    original = copy.deepcopy(logic_step(model, "GETFLOWOUTPUTS"))
    before = copy.deepcopy(model.flow)
    writes = copy.deepcopy(model.bodies)
    preview = await preview_edit(
        tools,
        [
            {
                "op": "update_inputs",
                "step": original["id"],
                "inputs": {"flow_template": ""},
            }
        ],
    )
    if referenced:
        assert preview["status"] == "error", preview
        assert (
            model.flow == before
            and model.bodies == writes
            and not len(coordinator.store)
        )
        return
    changed = await apply(tools, preview)
    assert changed["status"] == "success", changed
    step = logic_step(model, "GETFLOWOUTPUTS")
    assert step["id"] == original["id"]
    assert binding(step, "context")["value"] == CONTEXT
    assert binding(step, "flow_template")["value"] == ""
    assert step["dynamicOutputs"] == []
    assert step["flowLogicDefinition"]["variables"] == []
    before = copy.deepcopy(model.flow)
    writes = copy.deepcopy(model.bodies)
    invalid = await preview_edit(
        tools,
        [
            {
                "op": "insert_action",
                "action_type_sys_id": ACTION,
                "after": step["id"],
                "inputs": {"message": "{{" + step["uiUniqueIdentifier"] + ".answer}}"},
            }
        ],
    )
    assert invalid["status"] == "error", invalid
    assert (
        model.flow == before and model.bodies == writes and not len(coordinator.store)
    )


async def test_dynamic_flow_script_selector_can_omit_basic_target_and_survives_readback(
    settings,
):
    model, _, tools = setup(settings)
    source = "return 'Runtime selected';"
    request = create_request(dynamic_inputs(), kind="RUNDYNAMICFLOW")
    request["edits"][0]["inputs"].pop("flow")
    request["edits"][0]["input_scripts"] = {"flow": source}
    result = await apply(tools, decode_response(await tools["flow_preview"](**request)))
    assert result["status"] == "success", result
    step = logic_step(model, "RUNDYNAMICFLOW")
    selector = binding(step, "flow")
    assert selector["value"] == ""
    assert selector["scriptActive"] is True
    assert selector["script"] == {
        "flow": {"script": source, "scriptActive": True, "showScript": True}
    }
    identity = step["id"]
    updated_source = "return 'Another runtime selection';"
    edited = await apply(
        tools,
        await preview_edit(
            tools,
            [
                {
                    "op": "update_inputs",
                    "step": identity,
                    "inputs": {},
                    "input_scripts": {"flow": updated_source},
                }
            ],
        ),
    )
    assert edited["status"] == "success", edited
    step = logic_step(model, "RUNDYNAMICFLOW")
    assert step["id"] == identity
    assert binding(step, "flow")["value"] == ""
    assert binding(step, "flow")["script"] == {
        "flow": {"script": updated_source, "scriptActive": True, "showScript": True}
    }
    basic = await apply(
        tools,
        await preview_edit(
            tools,
            [
                {
                    "op": "update_inputs",
                    "step": identity,
                    "inputs": {"flow": TARGET},
                    "input_scripts": {"flow": None},
                }
            ],
        ),
    )
    assert basic["status"] == "success", basic
    selector = binding(logic_step(model, "RUNDYNAMICFLOW"), "flow")
    assert selector["value"] == TARGET
    assert selector["scriptActive"] is False
    assert selector["script"]["flow"]["script"] == updated_source


@pytest.mark.parametrize("operation", ["insert", "update"])
@pytest.mark.parametrize(
    ("kind", "field", "source"),
    [
        ("RUNDYNAMICFLOW", "flow_template", "return 'template';"),
        ("RUNDYNAMICFLOW", "wait_for_completion", "return true;"),
        ("TIMER", "timer_duration", "return '1970-01-01 00:00:02';"),
    ],
)
async def test_utility_static_configuration_and_non_scriptable_timer_reject_modes(
    settings, operation, kind, field, source
):
    model, coordinator, tools = setup(settings)
    inputs = {
        "RUNDYNAMICFLOW": dynamic_inputs(),
        "RUNWORKFLOW": workflow_inputs(),
        "TIMER": copy.deepcopy(EXPLICIT),
    }[kind]
    request = create_request(inputs, kind=kind)
    if operation == "insert":
        request["edits"][0]["input_scripts"] = {field: source}
        result = decode_response(await tools["flow_preview"](**request))
        assert result["status"] == "error", result
        assert model.flow is None and not model.bodies and not len(coordinator.store)
        return
    created = await apply(
        tools, decode_response(await tools["flow_preview"](**request))
    )
    assert created["status"] == "success", created
    step = logic_step(model, kind)
    before = copy.deepcopy(model.flow)
    writes = copy.deepcopy(model.bodies)
    result = await preview_edit(
        tools,
        [
            {
                "op": "update_inputs",
                "step": step["id"],
                "inputs": {},
                "input_scripts": {field: source},
            }
        ],
    )
    assert result["status"] == "error", result
    assert (
        model.flow == before and model.bodies == writes and not len(coordinator.store)
    )


async def test_workflow_element_identifies_inputs_without_rewriting_native_parameter_name(
    settings,
):
    model, _, tools = setup(settings)
    descriptor = model.workflow_config[0]["inputs"][0]
    descriptor.update(element="message", name="legacy_parameter_id")
    native = copy.deepcopy(descriptor)
    created = await apply(
        tools,
        decode_response(
            await tools["flow_preview"](
                **create_request(workflow_inputs(), kind="RUNWORKFLOW")
            )
        ),
    )
    assert created["status"] == "success", created
    step = logic_step(model, "RUNWORKFLOW")
    message = next(row for row in step["workflowInputs"] if row["name"] == "message")
    assert message["value"] == "Dispatch"
    assert message["parameter"] == native
    identity = step["id"]
    changed = await apply(
        tools,
        await preview_edit(
            tools,
            [
                {
                    "op": "update_inputs",
                    "step": identity,
                    "inputs": {"workflow_inputs": {"message": "Changed"}},
                }
            ],
        ),
    )
    assert changed["status"] == "success", changed
    step = logic_step(model, "RUNWORKFLOW")
    assert step["id"] == identity
    assert [row["name"] for row in step["workflowInputs"]] == ["message"]
    message = step["workflowInputs"][0]
    assert message["value"] == "Changed"
    assert message["parameter"] == native


async def test_dynamic_flow_published_template_and_target_do_not_require_active_flag(
    settings,
):
    model, _, tools = setup(settings)
    model.flow_records[TEMPLATE]["active"] = "false"
    model.flow_records[TARGET]["active"] = "false"
    result = await apply(
        tools,
        decode_response(
            await tools["flow_preview"](
                **create_request(dynamic_inputs(), kind="RUNDYNAMICFLOW")
            )
        ),
    )
    assert result["status"] == "success", result
    step = logic_step(model, "RUNDYNAMICFLOW")
    assert binding(step, "flow_template")["value"] == TEMPLATE
    assert binding(step, "flow")["value"] == TARGET
