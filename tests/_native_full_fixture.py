"""Stateful native-response server, independent of production DTO constructors."""

import copy
import json
from pathlib import Path
from typing import Any, override

import httpx2

from tests._complete_flow_fixture import ACTION, FLOW, STUDIO, StudioModel

CAPTURE = json.loads(
    (Path(__file__).parent / "fixtures/flow_full_native.json").read_text()
)


def create_request() -> dict[str, Any]:
    def log(ref: str, value: str, **placement: str) -> dict[str, Any]:
        return {
            "op": "insert_action",
            "action_type_sys_id": ACTION,
            "ref": ref,
            "inputs": {"log_message": value, "log_level": "info"},
            **placement,
        }

    return {
        "action": "create",
        "name": "Synthetic complete native acceptance",
        "scope": "global",
        "expected_scope": "global",
        "description": "Synthetic inactive fixture",
        "edits": [
            {"op": "set_trigger", "type": "service_catalog"},
            {
                "op": "create_field",
                "collection": "flowVariables",
                "name": "message",
                "label": "Message",
                "type": "string",
            },
            {
                "op": "set_variables",
                "inputs": {"message": "Synthetic one-shot acceptance"},
            },
            log("start", "{{flow_variable.message}}"),
            {
                "op": "insert_logic",
                "logic": "If",
                "ref": "has_number",
                "inputs": {
                    "condition": "{{Service Catalog_1.request_item.number}}ISNOTEMPTY"
                },
            },
            log("then_log", "Synthetic number present", parent="has_number"),
            {
                "op": "insert_logic",
                "logic": "Else",
                "after": "has_number",
                "ref": "otherwise",
            },
            log("else_log", "Synthetic number absent", parent="otherwise"),
            log("finish", "Synthetic one-shot acceptance end", after="otherwise"),
        ],
    }


def edit_request(result: dict[str, Any]) -> dict[str, Any]:
    refs = result["refs"]
    return {
        "action": "edit",
        "sys_id": FLOW,
        "expected_scope": "global",
        "expected_revision": result["revision"],
        "edits": [
            {
                "op": "move_step",
                "step": refs["finish"]["ui_id"],
                "before": refs["has_number"]["ui_id"],
            },
            {
                "op": "insert_action",
                "action_type_sys_id": ACTION,
                "ref": "inserted",
                "after": refs["start"]["ui_id"],
                "inputs": {"log_level": "info", "log_message": "Synthetic inserted"},
            },
            {
                "op": "update_inputs",
                "step": refs["finish"]["sys_id"],
                "inputs": {"log_message": "Synthetic updated"},
            },
        ],
    }


