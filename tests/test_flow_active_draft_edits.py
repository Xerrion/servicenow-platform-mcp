"""Offline operation round trips, not observations of remote metadata behavior.

The HTTP model assigns only the identities already supported by the strict
comparator. It preserves other requested metadata on success. Separate drift
cases prove that these synthetic successes do not authorize normalization of
unknown metadata or changes to existing eligibility rules.
"""

import copy
from typing import Any

import pytest

from servicenow_mcp.capabilities.flows import active_drafts, editing
from tests._mock_transport import http_mock
from tests.test_flow_active_drafts import ActiveModel
from tests.test_flow_editing import BASE
from tests.test_flow_error_authoring import definitions
from tests.test_flow_trigger_authoring import categories

KINDS = (
    "structural",
    "trigger_update",
    "trigger_replace",
    "stage",
    "handler",
    "declaration",
)
SAVED_TRIGGER = "8" * 32


class AssignedActiveModel(ActiveModel):
    """Add bounded server identities to the existing synthetic active HTTP model."""

    def __init__(self, settings, monkeypatch):
        super().__init__(settings, monkeypatch)
        self.next_id = 1000
        self.after_save = self.assign_identities
        http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
            200, json={"result": definitions()}
        )
        http_mock.get(f"{BASE}/api/now/trigger_api/trigger_categories").respond(
            200, json={"result": categories()}
        )

    def identity(self):
        self.next_id += 1
        return f"{self.next_id:032x}"

    def assign_identities(self):
        """Simulate existing ID normalization, never arbitrary schema normalization."""
        for collection in (
            "actionInstances",
            "flowLogicInstances",
            "subFlowInstances",
            "triggerInstances",
        ):
            for row in self.flow[collection]:
                if not row.get("id"):
                    row["id"] = self.identity()
        for stage in (
            self.flow["stages"].values()
            if isinstance(self.flow["stages"], dict)
            else []
        ):
            if not stage.get("id"):
                stage["id"] = self.identity()
        assigned = {}
        for row in self.flow["flowVariables"]:
            if not row.get("id"):
                uid = row["attributes"]["uiUniqueId"]
                row["id"] = self.identity()
                row.pop("uiUniqueId", None)
                assigned[uid] = row["id"]
        for step in self.flow["flowLogicInstances"]:
            if step.get("flowLogicDefinition", {}).get("type") != "SETFLOWVARIABLES":
                continue
            for collection in ("inputs", "flowVariables"):
                for binding in step[collection]:
                    parameter = binding["parameter"]
                    uid = parameter.get("attributes", {}).get("uiUniqueId")
                    if uid in assigned:
                        parameter.pop("uiUniqueId", None)
                        parameter["id"] = assigned[uid]
        # Authorized tombstones may clear on readback. The deleted instance
        # must remain absent, which the production comparator checks separately.
        for key in ("deletedActions", "deletedFlowLogicInstances", "deletedTriggers"):
            if key in self.flow:
                self.flow[key] = []


def operation(model, kind):
    if kind == "structural":
        return [
            {
                "op": "insert_logic",
                "logic": "If",
                "before": "1",
                "ref": "branch",
                "inputs": {"condition": "true"},
            },
            {"op": "move_step", "step": "ui-1", "parent": "branch"},
        ]
    if kind.startswith("trigger_"):
        trigger = editing.new_trigger(
            model.flow,
            editing.trigger_catalog(categories())["daily"],
            {"glide_time": "1970-01-01 08:00:00"},
        )
        trigger["id"] = SAVED_TRIGGER
        model.flow["triggerInstances"] = [trigger]
        if kind == "trigger_update":
            return [
                {
                    "op": "update_trigger",
                    "inputs": {"glide_time": "1970-01-01 09:00:00"},
                }
            ]
        return [
            {
                "op": "set_trigger",
                "type": "weekly",
                "replace": True,
                "inputs": {"day_of_week": "2", "glide_time": "1970-01-01 09:00:00"},
            }
        ]
    if kind == "stage":
        return [
            {
                "op": "create_stage",
                "label": "Synthetic work",
                "value": "synthetic_work",
                "ref": "work",
            },
            {"op": "update_stage", "stage": "work", "always_show": False},
            {"op": "attach_stage", "stage": "work", "step": "ui-1"},
        ]
    if kind == "handler":
        handler_step = copy.deepcopy(model.flow["actionInstances"][0])
        handler_step.update(id="9" * 32, uiUniqueIdentifier="ui-handler", order=2)
        model.flow["actionInstances"].append(handler_step)
        return [
            {"op": "set_error_handler", "enabled": True},
            {"op": "move_step", "step": "ui-handler", "parent": "error_handler"},
        ]
    assert kind == "declaration"
    return [
        {
            "op": "create_field",
            "collection": "flowVariables",
            "name": "total",
            "label": "Total",
            "type": "integer",
        },
        {
            "op": "update_field",
            "collection": "flowVariables",
            "name": "total",
            "label": "Planned total",
        },
        {"op": "set_variables", "inputs": {"total": 17}, "before": "ui-1"},
        {"op": "delete_field", "collection": "flowVariables", "name": "counter"},
    ]


