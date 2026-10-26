"""One complete native request followed by incremental edits, using mock HTTP only."""

import copy
import json
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession
from mcp.server import MCPServer
from mcp.shared.memory import create_client_server_memory_streams

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.flows import tree
from servicenow_mcp.mcp.inputs import NativeMCPServer
from tests._complete_flow_fixture import (
    ACTION,
    FLOW,
    STUDIO,
    SUBFLOW,
    StudioModel,
    create_request,
    edit_request,
)
from tests.helpers import decode_response, get_tool_functions, register_mutations


def setup(settings):
    model = StudioModel()
    model.install()
    server = MCPServer("complete-flow")
    coordinator = register_mutations(
        server, settings, OAuthPKCEProvider(settings), frozenset({"flows"})
    )
    return model, coordinator, get_tool_functions(server)


async def apply(tools, preview):
    assert preview["status"] == "success", preview
    return decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )


@pytest.mark.parametrize("subflow", [False, True])
@pytest.mark.parametrize("mirror", [False, True])
async def test_complete_create_then_incremental_edit_preserves_identities(
    settings, subflow, mirror
):
    model, _, tools = setup(settings)
    model.component_mirror = mirror
    preview = decode_response(
        await tools["flow_preview"](**create_request(subflow=subflow))
    )
    assert preview["status"] == "success", preview
    assert model.flow is None and not model.bodies
    created = await apply(tools, preview)
    assert created["status"] == "success", created
    data = created["data"]
    assert data["revision"] == "14" and "capture" not in data
    assert (
        data["refs"]["source"]["ui_id"] == preview["data"]["summary"]["refs"]["source"]
    )
    assert len({v["sys_id"] for v in data["refs"].values()}) == len(data["refs"])
    before = copy.deepcopy(model.flow)
    assert before["serverSetting"] == {"opaque": "preserve"}
    assert (
        not before["active"]
        and not before["isPublished"]
        and not before["masterSnapshotId"]
    )
    assert bool(before["triggerInstances"]) is not subflow
    modified = await apply(
        tools, decode_response(await tools["flow_preview"](**edit_request(data)))
    )
    assert modified["status"] == "success", modified
    assert modified["data"]["revision"] == "15"
    after = model.flow
    old = {s.uid: s for s in tree.walk(tree.build_tree(before))}
    new = {s.uid: s for s in tree.walk(tree.build_tree(after))}
    audit = data["refs"]["audit"]["ui_id"]
    assert set(old) - set(new) == {audit}
    for uid in old.keys() & new.keys():
        assert old[uid].raw["id"] == new[uid].raw["id"]
    assert before["triggerInstances"] == after["triggerInstances"]
    assert before["flowVariables"] == after["flowVariables"]
    assert before["serverSetting"] == after["serverSetting"]
    branch = new[data["refs"]["branch"]["ui_id"]]
    assert [s.uid for s in branch.children] == [data["refs"]["dispatch"]["ui_id"]]
    tail = new[data["refs"]["tail"]["ui_id"]]
    assert (
        tail.raw["order"] < branch.raw["order"]
        and tail.raw["inputs"][0]["value"] == "Completed"
    )
    assert model.calls.count(("POST", STUDIO + "/flow")) == 1
    assert model.calls.count(("PUT", STUDIO + "/flow")) == 2


