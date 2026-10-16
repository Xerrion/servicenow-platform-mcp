"""Exercise native contracts through the actual MCP protocol dispatcher."""

import copy
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest
from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from servicenow_mcp import telemetry as trace_module
from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.bootstrap.stdio import create_mcp_server
from servicenow_mcp.capabilities.flows import editing
from servicenow_mcp.capabilities.mutations.models import FlowCommand
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.generate_tool_schemas import generate
from tests.test_flow_children_readback import saved_branches
from tests.test_flow_dependency_schemas import SNAPSHOT, subflow_flow
from tests.test_flow_editing import BASE, DEFS, FLOW, fixtures
from tests.test_flow_lifecycle import status_header, validation_routes

if TYPE_CHECKING:
    from servicenow_mcp.config import Settings


async def test_native_wire_validation_and_results(settings: Settings) -> None:
    settings.mcp_tool_package = "flow"
    server = create_mcp_server(settings)
    async with (
        create_client_server_memory_streams() as (client_streams, server_streams),
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(
            server._lowlevel_server.run,
            *server_streams,
            server._lowlevel_server.create_initialization_options(),
        )
        async with ClientSession(*client_streams) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            tool = next(tool for tool in tools if tool.name == "flow")
            assert isinstance(tool.output_schema, dict)
            success = await session.call_tool("flow", {"action": "describe"})
            assert not success.is_error
            assert isinstance(success.structured_content, dict)
            Draft202012Validator(tool.output_schema).validate(
                success.structured_content
            )
            assert "actions" in success.structured_content["data"]
            for arguments in [
                {"action": "inspect", "sys_id": "a" * 32, "sections": '["inputs"]'},
                {"action": "private-sensitive-value"},
                {"action": "describe", "unknown": "private-sensitive-value"},
                {"action": "describe", "limit": "private-sensitive-value"},
            ]:
                failure = await session.call_tool("flow", arguments)
                assert failure.is_error
                assert isinstance(failure.structured_content, dict)
                Draft202012Validator(tool.output_schema).validate(
                    failure.structured_content
                )
                await session.validate_tool_result("flow", failure)
                assert failure.structured_content["status"] == "error"
                assert "private-sensitive-value" not in str(failure)
            assert not http_mock.calls
        tasks.cancel_scope.cancel()


async def test_invalid_flow_preview_trace_never_logs_peer_keys_or_values(
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="servicenow_mcp.telemetry")
    settings.mcp_tool_package = "flow_write"
    server = create_mcp_server(settings)
    private = "private-sensitive-marker"
    async with (
        create_client_server_memory_streams() as (client_streams, server_streams),
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(
            server._lowlevel_server.run,
            *server_streams,
            server._lowlevel_server.create_initialization_options(),
        )
        async with ClientSession(*client_streams) as session:
            await session.initialize()
            failure = await session.call_tool(
                "flow_preview",
                {"action": private, "edits": [{private: private}], private: private},
            )
            assert failure.is_error
            await session.validate_tool_result("flow_preview", failure)
            records = [
                json.loads(record.getMessage().removeprefix("Flow preview boundary "))
                for record in caplog.records
                if record.getMessage().startswith("Flow preview boundary ")
            ]
            assert [record["boundary"] for record in records] == [
                "protocol",
                "wire.structured",
                "wire.text",
            ]
            assert records[0]["argument_keys"] == ["action", "edits"]
            assert (records[0]["action"], records[0]["edit_count"]) == ("other", 1)
            assert records[1]["change_count"] == records[2]["change_count"] == -1
            assert private not in json.dumps(records)
            assert not http_mock.calls
        tasks.cancel_scope.cancel()


@pytest.mark.parametrize(
    "readback",
    [
        "matching",
        "same_value",
        "null_cache_attributes",
        "cache_choice_order",
        "omitted_ui_family",
        "contradictory_ui_family",
        "server_updated",
        "server_updated_race",
        "server_updated_input_drift",
        "server_updated_wrong_identity",
        "server_updated_active",
        "server_updated_stale_preview",
        "stale_message",
        "missing_subflow",
        "echo_missing_metadata",
        "interface",
        "opaque",
    ],
)
async def test_exact_log_message_edit_survives_native_preview_and_independent_readback(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    readback: str,
) -> None:
    caplog.set_level(logging.INFO, logger="servicenow_mcp.telemetry")
    target = "e2861e13c3bb8f105cf89fcd2b0131d6"
    action_id = "70a65253c3bb8f105cf89fcd2b013101"
    message = "MCP PoC: Independent semantic readback verified."
    arguments = {
        "action": "edit",
        "sys_id": target,
        "edits": [
            {
                "op": "update_inputs",
                "step": action_id,
                "inputs": {"log_message": message},
            },
        ],
    }
    # Synthetic source-compatible fixture with the target's six-node topology.
    # Preserve a leading log_level binding, so the outline intentionally omits the message.
    flow = saved_branches()
    flow["id"] = target
    if readback.startswith("server_updated"):
        flow["updated"] = "2026-10-15 12:59:00"
    flow["actionInstances"][0]["id"] = action_id
    if readback in {
        "same_value",
        "null_cache_attributes",
        "cache_choice_order",
        "omitted_ui_family",
        "contradictory_ui_family",
    }:
        flow["actionInstances"][0]["inputs"][0]["value"] = message
    for action in flow["actionInstances"]:
        action["inputs"].insert(
            0, {"name": "log_level", "value": "info", "parameter": {"type": "choice"}}
        )
    logic = flow["flowLogicInstances"][0]
    logic["inputs"] = [
        {
            "name": parameter["name"],
            "parameter": copy.deepcopy(parameter),
            "value": "{{Service Catalog_1.request_item.state}}=1"
            if parameter["name"] == "condition"
            else "Open",
        }
        for parameter in logic["flowLogicDefinition"]["inputs"]
    ]
    trigger = next(
        definition
        for category in DEFS["trigger_categories"]
        for definition in category["triggers"].values()
        if definition["type"] == "service_catalog"
    )
    flow["triggerInstances"] = [
        {
            "name": "Service Catalog",
            "type": "service_catalog",
            "triggerDefinitionId": trigger["id"],
            "inputs": [],
            "outputs": copy.deepcopy(trigger["outputs"]),
        },
    ]
    before = copy.deepcopy(flow)
    fixtures(settings, flow)
    read = http_mock.get(f"{BASE}/api/now/processflow/flow/{target}").respond(
        200, json={"result": {"data": flow}}
    )
    if readback in {
        "null_cache_attributes",
        "cache_choice_order",
        "omitted_ui_family",
        "contradictory_ui_family",
    }:
        # Stabilize all unrelated serializer fields, then model the exact live null/omitted delta.
        command = FlowCommand(action="edit", sys_id=target, edits=arguments["edits"])
        async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
            state = await editing.load_state(command, client)
            flow = (await editing.plan(command, state, client))["payload"]
        assert flow["label_cache"][0]["attributes"] is None
        flow["label_cache"][0].pop("attributes")
        if readback == "cache_choice_order":
            # Current dictionary fixture has values 1 and 2; presentation order is not a binding.
            flow["label_cache"][0]["choices"].reverse()
        elif readback in {"omitted_ui_family", "contradictory_ui_family"}:
            from tests.test_flow_ui_metadata import without_ui_cache

            flow = without_ui_cache(flow)
        before = copy.deepcopy(flow)
        read.respond(200, json={"result": {"data": flow}})
    planned = []
    original_plan = editing.plan

    async def inspect_plan(command, state, client):
        assert command.edits == arguments["edits"]
        result = await original_plan(command, state, client)
        planned.append(copy.deepcopy(result))
        return result

    monkeypatch.setattr(editing, "plan", inspect_plan)
    saved = {}

    def save(request):
        payload = json.loads(request.content)
        saved.update(copy.deepcopy(payload))
        actual = copy.deepcopy(payload)
        if readback.startswith("server_updated"):
            actual["updated"] = "2026-10-15 13:00:25"
            if readback == "server_updated_race":
                actual["updated"] = "2026-10-15 13:00:26"
            elif readback == "server_updated_input_drift":
                actual["actionInstances"][0]["inputs"][1]["value"] = (
                    "private-sensitive-marker"
                )
        if readback in {"omitted_ui_family", "contradictory_ui_family"}:
            from tests.test_flow_ui_metadata import without_ui_cache

            actual = without_ui_cache(actual)
            if readback == "contradictory_ui_family":
                actual["actionInstances"][0]["ancestorType"] = "IF"
        if readback in {"null_cache_attributes", "cache_choice_order"}:
            actual["label_cache"][0].pop("attributes")
            if readback == "cache_choice_order":
                actual["label_cache"][0]["choices"].reverse()
        elif readback == "stale_message":
            original = next(
                i
                for i in before["actionInstances"][0]["inputs"]
                if i["name"] == "log_message"
            )
            binding = next(
                i
                for i in actual["actionInstances"][0]["inputs"]
                if i["name"] == "log_message"
            )
            binding["value"] = original["value"]
        elif readback == "missing_subflow":
            actual["subFlowInstances"] = []
        elif readback == "interface":
            actual["outputs"] = [{"name": "private-sensitive-marker", "type": "string"}]
        elif readback == "opaque":
            actual["private-sensitive-marker"] = "private-sensitive-marker"
        read.respond(200, json={"result": {"data": actual}})
        echo = copy.deepcopy(payload)
        if readback.startswith("server_updated"):
            echo["updated"] = "2026-10-15 13:00:25"
            if readback == "server_updated_wrong_identity":
                echo["id"] = "9" * 32
            elif readback == "server_updated_active":
                echo["active"] = True
        if readback == "echo_missing_metadata":
            echo["actionInstances"][0]["actionType"].pop("userCanRead")
        elif readback == "omitted_ui_family":
            from tests.test_flow_ui_metadata import without_ui_cache

            echo = without_ui_cache(echo)
        elif readback in {"null_cache_attributes", "cache_choice_order"}:
            echo["label_cache"][0].pop("attributes")
            if readback == "cache_choice_order":
                echo["label_cache"][0]["choices"].reverse()
        return httpx2.Response(200, json={"result": {"data": echo}})

    write = http_mock.route(method="PUT", url=f"{BASE}/api/now/processflow/flow").mock(
        side_effect=save
    )
    settings.mcp_tool_package = "flow,flow_write"
    server = create_mcp_server(settings)
    async with (
        create_client_server_memory_streams() as (client_streams, server_streams),
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(
            server._lowlevel_server.run,
            *server_streams,
            server._lowlevel_server.create_initialization_options(),
        )
        async with ClientSession(*client_streams) as session:
            await session.initialize()
            preview = await session.call_tool("flow_preview", arguments)
            assert not preview.is_error, preview
            await session.validate_tool_result("flow_preview", preview)
            assert isinstance(preview.structured_content, dict)
            assert (
                json.loads(
                    next(
                        block.text for block in preview.content if block.type == "text"
                    )
                )
                == preview.structured_content
            )
            summary = preview.structured_content["data"]["summary"]
            assert summary["changes"] == [f"1. Update inputs of step {action_id}"]
            assert summary["before"] == summary["after"]
            assert (
                len(summary["after"]) == 7
            )  # Trigger plus all six steps, including the subflow.
            assert summary["after"][-1] == "Provision (REMOVE)"
            assert summary["data_pills"] == ["Service Catalog_1.request_item.state"]
            assert summary["context"]["state"] == "unverified"
            if readback == "same_value":
                check = summary["serialization_check"]
                assert check["matches"] is False
                assert check["mode"] == "planned_vs_current_read_only"
                assert (check["path"], check["kind"], check["side"]) == (
                    "flow.label_cache",
                    "COUNT_MISMATCH",
                    "both",
                )
                assert message not in json.dumps(check)
            elif readback in {
                "null_cache_attributes",
                "cache_choice_order",
                "omitted_ui_family",
                "contradictory_ui_family",
            }:
                assert summary["serialization_check"] == {
                    "mode": "planned_vs_current_read_only",
                    "matches": True,
                }
            else:
                assert "serialization_check" not in summary
            assert not write.called
            assert all(call.request.method == "GET" for call in http_mock.calls)
            boundary_records = [
                json.loads(record.getMessage().removeprefix("Flow preview boundary "))
                for record in caplog.records
                if record.getMessage().startswith("Flow preview boundary ")
            ]
            assert [record["boundary"] for record in boundary_records] == [
                "protocol",
                "handler",
                "command",
                "planner.in",
                "planner.out",
                "handler.out",
                "wire.structured",
                "wire.text",
            ]
            assert len({record["trace_id"] for record in boundary_records}) == 1
            sink = (
                trace_module._FLOW_TRACE_ROOT
                / "servicenow-flow-preview-trace/boundaries.jsonl"
            )
            sink_records = [json.loads(line) for line in sink.read_text().splitlines()]
            trace_id = boundary_records[0]["trace_id"]
            assert [
                record for record in sink_records if record["trace_id"] == trace_id
            ] == boundary_records
            for record in boundary_records:
                assert record["pid"] == os.getpid()
                assert record["marker"] == "flow-preview-boundaries-v1"
                assert len(record["loaded_code_sha256"]) == 64
                assert record["module_path"].startswith(
                    str(Path(__file__).parent.parent / "src/servicenow_mcp")
                )
                if "edit_count" in record:
                    assert (record["action"], record["edit_count"]) == ("edit", 1)
                if "node_counts" in record:
                    assert record["node_counts"] == {
                        "action": 3,
                        "flowlogic": 2,
                        "subflow": 1,
                    }
                if "change_count" in record:
                    assert (
                        record["change_count"],
                        record["after_count"],
                        record["pill_count"],
                    ) == (1, 7, 1)
            assert boundary_records[0]["argument_keys"] == ["action", "sys_id", "edits"]
            boundary_text = json.dumps(boundary_records)
            assert target not in boundary_text
            assert action_id not in boundary_text
            assert message not in boundary_text
            assert "Service Catalog_1" not in boundary_text
            assert (
                preview.structured_content["data"]["preview_token"] not in boundary_text
            )
            payload = planned[0]["payload"]
            wanted_actions = copy.deepcopy(before["actionInstances"])
            message_binding = next(
                i for i in wanted_actions[0]["inputs"] if i["name"] == "log_message"
            )
            message_binding.update(value=message, displayValue=message)
            wanted_by_id = {action["id"]: action for action in wanted_actions}
            assert {action["id"] for action in payload["actionInstances"]} == set(
                wanted_by_id
            )
            for actual in payload["actionInstances"]:
                wanted = wanted_by_id[actual["id"]]
                assert actual["inputs"] == wanted["inputs"]
                assert (actual["parent"], int(actual["order"])) == (
                    wanted["parent"],
                    int(wanted["order"]),
                )
            assert (
                len(payload["flowLogicInstances"]),
                len(payload["subFlowInstances"]),
            ) == (2, 1)
            assert (
                payload["subFlowInstances"][0]["id"]
                == before["subFlowInstances"][0]["id"]
            )
            assert (
                payload["subFlowInstances"][0]["inputs"]
                == before["subFlowInstances"][0]["inputs"]
            )
            assert (
                payload["subFlowInstances"][0]["subflowSysId"]
                == before["subFlowInstances"][0]["subflowSysId"]
            )
            assert payload["subFlowInstances"][0]["waitForCompletion"] == {
                "value": True
            }
            assert (
                payload["flowLogicInstances"][0]["inputs"]
                == before["flowLogicInstances"][0]["inputs"]
            )
            assert arguments["edits"] == [
                {
                    "op": "update_inputs",
                    "step": action_id,
                    "inputs": {"log_message": message},
                },
            ]
            reads_before_apply = read.call_count
            if readback == "server_updated_stale_preview":
                flow["updated"] = "2026-10-15 13:00:25"
                read.respond(200, json={"result": {"data": flow}})
            applied = await session.call_tool(
                "mutation_apply",
                {"preview_token": preview.structured_content["data"]["preview_token"]},
            )
            await session.validate_tool_result("mutation_apply", applied)
            assert isinstance(applied.structured_content, dict)
            if readback == "server_updated_stale_preview":
                assert applied.is_error
                assert applied.structured_content["error"]["code"] == "STALE_PREVIEW"
                assert not write.called
                assert all(call.request.method == "GET" for call in http_mock.calls)
                tasks.cancel_scope.cancel()
                return
            if readback in {
                "matching",
                "same_value",
                "null_cache_attributes",
                "cache_choice_order",
                "omitted_ui_family",
                "server_updated",
            }:
                assert not applied.is_error, applied
            else:
                assert applied.is_error
                assert applied.structured_content["error"]["code"] == "UNKNOWN_OUTCOME"
                error = applied.structured_content["error"]["message"]
                if readback in {
                    "server_updated_wrong_identity",
                    "server_updated_active",
                }:
                    assert (
                        error
                        == "The saved flow differs from the draft request. Inspect it before retrying."
                    )
                expected = {
                    "stale_message": "request.readback, both:flow.actionInstances[0].inputs[1].value VALUE_MISMATCH",
                    "missing_subflow": "request.readback, both:flow.subFlowInstances COUNT_MISMATCH",
                    "echo_missing_metadata": (
                        "request.echo, actual:flow.actionInstances[0].actionType[field:0] MISSING_FIELD"
                    ),
                    "interface": "request.readback, both:flow.outputs COUNT_MISMATCH",
                    "server_updated_race": "echo.readback, both:flow.updated SERVER_REVISION_MISMATCH",
                    "server_updated_input_drift": (
                        "request.readback, both:flow.actionInstances[0].inputs[1].value VALUE_MISMATCH"
                    ),
                    "contradictory_ui_family": (
                        "request.readback, actual:flow.actionInstances[0].ancestorType INCOMPLETE_SEMANTICS"
                    ),
                }.get(readback)
                if expected:
                    assert expected in error
                elif readback not in {
                    "server_updated_wrong_identity",
                    "server_updated_active",
                }:
                    assert "request.readback, expected:flow[field:" in error
                    assert "MISSING_FIELD" in error
                assert "private-sensitive-marker" not in error
                assert message not in error
                if readback not in {
                    "server_updated_wrong_identity",
                    "server_updated_active",
                }:
                    assert "Do not replay the write." in error
            assert saved == payload
            assert write.call_count == 1
            assert read.call_count > reads_before_apply
            if readback in {
                "echo_missing_metadata",
                "server_updated_wrong_identity",
                "server_updated_active",
            }:
                assert http_mock.calls[-1].request.method == "PUT"
            else:
                assert http_mock.calls[-1].request.method == "GET"
                assert (
                    http_mock.calls[-1].request.url.path
                    == f"/api/now/processflow/flow/{target}"
                )
            assert flow == before
        tasks.cancel_scope.cancel()


@pytest.mark.parametrize(
    ("defect", "reason", "http_status"),
    [
        ("generic", "DEFINITION_READ_FAILED", None),
        ("identity", "IDENTITY_MISMATCH", None),
        ("access", "READ_ACCESS_UNVERIFIED", None),
        ("entitlement", "ENTITLEMENT_UNVERIFIED", None),
        ("publication", "PUBLICATION_UNVERIFIED", None),
        ("schema", "SCHEMA_MISMATCH", None),
        ("missing_schema", "SCHEMA_MISMATCH", None),
        ("mandatory_representation", "SCHEMA_MISMATCH", None),
        ("missing_constraint", "SCHEMA_MISMATCH", None),
        ("unsafe_schema_name", "SCHEMA_MISMATCH", None),
        ("saved_missing_type", "SCHEMA_MISMATCH", None),
        ("nested_choices", "SCHEMA_MISMATCH", None),
        ("choice_presentation", None, None),
        ("choice_dependency", "SCHEMA_MISMATCH", None),
        ("choice_value", "SCHEMA_MISMATCH", None),
        ("acl", "DEFINITION_READ_FAILED", 403),
    ],
)
async def test_flow_dependency_reason_survives_native_wire(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    defect: str,
    reason: str | None,
    http_status: int | None,
) -> None:
    flow, snapshot = subflow_flow()
    validation_routes(flow)
    fixtures(settings, flow)
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow/{FLOW}").respond(
        200, json={"result": status_header()}
    )
    if defect == "identity":
        snapshot["id"] = "6" * 32
    elif defect == "access":
        snapshot["security"]["can_read"] = False
    elif defect == "entitlement":
        snapshot["isFlowEntitled"] = False
    elif defect == "publication":
        snapshot["snapshot"] = False
    elif defect == "schema":
        snapshot["outputs"][0]["reference"] = "incident"
    elif defect == "missing_schema":
        snapshot.pop("outputs")
    elif defect == "mandatory_representation":
        snapshot["inputs"][0]["mandatory"] = "true"
    elif defect == "missing_constraint":
        snapshot["outputs"][0].pop("maxsize")
    elif defect == "unsafe_schema_name":
        snapshot["outputs"][0]["name"] = "private-sensitive-marker"
        flow["subFlowInstances"][0]["subFlow"]["outputs"][0]["name"] = (
            "private-sensitive-marker"
        )
        snapshot["outputs"][0]["reference"] = "private-sensitive-marker"
    elif defect == "saved_missing_type":
        flow["subFlowInstances"][0]["subFlow"]["outputs"][0]["parameter"].pop("type")
        flow["subFlowInstances"][0]["subFlow"]["outputs"][0].pop("type")
    elif defect == "nested_choices":
        output = {
            "name": "payload",
            "type": "object",
            "children": [
                {
                    "name": "state",
                    "type": "choice",
                    "choices": [{"value": "ok", "label": "OK"}],
                },
            ],
        }
        snapshot["outputs"] = [output]
        flow["subFlowInstances"][0]["subFlow"]["outputs"] = [
            {
                **output,
                "children": [
                    {
                        "name": "state",
                        "type": "choice",
                        "choices": [
                            {"value": "private-sensitive-marker", "label": "OK"}
                        ],
                    },
                ],
            },
        ]
    elif defect == "choice_presentation":
        from tests.test_flow_dependency_schemas import choice_definitions

        saved, current = choice_definitions()
        snapshot["inputs"][0]["choices"] = current["inputs"][0]["choices"]
        flow["subFlowInstances"][0]["subFlow"]["inputs"][0]["parameter"]["choices"] = (
            saved["inputs"][0]["choices"]
        )
        flow["subFlowInstances"][0]["inputs"][0]["parameter"]["choices"] = saved[
            "inputs"
        ][0]["choices"]
    elif defect == "choice_dependency":
        snapshot["inputs"][0]["choices"][0]["dependent_value"] = (
            "private-sensitive-marker"
        )
    elif defect == "choice_value":
        snapshot["inputs"][0]["choices"][0]["value"] = "private-sensitive-marker"
    route = http_mock.get(f"{BASE}/api/now/processflow/flow/{SNAPSHOT}").respond(
        http_status or 200,
        json={"result": {"data": snapshot}, "error": "private-sensitive-marker"},
    )
    if defect == "generic":
        original = ServiceNowClient.studio_request

        async def unavailable(client, resource, **kwargs):
            if resource == f"flow/{SNAPSHOT}":
                raise RuntimeError("private-sensitive-marker")
            return await original(client, resource, **kwargs)

        monkeypatch.setattr(ServiceNowClient, "studio_request", unavailable)
    settings.mcp_tool_package = "flow"
    server = create_mcp_server(settings)
    async with (
        create_client_server_memory_streams() as (client_streams, server_streams),
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(
            server._lowlevel_server.run,
            *server_streams,
            server._lowlevel_server.create_initialization_options(),
        )
        async with ClientSession(*client_streams) as session:
            await session.initialize()
            result = await session.call_tool(
                "flow", {"action": "validate", "sys_id": FLOW}
            )
            assert not result.is_error
            payload = result.structured_content
            assert isinstance(payload, dict)
            tool = next(
                tool
                for tool in (await session.list_tools()).tools
                if tool.name == "flow"
            )
            assert isinstance(tool.output_schema, dict)
            Draft202012Validator(tool.output_schema).validate(payload)
            await session.validate_tool_result("flow", result)
            text = next(block.text for block in result.content if block.type == "text")
            assert json.loads(text) == payload
            if defect == "choice_presentation":
                assert payload["data"]["valid"] is True, payload
                assert payload["data"]["complete"] is True
                assert payload["data"]["diagnostics"] == []
                assert route.call_count == 1
                assert all(call.request.method == "GET" for call in http_mock.calls)
                tasks.cancel_scope.cancel()
                return
            assert payload["data"]["valid"] is None
            assert payload["data"]["complete"] is False
            issue = next(
                d
                for d in payload["data"]["diagnostics"]
                if d["code"] == "UNVERIFIED_DEPENDENCY"
            )
            assert reason is not None
            detail = reason + (
                f", HTTP {http_status}" if http_status is not None else ""
            )
            mismatch = {
                "schema": {
                    "side": "both",
                    "path": "outputs[result].reference",
                    "kind": "VALUE_MISMATCH",
                },
                "missing_schema": {
                    "side": "current",
                    "path": "outputs",
                    "kind": "INCOMPLETE_SCHEMA",
                },
                "mandatory_representation": {
                    "side": "both",
                    "path": "inputs[action].mandatory",
                    "kind": "REPRESENTATION_MISMATCH",
                },
                "missing_constraint": {
                    "side": "current",
                    "path": "outputs[result].maxsize",
                    "kind": "METADATA_PRESENCE_MISMATCH",
                },
                "unsafe_schema_name": {
                    "side": "both",
                    "path": "outputs[0].reference",
                    "kind": "VALUE_MISMATCH",
                },
                "saved_missing_type": {
                    "side": "saved",
                    "path": "outputs[0].type",
                    "kind": "INCOMPLETE_SCHEMA",
                },
                "nested_choices": {
                    "side": "both",
                    "path": "outputs[payload].children[state].choices",
                    "kind": "CHOICE_VALUE_SET_MISMATCH",
                },
                "choice_dependency": {
                    "side": "both",
                    "path": "inputs[action].choices",
                    "kind": "CHOICE_METADATA_MISMATCH",
                },
                "choice_value": {
                    "side": "both",
                    "path": "inputs[action].choices",
                    "kind": "CHOICE_VALUE_SET_MISMATCH",
                },
            }.get(defect)
            if mismatch:
                detail += f", {mismatch['side']}:{mismatch['path']} {mismatch['kind']}"
            expected = {
                "code": "UNVERIFIED_DEPENDENCY",
                "path": "subFlowInstances[sub-ui]",
                "message": f"Dependency verification failed ({detail}). No replacement version is inferred.",
                "incomplete": True,
                "reason": reason,
            }
            if http_status is not None:
                expected["http_status"] = http_status
            if mismatch:
                expected["schema_mismatch"] = mismatch
            assert issue == expected
            assert "private-sensitive-marker" not in str(result)
            assert all(call.request.method == "GET" for call in http_mock.calls)
            assert route.call_count == (0 if defect == "generic" else 1)
        tasks.cancel_scope.cancel()


async def test_native_schema_snapshot(settings: Settings) -> None:
    server = create_mcp_server(settings)
    async with server._lowlevel_server.lifespan(server._lowlevel_server):
        tools = await server.list_tools()
    snapshot = {
        tool.name: {
            "input": tool.input_schema,
            "output": tool.output_schema,
            "annotations": tool.annotations.model_dump(),
        }
        for tool in tools
    }
    path = Path(__file__).parent / "fixtures/native_tool_schemas.json"
    if os.environ.get("UPDATE_TOOL_SCHEMAS") == "1":
        await generate()
    expected = json.loads(path.read_text())
    flow = json.loads((path.parent / "flow_preview_schema.json").read_text())
    shared = expected[flow["shared_output_and_annotations"]]
    expected["flow_preview"] = {
        "input": flow["input"],
        "output": shared["output"],
        "annotations": shared["annotations"],
    }
    assert snapshot == expected


async def test_native_objects_and_nested_errors_are_safe_on_wire(
    settings: Settings,
) -> None:
    server = create_mcp_server(settings)
    async with (
        create_client_server_memory_streams() as (client_streams, server_streams),
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(
            server._lowlevel_server.run,
            *server_streams,
            server._lowlevel_server.create_initialization_options(),
        )
        async with ClientSession(*client_streams) as session:
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            for tool, args in [
                (
                    "record_preview",
                    {
                        "action": "create",
                        "table": "incident",
                        "data": '{"secret":"private-sensitive-value"}',
                    },
                ),
                (
                    "investigate",
                    {
                        "action": "run",
                        "name": "error_analysis",
                        "params": '{"hours":24}',
                    },
                ),
                (
                    "service_catalog_preview",
                    {
                        "action": "order_now",
                        "item_sys_id": "a" * 32,
                        "variables": '{"secret":"private-sensitive-value"}',
                    },
                ),
                (
                    "query",
                    {
                        "table": "incident",
                        "aggregate": [
                            {"operation": "avg", "field": "private-sensitive-value!"}
                        ],
                    },
                ),
                (
                    "query",
                    {"table": "incident", "fields": ["private-sensitive-value!"]},
                ),
                (
                    "query",
                    {
                        "table": "incident",
                        "aggregate": [
                            {"operation": "count", "unknown": "private-sensitive-value"}
                        ],
                    },
                ),
            ]:
                failure = await session.call_tool(tool, args)
                assert failure.is_error
                assert isinstance(failure.structured_content, dict)
                schema = tools[tool].output_schema
                assert isinstance(schema, dict)
                Draft202012Validator(schema).validate(failure.structured_content)
                await session.validate_tool_result(tool, failure)
                assert "private-sensitive-value" not in str(failure)
            assert not http_mock.calls
        tasks.cancel_scope.cancel()


async def test_native_argument_diagnostics_are_actionable_and_safe(
    settings: Settings,
) -> None:
    server = create_mcp_server(settings)
    secret = "private-sensitive-value"
    async with (
        create_client_server_memory_streams() as (client_streams, server_streams),
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(
            server._lowlevel_server.run,
            *server_streams,
            server._lowlevel_server.create_initialization_options(),
        )
        async with ClientSession(*client_streams) as session:
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            cases = [
                ("query", {"fields": ["number"]}, ["table"], None),
                (
                    "query",
                    {"table": "incident", "fields": f'["number","{secret}"]'},
                    None,
                    [{"field": "fields", "expected": 'array of string | "*" | null'}],
                ),
                ("query", {"table": "incident", "fields": secret}, None, None),
                ("query", {"table": "incident"}, ["fields"], None),
                ("query", {"table": "incident", "fields": None}, ["fields"], None),
                ("query", {"table": "incident", "fields": []}, ["fields"], None),
                (
                    "query",
                    {"fields": f'["{secret}"]'},
                    ["table"],
                    [{"field": "fields", "expected": 'array of string | "*" | null'}],
                ),
                (
                    "query",
                    {"table": 7, "limit": secret, "display_values": "yes"},
                    None,
                    [
                        {"field": "table", "expected": "string"},
                        {"field": "limit", "expected": "integer | null"},
                        {"field": "display_values", "expected": "boolean | null"},
                    ],
                ),
                (
                    "query",
                    {"table": "incident", "aggregate": [{"operation": secret}]},
                    None,
                    None,
                ),
                (
                    "query",
                    {"table": "incident", f"{secret}_api_key": secret},
                    None,
                    None,
                ),
                ("query", {f"{secret}<script>": 1}, ["table"], None),
                ("flow", {"action": secret}, None, None),
            ]
            for tool, args, missing, invalid in cases:
                failure = await session.call_tool(tool, args)
                assert failure.is_error
                content = failure.structured_content
                assert isinstance(content, dict)
                schema = tools[tool].output_schema
                assert isinstance(schema, dict)
                Draft202012Validator(schema).validate(content)
                await session.validate_tool_result(tool, failure)
                assert content["status"] == "error"
                assert content["data"] is None
                error = content["error"]
                assert error["code"] == "INVALID_REQUEST"
                assert error.get("missing_arguments") == missing
                assert "missing_fields" not in error
                if invalid is not None:
                    assert error["invalid_fields"] == invalid
                assert secret not in str(failure)
                assert "script" not in str(failure)
                assert "api_key" not in str(failure)
                assert len(error["message"]) < 400
            json_string = await session.call_tool(
                "query", {"table": "incident", "fields": '["number"]'}
            )
            assert isinstance(json_string.structured_content, dict)
            assert (
                "not JSON-encoded strings"
                in json_string.structured_content["error"]["message"]
            )
            literal = await session.call_tool("flow", {"action": secret})
            assert isinstance(literal.structured_content, dict)
            assert (
                literal.structured_content["error"]["invalid_fields"][0]["field"]
                == "action"
            )
            assert literal.structured_content["error"]["invalid_fields"][0][
                "expected"
            ].startswith("one of ")
            assert not http_mock.calls
        tasks.cancel_scope.cancel()


async def test_native_record_fields_and_projection_arguments_remain_distinct(
    settings: Settings,
) -> None:
    server = create_mcp_server(settings)
    base_url = settings.servicenow_instance_url
    http_mock.get(f"{base_url}/api/now/table/sys_db_object").respond(
        200, json={"result": [{"super_class": ""}]}
    )
    http_mock.get(f"{base_url}/api/now/table/sys_dictionary").respond(
        200,
        json={"result": [{"element": "name", "mandatory": "true"}]},
    )
    records = [{"sys_id": "a" * 32, "number": "INC0001"}]
    projected = http_mock.get(f"{base_url}/api/now/table/incident").respond(
        200, json={"result": records}
    )
    async with (
        create_client_server_memory_streams() as (client_streams, server_streams),
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(
            server._lowlevel_server.run,
            *server_streams,
            server._lowlevel_server.create_initialization_options(),
        )
        async with ClientSession(*client_streams) as session:
            await session.initialize()
            failure = await session.call_tool(
                "record_preview", {"action": "create", "table": "incident", "data": {}}
            )
            assert failure.is_error
            await session.validate_tool_result("record_preview", failure)
            assert failure.structured_content == {
                "status": "error",
                "data": None,
                "error": {
                    "code": "MISSING_MANDATORY_FIELDS",
                    "message": "Missing mandatory fields for table 'incident': name",
                    "missing_fields": ["name"],
                },
            }
            success = await session.call_tool(
                "query", {"table": "incident", "fields": ["number"]}
            )
            assert not success.is_error
            await session.validate_tool_result("query", success)
            assert isinstance(success.structured_content, dict)
            assert success.structured_content["status"] == "success"
            assert success.structured_content["data"] == records
            assert (
                projected.calls.last.request.url.params["sysparm_fields"]
                == "sys_id,number"
            )
            assert all(call.request.method == "GET" for call in http_mock.calls)
        tasks.cancel_scope.cancel()
