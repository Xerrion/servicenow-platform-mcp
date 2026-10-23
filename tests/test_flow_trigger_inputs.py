"""Catalog-owned Service Catalog call inputs, not authored subflow interfaces."""

import copy
from typing import Any, override

import httpx2
import pytest

from servicenow_mcp.capabilities.flows import editing, semantics, triggers
from tests._complete_flow_fixture import (
    ACTION,
    CATALOG_INPUTS,
    FLOW,
    STUDIO,
    create_request,
)
from tests.helpers import decode_response
from tests.test_flow_complete_creation import apply, setup
from tests.test_flow_editing import DEFS
from tests.test_flow_root_cache import NativeCacheModel
from tests.test_flow_trigger_authoring import categories


def definition():
    return editing.trigger_catalog(copy.deepcopy(DEFS["trigger_categories"]))[
        "service_catalog"
    ]


def test_independent_catalog_projects_exactly_to_complete_observed_native_input_dtos():
    actual = copy.deepcopy(CATALOG_INPUTS)
    for row in actual:
        row.pop("id")
    assert triggers.service_catalog_inputs(definition()) == actual
    renamed = definition()
    for index, row in enumerate(renamed["outputs"]):
        row["name"] = row["parameter"]["name"] = f"catalog_supplied_{index}"
    projected = triggers.service_catalog_inputs(renamed)
    assert {r["name"] for r in projected} == {
        r["parameter"]["name"]
        for r in renamed["outputs"]
        if r["type"] in {"reference", "table_name"}
    }


class NativeInputModel(NativeCacheModel):
    input_defect = None
    input_race = False
    raced = False
    test_categories: list[dict[str, Any]] | None = None

    @override
    def handle(self, request):
        prefix = "/api/now/table/sys_hub_flow_input/"
        if (
            request.method == "GET"
            and request.url.path == "/api/now/table/sys_dictionary"
        ):
            self.calls.append((request.method, request.url.path))
            return httpx2.Response(
                200,
                json={
                    "result": [
                        {
                            "name": "sc_req_item",
                            "element": "number",
                            "column_label": "Number",
                            "internal_type": "string",
                            "reference": "",
                            "choice": "0",
                        }
                    ]
                },
                request=request,
            )
        if (
            request.method == "GET"
            and request.url.path == "/api/now/trigger_api/trigger_categories"
            and self.test_categories is not None
        ):
            self.calls.append((request.method, request.url.path))
            return httpx2.Response(
                200, json={"result": self.test_categories}, request=request
            )
        if (
            request.method == "GET"
            and request.url.path == STUDIO + "/flow/" + FLOW
            and self.input_race
            and len(self.bodies) >= 2
            and not self.raced
        ):
            assert self.flow is not None
            self.flow["inputs"][0]["id"] = self.identity()
            self.raced = True
        response = super().handle(request)
        if (
            request.method == "GET"
            and request.url.path.startswith(prefix)
            and self.input_defect == "owner"
        ):
            row = response.json()["result"]
            row["model"] = "f" * 32
            return httpx2.Response(200, json={"result": row}, request=request)
        if (
            request.method == "PUT"
            and request.url.path == STUDIO + "/flow"
            and self.input_defect
        ):
            echo = response.json()["result"]["data"]
            assert self.flow is not None
            for flow in (echo, self.flow):
                if self.input_defect == "extra":
                    extra = copy.deepcopy(flow["inputs"][0])
                    extra.update(id="f" * 32, name="unexpected")
                    flow["inputs"].append(extra)
                elif self.input_defect == "missing":
                    flow["inputs"].pop()
                elif self.input_defect == "type":
                    flow["inputs"][1]["type"] = "string"
                elif self.input_defect == "reference":
                    flow["inputs"][1]["reference"] = "sys_user"
                elif self.input_defect == "value":
                    flow["inputs"][1]["value"] = "unexpected"
                elif self.input_defect == "default":
                    flow["inputs"][0]["defaultValue"] = "wrong_table"
                elif self.input_defect == "unknown":
                    flow["inputs"][0]["attributes"]["unexpected"] = True
                elif self.input_defect == "duplicate_id":
                    flow["inputs"][1]["id"] = flow["inputs"][0]["id"]
                elif self.input_defect == "other_setting":
                    flow["actionInstances"][0]["metadata"]["unexpected"] = True
            return httpx2.Response(
                200, json={"result": {"data": echo}}, request=request
            )
        return response


def seven_request():
    def log(ref, message, **placement):
        return {
            "op": "insert_action",
            "action_type_sys_id": ACTION,
            "ref": ref,
            "inputs": {"message": message},
            **placement,
        }

    return {
        "action": "create",
        "name": "Synthetic seven-step acceptance",
        "scope": "global",
        "expected_scope": "global",
        "edits": [
            {"op": "set_trigger", "type": "service_catalog"},
            {
                "op": "create_field",
                "collection": "flowVariables",
                "name": "message",
                "label": "Message",
                "type": "string",
            },
            {"op": "set_variables", "inputs": {"message": "Synthetic"}},
            log("start", "{{flow_variable.message}}"),
            {
                "op": "insert_logic",
                "logic": "If",
                "ref": "branch",
                "inputs": {
                    "condition": "{{Service Catalog_1.request_item.number}}ISNOTEMPTY"
                },
            },
            log("then", "Synthetic yes", parent="branch"),
            {
                "op": "insert_logic",
                "logic": "Else",
                "after": "branch",
                "ref": "otherwise",
            },
            log("skip", "Synthetic no", parent="otherwise"),
            log("tail", "Synthetic end", after="otherwise"),
        ],
    }