class NativeFullModel(StudioModel):
    """Return captured native schemas, not request-shaped instance echoes.

    Only authored values, placement and stable client UI identities are taken
    from requests. IDs, wrappers, parameters, defaults, and embedded definitions
    come from the independent capture. The same server handles subsequent edits.
    """

    def __init__(self) -> None:
        super().__init__()
        self.action = copy.deepcopy(CAPTURE["action"])
        self.logic = copy.deepcopy(CAPTURE["logic"])
        self.owner_defect: str | None = None
        self.owner_kind = "SETFLOWVARIABLES"
        self.input_record_element: str | None = None
        self.input_records: dict[str, dict[str, Any]] = {}

    def _saved(self, body: dict[str, Any]) -> dict[str, Any]:
        previous = self.flow or {}
        result = copy.deepcopy(CAPTURE["flow"])
        # Existing root settings remain server state. Only authored properties change.
        if previous:
            result.update(copy.deepcopy(previous))
        for key in ("name", "description", "scope", "internalName"):
            if key in body:
                result[key] = body[key]
        result["id"] = FLOW
        variables = []
        for authored in body["flowVariables"]:
            row = copy.deepcopy(CAPTURE["flow"]["flowVariables"][0])
            row.update(
                name=authored["name"],
                label=authored["label"],
                id=authored.get("id") or self.identity(),
            )
            row["attributes"]["uiUniqueId"] = authored["attributes"]["uiUniqueId"]
            variables.append(row)
        result["flowVariables"] = variables
        for section in ("actionInstances", "flowLogicInstances"):
            rows = []
            for authored in body[section]:
                kind = ""
                if section == "actionInstances":
                    row = copy.deepcopy(CAPTURE["flow"][section][0])
                else:
                    kind = authored["flowLogicDefinition"]["type"]
                    row = copy.deepcopy(
                        next(
                            r
                            for r in CAPTURE["flow"][section]
                            if r["flowLogicDefinition"]["type"] == kind
                        )
                    )
                    row["flowBlockId"] = authored.get("flowBlockId") or self.identity()
                row.update(
                    id=authored.get("id") or self.identity(),
                    flowSysId=FLOW,
                    uiUniqueIdentifier=authored["uiUniqueIdentifier"],
                    order=str(authored["order"]),
                    parent=authored["parent"],
                )
                stored = next(
                    (r for r in previous.get(section, []) if r["id"] == row["id"]), None
                )
                if stored:
                    row["metadata"] = copy.deepcopy(stored["metadata"])
                for binding in row["inputs"]:
                    wanted = next(
                        r for r in authored["inputs"] if r["name"] == binding["name"]
                    )
                    binding["value"] = wanted["value"]
                    binding["displayValue"] = (
                        "Info"
                        if binding["name"] == "log_level" and wanted["value"] == "info"
                        else wanted["value"]
                    )
                    if section == "flowLogicInstances" and kind == "SETFLOWVARIABLES":
                        binding["parameter"]["id"] = variables[0]["id"]
                        binding["parameter"]["attributes"]["uiUniqueId"] = variables[0][
                            "attributes"
                        ]["uiUniqueId"]
                if section == "flowLogicInstances":
                    row["flowVariables"] = (
                        copy.deepcopy(row["inputs"])
                        if kind == "SETFLOWVARIABLES"
                        else []
                    )
                    row["name"] = (
                        row["flowLogicDefinition"]["name"]
                        + ": "
                        + next(
                            (
                                r["value"]
                                for r in row["inputs"]
                                if r["name"] == "condition"
                            ),
                            "",
                        )
                    )
                rows.append(row)
            result[section] = rows
        trigger = copy.deepcopy(CAPTURE["flow"]["triggerInstances"][0])
        trigger.update(
            id=body["triggerInstances"][0].get("id") or self.identity(), flowSysId=FLOW
        )
        for row in trigger["inputs"]:
            authored = next(
                r
                for r in body["triggerInstances"][0]["inputs"]
                if r["name"] == row["name"]
            )
            row["value"] = authored["value"]
            row["parameter"].pop("uiDisplayTypeLabel", None)
            if "type_label" in authored["parameter"]:
                row["parameter"]["uiDisplayTypeLabel"] = authored["parameter"][
                    "type_label"
                ]
        result["triggerInstances"] = [trigger]
        if not result["inputs"]:
            result["inputs"] = copy.deepcopy(CAPTURE["flow"]["inputs"])
            for row, template in zip(
                result["inputs"], CAPTURE["input_records"], strict=True
            ):
                row["id"] = self.identity()
                record = copy.deepcopy(template)
                record.update(sys_id=row["id"], model={"value": FLOW})
                self.input_records[row["id"]] = record
        # This REST field is authored cache data, not a definition-owned schema.
        # Its four nullable slots follow cleanupFlowBeforePut:101724-101727.
        result["label_cache"] = copy.deepcopy(body["label_cache"])
        for row in result["label_cache"]:
            for key in ("parent_table_name", "column_name", "choices", "attributes"):
                if row.get(key) is None:
                    row.pop(key, None)
        result["labelCacheAsJsonString"] = json.dumps(
            result["label_cache"], ensure_ascii=False, separators=(",", ":")
        )
        return result

    @override
    def handle(self, request: httpx2.Request) -> httpx2.Response:
        path, method = request.url.path, request.method
        if method == "GET" and path == STUDIO + "/flow_logic/types":
            self.calls.append((method, path))
            result = self.logic
        elif method == "GET" and path == "/api/now/trigger_api/trigger_categories":
            self.calls.append((method, path))
            result = CAPTURE["trigger_categories"]
        elif method == "GET" and path == "/api/now/table/sys_dictionary":
            self.calls.append((method, path))
            result = [
                {
                    "name": "sc_req_item",
                    "element": "number",
                    "column_label": "Number",
                    "internal_type": "string",
                    "reference": "",
                    "choice": "0",
                }
            ]
        elif method == "GET" and path == "/api/now/table/sys_hub_flow/" + FLOW:
            self.calls.append((method, path))
            assert self.flow is not None
            result = {
                "sys_id": FLOW,
                "type": "flow",
                "sys_scope": "global",
                "active": "false",
                "status": "draft",
                "master_snapshot": "",
                "latest_snapshot": "",
                "sys_updated_on": self.flow["updated"],
                "sys_mod_count": str(self.revision),
            }
        elif method == "GET" and path.startswith("/api/now/table/sys_hub_flow_input/"):
            self.calls.append((method, path))
            record = self.input_records.get(path.rsplit("/", 1)[1])
            if record is None:
                return httpx2.Response(
                    404, json={"error": "Unknown synthetic record"}, request=request
                )
            result = copy.deepcopy(record)
            if self.input_record_element is not None:
                result["element"] = self.input_record_element
        elif method == "GET" and path.startswith(
            "/api/now/table/sys_hub_flow_logic_instance_v2/"
        ):
            self.calls.append((method, path))
            assert self.flow is not None
            row = next(
                r
                for r in self.flow["flowLogicInstances"]
                if r["id"] == path.rsplit("/", 1)[1]
            )
            result = {
                "sys_id": row["id"],
                "flow": FLOW,
                "ui_id": row["uiUniqueIdentifier"],
                "sys_class_name": "sys_hub_flow_logic_instance_v2",
                "logic_definition": row["definitionId"],
                "block": row["flowBlockId"],
                "block.sys_id": row["flowBlockId"],
            }
            if (
                self.owner_defect is not None
                and row["flowLogicDefinition"]["type"] == self.owner_kind
            ):
                result[self.owner_defect] = "f" * 32
        elif method == "POST" and path == STUDIO + "/flow":
            self.calls.append((method, path))
            body = json.loads(request.content)
            self.bodies.append(body)
            self.flow = copy.deepcopy(CAPTURE["flow"])
            self.flow.update(body, id=FLOW)
            for key in (
                "actionInstances",
                "flowLogicInstances",
                "triggerInstances",
                "inputs",
                "outputs",
                "flowVariables",
                "label_cache",
            ):
                self.flow[key] = []
            self.flow["labelCacheAsJsonString"] = "[]"
            self.flow["stages"] = {}
            self.advance()
            result = {"data": self.flow}
        elif method == "PUT" and path == STUDIO + "/flow":
            self.calls.append((method, path))
            body = json.loads(request.content)
            self.bodies.append(copy.deepcopy(body))
            self.flow = self._saved(body)
            self.advance()
            result = {"data": copy.deepcopy(self.flow)}
        else:
            return super().handle(request)
        return httpx2.Response(200, json={"result": result}, request=request)
