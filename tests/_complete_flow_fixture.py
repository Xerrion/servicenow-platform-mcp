"""Stateful source-derived HTTP model for complete-authoring wire tests. No live data."""

import copy
import json
import re
from typing import Any

import httpx2

from servicenow_mcp.capabilities.flows import declarations, tree
from tests._mock_transport import http_mock
from tests.test_flow_editing import DEFS, FLOW, base_flow
from tests.test_flow_error_authoring import definitions
from tests.test_flow_output_assignments import definitions as output_definitions

ACTION, SUBFLOW, SNAPSHOT, UPDATE_SET = "b" * 32, "c" * 32, "d" * 32, "e" * 32
STUDIO = "/api/now/processflow"


def create_request(*, subflow: bool = False) -> dict[str, Any]:
    edits: list[dict[str, Any]] = [
        {
            "op": "create_field",
            "collection": "flowVariables",
            "name": "message",
            "label": "Message",
            "type": "string",
        },
        {"op": "set_variables", "inputs": {"message": "Dispatch"}},
        {
            "op": "insert_action",
            "action_type_sys_id": ACTION,
            "ref": "source",
            "inputs": {"message": "{{flow_variable.message}}"},
        },
        {
            "op": "insert_logic",
            "logic": "If",
            "ref": "branch",
            "inputs": {"condition": "{{source.text}}ISNOTEMPTY"},
        },
        {
            "op": "insert_subflow",
            "subflow_sys_id": SUBFLOW,
            "parent": "branch",
            "ref": "dispatch",
            "inputs": {"message": "{{source.text}}"},
        },
        {
            "op": "insert_action",
            "action_type_sys_id": ACTION,
            "parent": "branch",
            "ref": "audit",
            "inputs": {"message": "{{dispatch.text}}"},
        },
        {"op": "insert_logic", "logic": "Else", "after": "branch", "ref": "otherwise"},
        {
            "op": "insert_action",
            "action_type_sys_id": ACTION,
            "parent": "otherwise",
            "ref": "skip",
            "inputs": {"message": "Skipped"},
        },
        {
            "op": "insert_action",
            "action_type_sys_id": ACTION,
            "after": "otherwise",
            "ref": "tail",
            "inputs": {"message": "Done"},
        },
    ]
    if subflow:
        edits = [
            {
                "op": "create_field",
                "collection": "inputs",
                "name": "request",
                "label": "Request",
                "type": "string",
                "mandatory": True,
            },
            {
                "op": "create_field",
                "collection": "outputs",
                "name": "result",
                "label": "Result",
                "type": "string",
            },
            *edits,
            {
                "op": "assign_outputs",
                "ref": "result",
                "outputs": {"result": "{{subflow.request}}"},
            },
        ]
    else:
        edits = [
            {"op": "set_trigger", "type": "service_catalog"},
            *edits,
            {"op": "create_stage", "ref": "work", "label": "Work", "value": "work"},
            {"op": "attach_stage", "stage": "work", "step": "source"},
        ]
    return {
        "action": "create",
        "name": "Dispatch request",
        "scope": "global",
        "flow_type": "subflow" if subflow else "flow",
        "edits": edits,
        "expected_scope": "global",
    }


def edit_request(created: dict[str, Any]) -> dict[str, Any]:
    refs = created["refs"]
    return {
        "action": "edit",
        "sys_id": created["sys_id"],
        "expected_scope": "global",
        "expected_revision": created["revision"],
        "edits": [
            {
                "op": "move_step",
                "step": refs["tail"]["ui_id"],
                "before": refs["branch"]["ui_id"],
            },
            {
                "op": "insert_action",
                "action_type_sys_id": ACTION,
                "ref": "inserted",
                "after": refs["source"]["ui_id"],
                "inputs": {"message": "{{" + refs["source"]["ui_id"] + ".text}}"},
            },
            {
                "op": "update_inputs",
                "step": refs["tail"]["sys_id"],
                "inputs": {"message": "Completed"},
            },
            {"op": "delete_step", "step": refs["audit"]["sys_id"]},
        ],
    }


