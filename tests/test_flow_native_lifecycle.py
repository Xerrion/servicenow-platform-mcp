"""Native root lifecycle responses and immutable selected-version preservation."""

import copy
import json
from pathlib import Path
from typing import override

import httpx2
import pytest

from servicenow_mcp.capabilities.flows import active_drafts
from tests._compiled_snapshot_fixture import snapshot_storage
from tests._native_full_fixture import (
    FLOW,
    STUDIO,
    NativeFullModel,
    create_request,
    edit_request,
)
from tests.helpers import decode_response
from tests.test_flow_complete_creation import apply, setup

MAIN, SELECTED, REMOTE = (value * 16 for value in ("c1", "c2", "c3"))
OBSERVED = json.loads(
    (Path(__file__).parent / "fixtures/flow_native_lifecycle.json").read_text()
)


class NativeLifecycleModel(NativeFullModel):
    def __init__(self):
        super().__init__()
        self.selected = None
        self.serialized_storage: tuple[dict[str, str], list[dict[str, str]]] | None = (
            None
        )
        self.lifecycle_writes = []
        self.unset_echo_engine = False
        self.native_master = False
        self.runtime_drift = False
        self.echo_defect = None
        self.runtime_read_defect = None

    @override
    def _saved(self, body):
        result = super()._saved(body)
        if self.native_master:
            if not self.selected:
                result.update(copy.deepcopy(OBSERVED["root_before"]))
            if self.selected:
                result.update(
                    active=body["active"],
                    status="draft",
                    isPublished=False,
                    masterSnapshotId=MAIN,
                    latestSnapshot=MAIN,
                    remoteTriggerSysId=REMOTE,
                    version="1",
                )
            if self.runtime_drift:
                self.runtime_message = "Unexpected runtime change"
        return result

    @override
    def handle(self, request):
        path = request.url.path
        if request.method == "POST" and path == STUDIO + "/flow/" + FLOW + "/snapshot":
            self.calls.append((request.method, path))
            self.lifecycle_writes.append(json.loads(request.content))
            assert self.flow is not None
            self.flow.update(
                active=True,
                status="draft",
                isPublished=False,
                masterSnapshotId=MAIN,
                latestSnapshot=SELECTED,
                remoteTriggerSysId=REMOTE,
                version="1",
            )
            self.selected = {
                **copy.deepcopy(self.flow),
                "id": SELECTED,
                "parentFlow": FLOW,
                "status": "published",
                "snapshot": True,
                "jsonSnapshot": True,
                "isPublished": True,
            }
            self.serialized_storage = snapshot_storage(self.selected)
            self.advance()
            if self.native_master:
                self.flow.update(
                    status="published", isPublished=True, latestSnapshot=MAIN
                )
                self.flow["attributes"] = copy.deepcopy(
                    OBSERVED["publish_echo"]["attributes"]
                )
                self.advance()
            echo = copy.deepcopy(self.flow)
            if self.unset_echo_engine:
                echo["flowEngineVersion"] = 0
            if self.echo_defect == "engine":
                echo["flowEngineVersion"] = 1
            elif self.echo_defect == "telemetry":
                echo["attributes"]["timeFromCreateToActivate"] = "Not a duration"
            elif self.echo_defect == "unknown_attribute":
                echo["attributes"]["unexpected"] = "changed"
            elif self.echo_defect == "boolean_engine":
                echo["flowEngineVersion"] = False
            elif self.echo_defect == "missing_version":
                echo.pop("version", None)
                self.flow.pop("version", None)
            return httpx2.Response(
                200, json={"result": {"data": echo}}, request=request
            )
        if request.method == "GET" and path == STUDIO + "/flow/" + FLOW + "/deactivate":
            self.calls.append((request.method, path))
            self.lifecycle_writes.append(None)
            assert self.flow is not None
            self.flow["active"] = False
            self.advance()
            echo = copy.deepcopy(self.flow)
            if self.native_master:
                echo.update(copy.deepcopy(OBSERVED["deactivate_echo"]))
            if self.echo_defect == "roles":
                echo["runAs"] = "system"
            return httpx2.Response(
                200, json={"result": {"data": echo}}, request=request
            )
        if request.method == "GET" and path == "/api/now/table/sys_hub_flow/" + FLOW:
            result = super().handle(request)
            data = result.json()["result"]
            assert self.flow is not None
            data.update(
                active=str(self.flow["active"]).lower(),
                status=self.flow["status"],
                version=self.flow["version"],
                master_snapshot=self.flow["masterSnapshotId"],
                latest_snapshot=self.flow.get("latestSnapshot", ""),
                remote_trigger_id=self.flow.get("remoteTriggerSysId", ""),
            )
            return httpx2.Response(200, json={"result": data}, request=request)
        if (
            request.method == "GET"
            and path == "/api/now/table/sys_hub_snapshot/" + SELECTED
        ):
            assert self.serialized_storage is not None
            return httpx2.Response(
                200, json={"result": self.serialized_storage[0]}, request=request
            )
        if request.method == "GET" and path == "/api/now/table/sys_hub_snapshot_chunk":
            assert self.serialized_storage is not None
            return httpx2.Response(
                200, json={"result": self.serialized_storage[1]}, request=request
            )
        result = None
        if (
            request.method == "GET"
            and path == "/api/now/table/sys_hub_flow_snapshot/" + MAIN
        ):
            result = {
                "sys_id": MAIN,
                "parent_flow": FLOW,
                "master": "true",
                "active": "true",
                "status": "published",
                "version": "1",
                "sys_scope": "global",
                "sys_mod_count": "0",
                "sys_updated_on": "2026-10-23 10:00:00",
            }
        elif request.method == "GET" and path == STUDIO + "/flow/" + SELECTED:
            result = {"data": self.selected}
        elif request.method == "GET" and path == STUDIO + "/flow/" + MAIN:
            assert self.selected is not None
            result = {
                "data": {
                    **self.selected,
                    "id": MAIN,
                    "jsonSnapshot": False,
                    "masterSnapshot": True,
                }
            }
        elif (
            request.method == "GET"
            and path.startswith("/api/now/table/")
            and path.rsplit("/", 1)[-1]
            in {row[0] for row in active_drafts.RUNTIME_TABLES.values()}
        ):
            result = []
            if path.endswith("/sys_hub_action_instance_v2"):
                result = [
                    {
                        **dict.fromkeys(active_drafts.COMPONENT_REQUIRED, ""),
                        "action_type_parent": "d1" * 16,
                        "compiled_snapshot": "d1" * 16,
                        "sys_id": "f1" * 16,
                        "flow": MAIN,
                        "sys_mod_count": "0",
                        "sys_updated_on": "2026-10-23 10:00:00",
                        "values": getattr(
                            self,
                            "runtime_message",
                            "Synthetic immutable published message",
                        ),
                        "action_type": "d1" * 16,
                        "order": "1",
                        "parent_ui_id": "",
                        "ui_id": "synthetic-version-ui",
                    }
                ]
                if self.runtime_read_defect == "hidden_values":
                    result[0].pop("values")
                elif self.runtime_read_defect == "wrong_owner":
                    result[0]["flow"] = FLOW
                elif self.runtime_read_defect == "row_cap":
                    result *= 1000
        elif request.method == "GET" and path == "/api/now/table/sys_properties":
            result = [
                {
                    "sys_id": "e1" * 16,
                    "name": "sn_flow_designer.save_as_you_go_enabled",
                    "value": "false",
                    "sys_mod_count": "0",
                    "sys_updated_on": "2026-10-23 10:00:00",
                }
            ]
        elif request.method == "GET" and path == "/api/now/table/sys_flow_trigger_plan":
            result = [
                {
                    "sys_id": "e2" * 16,
                    "trigger": REMOTE,
                    "snapshot": SELECTED,
                    "plan_id": FLOW,
                    "plan": "{}",
                    "binding_strategy": "native",
                    "sys_mod_count": "0",
                    "sys_updated_on": "2026-10-23 10:00:00",
                }
            ]
            if self.native_master:
                result[0].update(OBSERVED["trigger_plan"], snapshot=MAIN)
        elif (
            request.method == "GET"
            and path == "/api/now/table/sys_flow_trigger/" + REMOTE
        ):
            result = {
                "sys_id": REMOTE,
                "active": "true",
                "sys_class_name": "sys_flow_catalog_trigger",
                "sys_mod_count": "0",
                "sys_updated_on": "2026-10-23 10:00:00",
            }
        if result is not None:
            self.calls.append((request.method, path))
            return httpx2.Response(200, json={"result": result}, request=request)
        return super().handle(request)