def edited_row(flow: dict[str, Any], kind: str):
    if kind == "structural":
        return flow["flowLogicInstances"][0]
    if kind.startswith("trigger_"):
        return flow["triggerInstances"][0]
    if kind == "stage":
        return next(iter(flow["stages"].values()))
    if kind == "handler":
        return next(
            row
            for row in flow["flowLogicInstances"]
            if row["flowLogicDefinition"]["type"] == "TOP_LEVEL_CATCH"
        )
    return next(row for row in flow["flowVariables"] if row["name"] == "total")


def assert_successful_edit(model, kind, result):
    wire = model.writes[0]
    row, wanted = edited_row(model.flow, kind), edited_row(wire, kind)
    assert row["id"] and len(row["id"]) == 32
    if kind != "trigger_update":
        assert not wanted.get("id") and row["id"] != wanted.get("id")
    if kind == "structural":
        assert row["children"] == ["ui-1"]
        assert model.flow["actionInstances"][0]["parent"] == row["uiUniqueIdentifier"]
        assert result["data"]["refs"]["branch"]["sys_id"] == row["id"]
        assert result["data"]["refs"]["branch"]["ui_id"] == wanted["uiUniqueIdentifier"]
    elif kind.startswith("trigger_"):
        inputs = {item["name"]: item["value"] for item in row["inputs"]}
        assert inputs["glide_time"] == "1970-01-01 09:00:00"
        if kind == "trigger_update":
            assert row["id"] == SAVED_TRIGGER and row["type"] == "daily"
        else:
            assert row["id"] != SAVED_TRIGGER and row["type"] == "weekly"
            assert inputs["day_of_week"] == "2"
            assert wire["deletedTriggers"] == [{"id": SAVED_TRIGGER}]
            assert model.flow["deletedTriggers"] == []
    elif kind == "stage":
        assert row["stageId"] == wanted["stageId"]
        assert row["componentIndexes"] == [0] and row["alwaysShow"] is False
        assert result["data"]["stage_refs"]["work"] == row["stageId"]
    elif kind == "handler":
        wrappers = {
            item["flowLogicDefinition"]["type"]: item
            for item in model.flow["flowLogicInstances"]
        }
        assert set(wrappers) == {"TOP_LEVEL_TRY", "TOP_LEVEL_CATCH"}
        assert wrappers["TOP_LEVEL_TRY"]["order"] == 0
        assert wrappers["TOP_LEVEL_CATCH"]["children"] == ["ui-handler"]
        assert (
            next(item for item in row["inputs"] if item["name"] == "enabled")["value"]
            is True
        )
        assert len({item["id"] for item in wrappers.values()}) == 2
    else:
        assert row["label"] == "Planned total" and row["type"] == "integer"
        assert [item["name"] for item in model.flow["flowVariables"]] == ["total"]
        assert (
            "uiUniqueId" not in row
            and row["attributes"]["uiUniqueId"] == wanted["uiUniqueId"]
        )
        setter = model.flow["flowLogicInstances"][0]
        assert setter["inputs"] == setter["flowVariables"]
        parameter = setter["inputs"][0]["parameter"]
        assert (
            parameter["id"] == row["id"]
            and parameter["attributes"]["uiUniqueId"] == row["attributes"]["uiUniqueId"]
        )
        assert setter["inputs"][0]["value"] == 17


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "result_kind", ["success", "unknown_metadata", "assigned_identity_drift"]
)
async def test_active_existing_edit_roundtrip_and_strict_readback(
    settings, monkeypatch, kind, result_kind
):
    model = AssignedActiveModel(settings, monkeypatch)
    edits = operation(model, kind)
    runtime = copy.deepcopy((model.snapshot, model.plans, model.trigger, model.main))
    if result_kind == "unknown_metadata":

        def drift():
            model.assign_identities()
            row = edited_row(model.flow, kind)
            metadata = row["attributes"] if kind == "declaration" else row
            metadata["unprovenMetadata"] = {"synthetic": "not normalized"}

        model.after_save = drift
    elif result_kind == "assigned_identity_drift":

        def drift_echo(echo):
            row = edited_row(echo, kind)
            row["id"] = "a1" * 16
            if kind == "declaration":
                for step in echo["flowLogicInstances"]:
                    if step["flowLogicDefinition"]["type"] == "SETFLOWVARIABLES":
                        for collection in ("inputs", "flowVariables"):
                            for binding in step[collection]:
                                if binding["name"] == "total":
                                    binding["parameter"]["id"] = row["id"]

        model.change_echo = drift_echo
    before = copy.deepcopy(model.flow)
    preview = await model.preview(edits=edits)
    assert preview["status"] == "success", preview
    assert not model.writes and model.flow == before
    assert all(call.request.method == "GET" for call in http_mock.calls)
    result = await model.apply(preview)
    if result_kind == "success":
        assert result["status"] == "success", result
        assert result["data"]["active"] is True and result["data"]["status"] == "draft"
        assert result["data"]["revision"] == "8" and result["data"]["version"] == "13"
        assert result["data"]["runtime_preservation"] == "verified"
        assert_successful_edit(model, kind, result)
    else:
        assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
        assert result["error"]["outcome"] == {
            "runtime_preservation": "verified",
            "configuration": "unverified",
        }
        assert "configuration verification" in result["error"]["message"]
        if result_kind == "assigned_identity_drift" and kind != "trigger_update":
            assert "echo.readback" in result["error"]["message"], result
    assert len(model.writes) == 1
    assert all(
        model.writes[0][key] == before[key]
        for key in active_drafts.LIFECYCLE
        if key != "status"
    )
    assert (model.snapshot, model.plans, model.trigger, model.main) == runtime
    assert all(
        call.request.method == "GET"
        or call.request.method == "PUT"
        and call.request.url.path == "/api/now/processflow/flow"
        for call in http_mock.calls
    )
    assert all(
        "sys_update_" not in call.request.url.path
        and "concoursepicker" not in call.request.url.path
        for call in http_mock.calls
    )
    assert (await model.apply(preview))["status"] == "error"
    assert len(model.writes) == 1