@pytest.mark.parametrize(
    "bad",
    [
        {"op": "unknown"},
        {
            "op": "insert_action",
            "action_type_sys_id": ACTION,
            "inputs": {"unknown": "x"},
        },
        {
            "op": "insert_action",
            "action_type_sys_id": ACTION,
            "inputs": {"message": []},
        },
        {
            "op": "insert_action",
            "action_type_sys_id": ACTION,
            "inputs": {"message": "{{missing.text}}"},
        },
        {"op": "insert_action", "action_type_sys_id": ACTION, "parent": "source"},
        {"op": "insert_logic", "logic": "Else"},
        {"op": "insert_action", "action_type_sys_id": ACTION, "ref": "source"},
        {"op": "insert_action", "action_type_sys_id": ACTION, "ref": "flow_variable"},
        {"op": "insert_action", "action_type_sys_id": ACTION, "ref": "subflow"},
        {
            "op": "insert_action",
            "action_type_sys_id": ACTION,
            "settings": {"active": True},
        },
    ],
)
async def test_invalid_complete_plan_fails_before_header_creation(settings, bad):
    model, coordinator, tools = setup(settings)
    args = create_request()
    args["edits"].append(bad)
    result = decode_response(await tools["flow_preview"](**args))
    assert result["status"] == "error", result
    assert model.flow is None and not model.bodies and not len(coordinator.store)


@pytest.mark.parametrize("defect", ["definition", "scope", "grant", "policy"])
async def test_complete_dependencies_and_authority_drift_block_first_write(
    settings, defect, monkeypatch
):
    from servicenow_mcp import policy

    model, coordinator, tools = setup(settings)
    preview = decode_response(await tools["flow_preview"](**create_request()))
    assert preview["status"] == "success", preview
    if defect == "definition":
        model.action["description"] = "changed"
    elif defect == "scope":
        model.scope_record["sys_id"] = "1" * 32
    elif defect == "grant":
        coordinator.auth._generation += 1
    else:
        monkeypatch.setattr(
            policy,
            "DENIED_TABLES",
            policy.DENIED_TABLES | {"sys_hub_action_instance_v2"},
        )
    result = await apply(tools, preview)
    assert result["status"] == "error" and not model.bodies
    assert not len(coordinator.store)


@pytest.mark.parametrize(
    "defect",
    [
        "post_timeout",
        "put_timeout",
        "put_rejected",
        "readback_value",
        "readback_id",
        "readback_setting",
        "readback_parent",
        "readback_active",
    ],
)
async def test_partial_outcomes_report_created_id_without_retry(settings, defect):
    model, coordinator, tools = setup(settings)
    preview = decode_response(await tools["flow_preview"](**create_request()))
    model.defect = defect
    result = await apply(tools, preview)
    assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    outcome = result["error"]["outcome"]
    assert outcome["sys_id"] == (None if defect == "post_timeout" else FLOW)
    assert outcome["header"] == ("unknown" if defect == "post_timeout" else "verified")
    assert outcome["definition"] == (
        "not_sent" if defect == "post_timeout" else "unknown"
    )
    assert "private-body-marker" not in str(result)
    assert not len(coordinator.store)
    count = len(model.bodies)
    again = await apply(tools, preview)
    assert again["status"] == "error" and len(model.bodies) == count


@pytest.mark.parametrize("count", [51, 200, 201])
async def test_complete_request_size_boundary(settings, count):
    model, _, tools = setup(settings)
    args = create_request()
    args["edits"] = [
        {
            "op": "insert_action",
            "action_type_sys_id": ACTION,
            "ref": f"item_{i}",
            "inputs": {"message": "Example"},
        }
        for i in range(count)
    ]
    result = decode_response(await tools["flow_preview"](**args))
    assert result["status"] == ("success" if count <= 200 else "error"), result
    assert not model.bodies


@pytest.mark.parametrize(
    "defect", ["post_dependency", "header_mirror", "header_changed"]
)
async def test_post_header_failure_keeps_precise_persisted_progress(settings, defect):
    model, _, tools = setup(settings)
    preview = decode_response(await tools["flow_preview"](**create_request()))
    model.defect = defect
    result = await apply(tools, preview)
    assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    outcome = result["error"]["outcome"]
    assert outcome["sys_id"] == FLOW and outcome["header"] == "verified"
    assert outcome["definition"] == "not_sent"
    assert len(model.bodies) == 1