async def test_native_activate_active_reorder_insert_update_then_deactivate(settings):
    _, _, tools = setup(settings)
    model = NativeLifecycleModel()
    model.install()
    created = await apply(
        tools, decode_response(await tools["flow_preview"](**create_request()))
    )
    assert created["status"] == "success", created
    activate = {
        "action": "activate",
        "sys_id": FLOW,
        "expected_scope": "global",
        "expected_revision": created["data"]["revision"],
    }
    published = await apply(
        tools, decode_response(await tools["flow_preview"](**activate))
    )
    assert published["status"] == "success", published
    assert (
        published["data"]["active"] is True and published["data"]["status"] == "draft"
    )
    runtime = copy.deepcopy(model.selected)
    request = edit_request(
        {**created["data"], "revision": published["data"]["revision"]}
    )
    edited = await apply(tools, decode_response(await tools["flow_preview"](**request)))
    assert edited["status"] == "success", edited
    assert model.flow is not None
    assert edited["data"]["runtime_preservation"] == "verified"
    assert model.selected == runtime and model.flow["active"] is True
    stopped = await apply(
        tools,
        decode_response(
            await tools["flow_preview"](
                action="deactivate",
                sys_id=FLOW,
                expected_scope="global",
                expected_revision=edited["data"]["revision"],
            )
        ),
    )
    assert stopped["status"] == "success", stopped
    assert model.flow is not None
    assert model.flow["active"] is False and model.selected == runtime
    assert len(model.lifecycle_writes) == 2
    assert not any("designer-bundle" in path for _, path in model.calls)