async def test_new_variable_mirrored_parameter_metadata_remains_strict(
    settings, monkeypatch
):
    model = AssignedActiveModel(settings, monkeypatch)
    edits = operation(model, "declaration")

    def drift():
        model.assign_identities()
        setter = model.flow["flowLogicInstances"][0]
        for collection in ("inputs", "flowVariables"):
            setter[collection][0]["parameter"]["attributes"]["unprovenMetadata"] = (
                "not normalized"
            )

    model.after_save = drift
    preview = await model.preview(edits=edits)
    assert preview["status"] == "success", preview
    result = await model.apply(preview)
    assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    assert result["error"]["outcome"] == {
        "runtime_preservation": "verified",
        "configuration": "unverified",
    }
    assert "configuration verification" in result["error"]["message"]
    assert len(model.writes) == 1
    assert (await model.apply(preview))["status"] == "error"
    assert len(model.writes) == 1


@pytest.mark.parametrize(
    "edit, reason",
    [
        (
            {
                "op": "create_field",
                "collection": "inputs",
                "name": "new_input",
                "label": "Input",
                "type": "integer",
            },
            "subflow",
        ),
        (
            {
                "op": "create_field",
                "collection": "flowVariables",
                "name": "nested",
                "label": "Nested",
                "type": "object",
            },
            "unavailable",
        ),
        ({"op": "remove_error_handler"}, "no error handler"),
    ],
)
async def test_active_operation_exclusions_remain_explicit(
    settings, monkeypatch, edit, reason
):
    model = AssignedActiveModel(settings, monkeypatch)
    result = await model.preview(edits=[edit])
    assert result["status"] == "error", result
    assert reason in result["error"]["message"].lower(), result
    assert len(model.coordinator.store) == 0 and not model.writes
    assert all(call.request.method == "GET" for call in http_mock.calls)