@pytest.mark.parametrize("defect", ["readback_mirror", "readback_mirror_id"])
async def test_new_server_ids_do_not_hide_opaque_mirror_changes(settings, defect):
    model, _, tools = setup(settings)
    model.component_mirror = True
    preview = decode_response(await tools["flow_preview"](**create_request()))
    model.defect = defect
    result = await apply(tools, preview)
    assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    assert "flow.componentInstances" in result["error"]["message"]
    assert (
        "MIRROR_MISMATCH" if defect == "readback_mirror" else "IDENTITY_MISMATCH"
    ) in result["error"]["message"]
    assert "private-body-marker" not in str(result)
    assert len(model.bodies) == 2


@pytest.mark.parametrize(
    "defect", ["subflow_trigger", "private_scope", "unpublished", "unreadable"]
)
async def test_complete_creation_rejects_unsupported_definition_contracts(
    settings, defect
):
    model, _, tools = setup(settings)
    args = create_request()
    if defect == "subflow_trigger":
        args["flow_type"] = "subflow"
    elif defect == "private_scope":
        model.subflow.update(scope="1" * 32, access="private")
    elif defect == "unpublished":
        model.action["is_snapshot"] = False
    else:
        model.action["security"]["can_read"] = False
    result = decode_response(await tools["flow_preview"](**args))
    assert result["status"] == "error" and not model.bodies


@pytest.mark.parametrize("count", [198, 199])
async def test_complete_node_bound_counts_hidden_handler_wrappers(settings, count):
    model, _, tools = setup(settings)
    args = create_request()
    args["edits"] = [{"op": "set_error_handler", "enabled": True}] + [
        {
            "op": "insert_action",
            "action_type_sys_id": ACTION,
            "inputs": {"message": "Example"},
        }
        for _ in range(count)
    ]
    result = decode_response(await tools["flow_preview"](**args))
    assert result["status"] == ("success" if count == 198 else "error"), result
    assert not model.bodies


async def test_complete_creation_does_not_depend_on_capture_freshness(
    settings,
):
    model, _, tools = setup(settings)
    model.defect = "capture_drift"
    args = create_request()
    args.pop("expected_scope")
    result = await apply(tools, decode_response(await tools["flow_preview"](**args)))
    assert result["status"] == "success", result
    assert result["data"]["revision"] == "14"
    assert "capture" not in result["data"]
    assert all(
        "sys_update_" not in path and "concoursepicker" not in path
        for _, path in model.calls
    )


@pytest.mark.parametrize("state", ["active", "published"])
async def test_created_flow_cannot_bypass_existing_active_published_edit_guard(
    settings, state
):
    model, _, tools = setup(settings)
    created = await apply(
        tools, decode_response(await tools["flow_preview"](**create_request()))
    )
    assert created["status"] == "success", created
    if state == "active":
        model.flow["active"] = True
    else:
        model.flow.update(
            status="published", isPublished=True, masterSnapshotId="1" * 32
        )
    result = decode_response(
        await tools["flow_preview"](**edit_request(created["data"]))
    )
    assert result["status"] == "error" and len(model.bodies) == 2


async def test_followup_revision_drift_rejects_before_any_additional_write(settings):
    model, _, tools = setup(settings)
    created = await apply(
        tools, decode_response(await tools["flow_preview"](**create_request()))
    )
    preview = decode_response(
        await tools["flow_preview"](**edit_request(created["data"]))
    )
    model.revision += 1
    result = await apply(tools, preview)
    assert result["error"]["code"] == "STALE_PREVIEW" and len(model.bodies) == 2


async def test_created_header_revision_drift_never_dispatches_complete_save(
    settings, monkeypatch
):
    from servicenow_mcp.capabilities.flows import target

    model, _, tools = setup(settings)
    original = target.require_current

    async def changed(command, client, before):
        if command.action == "edit":
            model.revision += 1
        await original(command, client, before)

    preview = decode_response(await tools["flow_preview"](**create_request()))
    monkeypatch.setattr(target, "require_current", changed)
    result = await apply(tools, preview)
    assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    assert result["error"]["outcome"]["sys_id"] == FLOW
    assert result["error"]["outcome"]["definition"] == "not_sent"
    assert len(model.bodies) == 1