async def test_native_unset_publish_echo_engine_requires_independent_v2_owner_and_runtime(
    settings,
):
    _, _, tools = setup(settings)
    model = NativeLifecycleModel()
    model.unset_echo_engine = True
    model.install()
    created = await apply(
        tools, decode_response(await tools["flow_preview"](**create_request()))
    )
    assert created["status"] == "success", created
    published = await apply(
        tools,
        decode_response(
            await tools["flow_preview"](
                action="activate",
                sys_id=FLOW,
                expected_scope="global",
                expected_revision=created["data"]["revision"],
            )
        ),
    )
    assert published["status"] == "success", published
    assert published["data"]["native_echo_engine_unset"] is True
    assert published["data"]["runtime_snapshot_sys_id"] == SELECTED


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "engine",
        "boolean_engine",
        "missing_version",
        "telemetry",
        "unknown_attribute",
        "runtime",
        "roles",
    ],
)
async def test_captured_native_master_lifecycle_is_strict(settings, defect):
    _, _, tools = setup(settings)
    model = NativeLifecycleModel()
    model.native_master = True
    model.unset_echo_engine = True
    model.install()
    created = await apply(
        tools, decode_response(await tools["flow_preview"](**create_request()))
    )
    assert created["status"] == "success", created
    model.echo_defect = defect
    published = await apply(
        tools,
        decode_response(
            await tools["flow_preview"](
                action="activate",
                sys_id=FLOW,
                expected_scope="global",
                expected_revision=created["data"]["revision"],
            )
        ),
    )
    if defect in {
        "engine",
        "boolean_engine",
        "missing_version",
        "telemetry",
        "unknown_attribute",
    }:
        assert published["error"]["code"] == "UNKNOWN_OUTCOME", published
        assert len(model.lifecycle_writes) == 1
        return
    assert published["status"] == "success", published
    model.runtime_drift = defect == "runtime"
    edited = await apply(
        tools,
        decode_response(
            await tools["flow_preview"](
                **edit_request(
                    {**created["data"], "revision": published["data"]["revision"]}
                )
            )
        ),
    )
    if defect == "runtime":
        assert edited["error"]["code"] == "UNKNOWN_OUTCOME", edited
        return
    assert edited["status"] == "success", edited
    assert (
        edited["data"]["runtime_preservation"] == "verified"
        and edited["data"]["status"] == "draft"
    )
    stopped = await apply(
        tools,
        decode_response(
            await tools["flow_preview"](
                action="deactivate",
                sys_id=FLOW,
                expected_scope="global",
                expected_revision=edited["data"]["revision"],
            )
        ),
    )
    if defect == "roles":
        assert stopped["error"]["code"] == "UNKNOWN_OUTCOME", stopped
    else:
        assert stopped["status"] == "success", stopped
    assert model.flow is not None and model.flow["active"] is False
    if defect is None:
        inactive = await apply(
            tools,
            decode_response(
                await tools["flow_preview"](
                    action="edit",
                    sys_id=FLOW,
                    expected_scope="global",
                    expected_revision=stopped["data"]["revision"],
                    edits=[
                        {
                            "op": "update_inputs",
                            "step": created["data"]["refs"]["start"]["sys_id"],
                            "inputs": {
                                "log_message": "Synthetic inactive published edit"
                            },
                        }
                    ],
                )
            ),
        )
        assert inactive["status"] == "success", inactive
        assert (
            inactive["data"]["active"] is False
            and inactive["data"]["runtime_preservation"] == "verified"
        )


@pytest.mark.parametrize("defect", ["hidden_values", "wrong_owner", "row_cap"])
async def test_persisted_runtime_requires_complete_owned_uncapped_content(
    settings, defect
):
    _, _, tools = setup(settings)
    model = NativeLifecycleModel()
    model.native_master = True
    model.install()
    created = await apply(
        tools, decode_response(await tools["flow_preview"](**create_request()))
    )
    published = await apply(
        tools,
        decode_response(
            await tools["flow_preview"](
                action="activate",
                sys_id=FLOW,
                expected_scope="global",
                expected_revision=created["data"]["revision"],
            )
        ),
    )
    assert published["status"] == "success", published
    writes = len(model.bodies)
    model.runtime_read_defect = defect
    preview = decode_response(
        await tools["flow_preview"](
            **edit_request(
                {**created["data"], "revision": published["data"]["revision"]}
            )
        )
    )
    assert preview["error"]["code"] == "UNAVAILABLE", preview
    assert len(model.bodies) == writes