class StudioModel:
    def __init__(self) -> None:
        self.flow: dict[str, Any] | None = None
        self.revision = 12  # Deliberately not the commonly guessed zero/one.
        self.capture_revision = -1
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[dict[str, Any]] = []
        self.defect: str | None = None
        self.action = {
            "id": ACTION,
            "name": "Format message",
            "scope": "global",
            "access": "public",
            "state": "published",
            "is_snapshot": True,
            "entitled": True,
            "security": {"can_read": True},
            "inputs": [
                {"id": "1" * 32, "name": "message", "type": "string", "children": []}
            ],
            "outputs": [{"name": "text", "type": "string", "children": []}],
        }
        self.subflow = {
            **copy.deepcopy(self.action),
            "id": SNAPSHOT,
            "parentFlow": SUBFLOW,
            "name": "Dispatch",
            "type": "subflow",
            "snapshot": True,
            "masterSnapshot": True,
            "isFlowEntitled": True,
        }
        self.logic = definitions() + [output_definitions()[-1]]
        self.context = {"scope": "global", "update_set": UPDATE_SET}
        self.scope_record = {"sys_id": "global", "name": "Global"}
        self.next_id = 100
        self.component_mirror = False

    def install(self) -> None:
        http_mock.route(url=re.compile(r"https://test\.service-now\.com/")).mock(
            side_effect=self.handle
        )

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        if "sys_update_" in path or "concoursepicker" in path:
            raise AssertionError(
                "Flow operations must not read update sets or capture metadata"
            )
        result: Any
        if method == "POST" and path == STUDIO + "/flow":
            body = json.loads(request.content)
            self.bodies.append(body)
            if self.defect == "post_timeout":
                raise httpx2.ReadTimeout("private-body-marker", request=request)
            self.flow = base_flow()
            self.flow.update(
                body,
                id=FLOW,
                actionInstances=[],
                subFlowInstances=[],
                stages={},
                serverSetting={"opaque": "preserve"},
            )
            self.advance()
            if self.component_mirror:
                self.flow["componentInstances"] = []
            if self.defect == "post_dependency":
                self.action["description"] = "changed after header creation"
            elif self.defect == "header_mirror":
                self.flow["componentInstances"] = [{"opaque": "unknown"}]
            result = {"data": self.flow}
        elif method == "PUT" and path == STUDIO + "/flow":
            body = json.loads(request.content)
            self.bodies.append(body)
            if self.defect == "put_timeout":
                raise httpx2.ReadTimeout("private-body-marker", request=request)
            if self.defect == "put_rejected":
                return httpx2.Response(
                    500, json={"error": "private-body-marker"}, request=request
                )
            for section in (*tree.COLLECTIONS.values(), "triggerInstances"):
                for row in body[section]:
                    if not row.get("id"):
                        row["id"] = self.identity()
            for section in ("inputs", "outputs", "flowVariables"):
                for _, row in declarations.walk(body[section]):
                    if row.get("uiUniqueId") and not row.get("id"):
                        row["id"] = self.identity()
            for row in body["stages"].values():
                row.setdefault("id", self.identity())
            if self.component_mirror:
                body["componentInstances"] = copy.deepcopy(
                    sorted(
                        (row for key in tree.COLLECTIONS.values() for row in body[key]),
                        key=lambda row: int(row["order"]),
                    )
                )
            for key in (
                "deletedActions",
                "deletedTriggers",
                "deletedFlowLogicInstances",
            ):
                body.pop(key, None)
            self.flow = copy.deepcopy(body)
            self.advance()
            if self.defect == "capture_drift":
                self.capture_revision += 1
            result = {"data": body}
            if self.defect == "readback_value":
                self.flow["actionInstances"][0]["inputs"][0]["value"] = "corrupt"
            elif self.defect == "readback_id":
                self.flow["actionInstances"][0]["id"] = self.identity()
            elif self.defect == "readback_setting":
                self.flow["serverSetting"] = {"opaque": "corrupt"}
            elif self.defect == "readback_parent":
                self.flow["subFlowInstances"][0]["parent"] = ""
            elif self.defect == "readback_active":
                self.flow["active"] = True
            elif self.defect == "readback_mirror":
                self.flow["componentInstances"][0]["mirrorSetting"] = (
                    "private-body-marker"
                )
            elif self.defect == "readback_mirror_id":
                self.flow["componentInstances"][0]["id"] = self.identity()
        elif method == "GET" and path == STUDIO + "/flow/" + FLOW:
            assert self.flow is not None
            if (
                self.defect == "header_changed"
                and self.calls.count((method, path)) == 2
            ):
                self.flow["serverSetting"] = {"opaque": "concurrent change"}
            result = {"data": self.flow}
        elif method == "GET" and path == STUDIO + "/action/action_types/" + ACTION:
            result = self.action
        elif method == "GET" and path == STUDIO + "/flow/" + SNAPSHOT:
            result = {"data": self.subflow}
        elif method == "GET" and path == STUDIO + "/flow_logic/types":
            result = self.logic
        elif method == "GET" and path == "/api/now/trigger_api/trigger_categories":
            result = DEFS["trigger_categories"]
        elif method == "GET" and path == "/api/now/ui/concoursepicker/current":
            result = {
                "currentApplication": {"sysId": self.context["scope"]},
                "currentUpdateSet": {"sysId": self.context["update_set"]},
            }
        elif method == "GET" and path == "/api/now/table/sys_scope/global":
            result = self.scope_record
        elif method == "GET" and path == "/api/now/table/sys_update_set/" + UPDATE_SET:
            result = {
                "sys_id": UPDATE_SET,
                "application": "global",
                "state": "in progress",
                "sys_updated_on": "2026-10-22 10:00:00",
            }
        elif method == "GET" and path == "/api/now/table/sys_hub_flow/" + SUBFLOW:
            result = {
                "sys_id": SUBFLOW,
                "type": "subflow",
                "active": "true",
                "master_snapshot": SNAPSHOT,
            }
        elif method == "GET" and path == "/api/now/table/sys_hub_flow/" + FLOW:
            result = {
                "sys_id": FLOW,
                "sys_scope": "global",
                "sys_mod_count": str(self.revision),
                "sys_updated_on": "2026-10-22 10:00:00",
            }
        elif method == "GET" and path == "/api/now/table/sys_hub_flow":
            result = [{"sys_id": FLOW, "name": "Dispatch request"}] if self.flow else []
        elif method == "GET" and path == "/api/now/table/sys_update_xml":
            result = (
                []
                if self.flow is None
                else [
                    {
                        "sys_id": "f" * 32,
                        "name": "sys_hub_flow_" + FLOW,
                        "application": "global",
                        "update_set": UPDATE_SET,
                        "sys_mod_count": str(self.capture_revision),
                        "sys_updated_on": "2026-10-22 10:00:00",
                        "action": "INSERT_OR_UPDATE",
                    }
                ]
            )
        else:
            raise AssertionError(f"Unexpected fixture operation: {method} {path}")
        return httpx2.Response(200, json={"result": result}, request=request)

    def identity(self) -> str:
        self.next_id += 1
        return f"{self.next_id:032x}"

    def advance(self) -> None:
        self.revision += 1
        self.capture_revision += 1