async def test_full_creation_supports_handler_and_try_subtree_move(settings):
    model, _, tools = setup(settings)
    args = create_request()
    args["edits"].insert(1, {"op": "set_error_handler", "enabled": True})
    args["edits"].extend(
        [
            {"op": "insert_logic", "logic": "Try", "ref": "pair"},
            {
                "op": "insert_action",
                "action_type_sys_id": ACTION,
                "parent": "pair",
                "ref": "nested",
                "inputs": {"message": "Try child"},
            },
            {
                "op": "insert_action",
                "action_type_sys_id": ACTION,
                "parent": "error_handler",
                "ref": "failure",
                "inputs": {"message": "{{error_handler.fixture_status.message}}"},
            },
        ]
    )
    created = await apply(tools, decode_response(await tools["flow_preview"](**args)))
    assert created["status"] == "success", created
    before = copy.deepcopy(model.flow)
    edit = edit_request(created["data"])
    edit["edits"] = [
        {
            "op": "move_step",
            "step": created["data"]["refs"]["pair"]["ui_id"],
            "before": created["data"]["refs"]["branch"]["ui_id"],
        }
    ]
    result = await apply(tools, decode_response(await tools["flow_preview"](**edit)))
    assert result["status"] == "success", result
    old = {s.uid: s for s in tree.walk(tree.build_tree(before))}
    new = {s.uid: s for s in tree.walk(tree.build_tree(model.flow))}
    assert old.keys() == new.keys()
    assert all(old[uid].raw["id"] == new[uid].raw["id"] for uid in old)
    pair = new[created["data"]["refs"]["pair"]["ui_id"]]
    assert pair.children[0].uid == created["data"]["refs"]["nested"]["ui_id"]
    catch = next(s for s in new.values() if s.logic_type == "CATCH")
    assert catch.raw["connectedTo"] == pair.uid


async def test_complete_authoring_native_wire(settings):
    model = StudioModel()
    model.install()
    server = NativeMCPServer("complete-flow-wire")
    register_mutations(
        server, settings, OAuthPKCEProvider(settings), frozenset({"flows"})
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
            preview = await session.call_tool("flow_preview", create_request())
            await session.validate_tool_result("flow_preview", preview)
            assert not preview.is_error and not model.bodies, preview
            created = await session.call_tool(
                "mutation_apply",
                {"preview_token": preview.structured_content["data"]["preview_token"]},
            )
            await session.validate_tool_result("mutation_apply", created)
            assert not created.is_error, created
            preview = await session.call_tool(
                "flow_preview", edit_request(created.structured_content["data"])
            )
            assert not preview.is_error, preview
            edited = await session.call_tool(
                "mutation_apply",
                {"preview_token": preview.structured_content["data"]["preview_token"]},
            )
            await session.validate_tool_result("mutation_apply", edited)
            assert not edited.is_error, edited
            assert model.revision == 15 and len(model.bodies) == 3
        tasks.cancel_scope.cancel()


async def test_documented_full_create_is_the_tested_native_contract(settings):
    doc = (Path(__file__).parents[1] / "docs/flow-authoring.md").read_text()
    example = doc.split("```json\n", 1)[1].split("```", 1)[0]
    for placeholder, identity in {
        "<format-message-snapshot-id>": ACTION,
        "<dispatch-subflow-definition-id>": SUBFLOW,
    }.items():
        example = example.replace(placeholder, identity)
    args = json.loads(example)
    expected = create_request()
    expected.pop("flow_type")
    assert args == expected
    _, _, tools = setup(settings)
    result = await apply(tools, decode_response(await tools["flow_preview"](**args)))
    assert result["status"] == "success", result