async def test_nine_edit_seven_step_create_verifies_generated_inputs_and_preserves_later_edits(
    settings,
):
    _, _, tools = setup(settings)
    model = NativeInputModel()
    model.action["name"] = "Log"
    model.install()
    preview = decode_response(await tools["flow_preview"](**seven_request()))
    assert preview["status"] == "success", preview
    assert {r["name"] for r in preview["data"]["summary"]["fields"]["inputs"]} == {
        "table_name",
        "request_item",
    }
    created = await apply(tools, preview)
    assert created["status"] == "success", created
    assert model.flow is not None
    assert (
        len(model.flow["actionInstances"]) == 4
        and len(model.flow["flowLogicInstances"]) == 3
    )
    refs = created["data"]["refs"]
    nodes = {
        row["uiUniqueIdentifier"]: row
        for section in ("actionInstances", "flowLogicInstances")
        for row in model.flow[section]
    }
    assert (
        nodes[refs["start"]["ui_id"]]["inputs"][0]["value"]
        == "{{flow_variable.message}}"
    )
    condition = next(
        row
        for row in nodes[refs["branch"]["ui_id"]]["inputs"]
        if row["name"] == "condition"
    )
    assert condition["value"] == "{{Service Catalog_1.request_item.number}}ISNOTEMPTY"
    assert nodes[refs["then"]["ui_id"]]["parent"] == refs["branch"]["ui_id"]
    assert nodes[refs["skip"]["ui_id"]]["parent"] == refs["otherwise"]["ui_id"]
    assert model.bodies[0].get("inputs", []) == [] and model.bodies[1]["inputs"] == []
    before = copy.deepcopy(model.flow["inputs"])
    unbound = copy.deepcopy(model.flow)
    unbound["inputs"] = []
    with pytest.raises(semantics.SemanticMismatch, match="COUNT_MISMATCH"):
        semantics.compare_saved(unbound, model.flow, generated_ids=set())
    for row in model.flow["inputs"]:
        row["attributes"]["opaqueExisting"] = {"preserve": True}
    wanted = copy.deepcopy(model.flow["inputs"])
    result = await apply(
        tools,
        decode_response(
            await tools["flow_preview"](
                action="edit",
                sys_id=FLOW,
                expected_scope="global",
                expected_revision=created["data"]["revision"],
                edits=[
                    {
                        "op": "update_inputs",
                        "step": created["data"]["refs"]["tail"]["ui_id"],
                        "inputs": {"message": "Updated synthetic"},
                    }
                ],
            )
        ),
    )
    assert result["status"] == "success", result
    assert model.flow["inputs"] == wanted and len({r["id"] for r in before}) == 2
    assert any(
        path.startswith("/api/now/table/sys_hub_flow_input/")
        for method, path in model.calls
        if method == "GET"
    )


@pytest.mark.parametrize(
    "defect",
    [
        "extra",
        "missing",
        "type",
        "reference",
        "value",
        "default",
        "unknown",
        "duplicate_id",
        "owner",
        "other_setting",
    ],
)
async def test_generated_input_contract_does_not_hide_drift_or_wrong_ownership(
    settings, defect
):
    _, _, tools = setup(settings)
    model = NativeInputModel()
    model.input_defect = defect
    model.install()
    result = await apply(
        tools, decode_response(await tools["flow_preview"](**create_request()))
    )
    assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    assert result["error"]["outcome"]["header"] == "verified"
    assert (
        model.calls.count(("POST", STUDIO + "/flow")) == 1
        and model.calls.count(("PUT", STUDIO + "/flow")) == 1
    )


async def test_generated_input_ids_must_agree_between_echo_and_independent_get(
    settings,
):
    _, _, tools = setup(settings)
    model = NativeInputModel()
    model.input_race = True
    model.install()
    result = await apply(
        tools, decode_response(await tools["flow_preview"](**create_request()))
    )
    assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    assert "echo.readback" in result["error"]["message"]


@pytest.mark.parametrize("kind", ["daily", "subflow"])
async def test_different_trigger_and_authored_triggerless_subflow_are_not_reconciled(
    settings, kind
):
    _, _, tools = setup(settings)
    model = NativeInputModel()
    model.install()
    request = create_request(subflow=kind == "subflow")
    if kind == "daily":
        model.test_categories = categories()
        request["edits"][0] = {
            "op": "set_trigger",
            "type": "daily",
            "inputs": {"glide_time": "1970-01-01 09:00:00"},
        }
    result = await apply(tools, decode_response(await tools["flow_preview"](**request)))
    assert result["status"] == "success", result
    assert model.flow is not None
    assert [r["name"] for r in model.flow["inputs"]] == (
        ["request"] if kind == "subflow" else []
    )
    assert not any(
        path.startswith("/api/now/table/sys_hub_flow_input/")
        for method, path in model.calls
    )


async def test_implicit_input_table_policy_blocks_before_any_creation(
    settings, monkeypatch
):
    from servicenow_mcp import policy

    _, _, tools = setup(settings)
    model = NativeInputModel()
    model.install()
    monkeypatch.setattr(
        policy, "DENIED_TABLES", policy.DENIED_TABLES | {"sys_hub_flow_input"}
    )
    result = decode_response(await tools["flow_preview"](**create_request()))
    assert result["status"] == "error" and not model.calls and not model.bodies
