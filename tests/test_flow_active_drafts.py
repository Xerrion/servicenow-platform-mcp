"""Synthetic HTTP regressions for the observed active-draft save profile.

The development capture establishes lifecycle fields, PUT status=modified,
one header revision increment, and runtime selection through the trigger plan.
These fixtures contain no captured business values. Unknown configuration
metadata remains strict, even when runtime preservation succeeds.
"""

import copy
import hashlib
import json
from typing import Any

import httpx2
import pytest

from servicenow_mcp.capabilities.flows import active_drafts, semantics
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_data_edits import typed_flow
from tests.test_flow_editing import BASE, DEFS, FLOW, fixtures

MAIN, SELECTED, TRIGGER, PLAN, PROPERTY = (character * 32 for character in "bcdef")
TIME = "2026-10-22 10:00:00"
SOURCE = b"Synthetic offline Studio source profile"


class ActiveModel:
    def __init__(self, settings, monkeypatch):
        self.flow = typed_flow()
        self.flow.update(
            active=True,
            jsonSnapshot=False,
            masterSnapshotId=MAIN,
            latestSnapshot=SELECTED,
            remoteTriggerSysId=TRIGGER,
            version="13",
            updated=TIME,
        )
        self.flow["actionInstances"] = self.flow["actionInstances"][:1]
        self.flow["subFlowInstances"] = []
        self.header = {
            "sys_id": FLOW,
            "sys_scope": "global",
            "sys_mod_count": "7",
            "sys_updated_on": TIME,
            "active": "true",
            "status": "draft",
            "type": "flow",
            "version": "13",
            "master_snapshot": MAIN,
            "latest_snapshot": SELECTED,
            "remote_trigger_id": TRIGGER,
        }
        self.snapshot: dict[str, Any] = {
            **copy.deepcopy(self.flow),
            "id": SELECTED,
            "snapshot": True,
            "jsonSnapshot": True,
            "isPublished": True,
            "status": "published",
            "parentFlow": FLOW,
            "security": {"can_read": True},
        }
        self.main = {
            "sys_id": MAIN,
            "parent_flow": FLOW,
            "master": "true",
            "active": "true",
            "status": "published",
            "version": "13",
            "sys_scope": "global",
            "sys_mod_count": "0",
            "sys_updated_on": TIME,
        }
        self.trigger: dict[str, Any] = {
            "sys_id": TRIGGER,
            "active": "true",
            "sys_class_name": "sys_flow_catalog_trigger",
            "sys_mod_count": "0",
            "sys_updated_on": TIME,
            "opaque": {"setting": 1},
        }
        self.plans: list[dict[str, Any]] = [
            {
                "sys_id": PLAN,
                "trigger": TRIGGER,
                "snapshot": SELECTED,
                "plan_id": FLOW,
                "plan": "{}",
                "binding_strategy": "synthetic",
                "sys_mod_count": "0",
                "sys_updated_on": TIME,
                "opaque": {"setting": 1},
            }
        ]
        self.properties = [
            {
                "sys_id": PROPERTY,
                "name": "sn_flow_designer.save_as_you_go_enabled",
                "value": "false",
                "sys_mod_count": "0",
                "sys_updated_on": TIME,
            }
        ]
        self.source = SOURCE
        self.writes = []
        self.after_save = None
        self.change_echo = None
        self.coordinator, self.tools = fixtures(settings, self.flow)
        monkeypatch.setattr(
            active_drafts, "SOURCE_SHA256", hashlib.sha256(SOURCE).hexdigest()
        )
        for path, getter in (
            (f"/api/now/table/sys_hub_flow/{FLOW}", lambda: self.header),
            (f"/api/now/table/sys_hub_flow_snapshot/{MAIN}", lambda: self.main),
            (f"/api/now/table/sys_flow_trigger/{TRIGGER}", lambda: self.trigger),
            ("/api/now/table/sys_flow_trigger_plan", lambda: self.plans),
            ("/api/now/table/sys_properties", lambda: self.properties),
        ):

            def respond(request, getter=getter):
                return httpx2.Response(200, json={"result": copy.deepcopy(getter())})

            http_mock.get(BASE + path).mock(side_effect=respond)
        http_mock.get(f"{BASE}/api/now/processflow/flow/{SELECTED}").mock(
            side_effect=lambda _: httpx2.Response(
                200, json={"result": {"data": copy.deepcopy(self.snapshot)}}
            )
        )
        http_mock.get(f"{BASE}/scripts/designer-bundle.min.js").mock(
            side_effect=lambda _: httpx2.Response(200, content=self.source)
        )
        self.write = http_mock.route(
            method="PUT", url=f"{BASE}/api/now/processflow/flow"
        ).mock(side_effect=self.save)

    def save(self, request):
        body = json.loads(request.content)
        self.writes.append(copy.deepcopy(body))
        assert body["status"] == "modified" and body["active"] is True
        assert body["version"] == "13" and body["latestSnapshot"] == SELECTED
        saved = {**body, "status": "draft", "updated": "2026-10-22 10:01:00"}
        self.flow.clear()
        self.flow.update(copy.deepcopy(saved))
        self.header.update(sys_mod_count="8", sys_updated_on=saved["updated"])
        if self.after_save:
            self.after_save()
        echo = copy.deepcopy(self.flow)
        if self.change_echo:
            self.change_echo(echo)
        return httpx2.Response(200, json={"result": {"data": echo}})

    async def preview(self, **overrides):
        args = {
            "action": "edit",
            "sys_id": FLOW,
            "expected_scope": "global",
            "expected_revision": "7",
            "edits": [
                {
                    "op": "update_inputs",
                    "step": "1",
                    "inputs": {"log_message": "Synthetic revised message"},
                }
            ],
        }
        args.update(overrides)
        return decode_response(await self.tools["flow_preview"](**args))

    async def apply(self, preview):
        return decode_response(
            await self.tools["mutation_apply"](
                preview_token=preview["data"]["preview_token"]
            )
        )


async def test_active_draft_preview_and_one_native_save(settings, monkeypatch):
    model = ActiveModel(settings, monkeypatch)
    original = copy.deepcopy(model.flow)
    preview = await model.preview()
    assert preview["status"] == "success", preview
    assert preview["data"]["summary"]["active"] is True
    assert all(call.request.method == "GET" for call in http_mock.calls)
    result = await model.apply(preview)
    assert result["status"] == "success", result
    assert result["data"]["active"] is True and result["data"]["status"] == "draft"
    assert (
        result["data"]["revision"] == "8"
        and result["data"]["runtime_preservation"] == "verified"
    )
    assert len(model.writes) == 1
    assert (
        model.writes[0]["actionInstances"][0]["inputs"][0]["value"]
        == "Synthetic revised message"
    )
    assert model.writes[0]["flowVariables"] == original["flowVariables"]
    assert model.writes[0]["masterSnapshotId"] == MAIN
    assert model.writes[0]["remoteTriggerSysId"] == TRIGGER
    assert "capture" not in result["data"]
    assert (await model.apply(preview))["status"] == "error"
    assert len(model.writes) == 1
    assert all(
        "sys_update_" not in call.request.url.path
        and "concoursepicker" not in call.request.url.path
        for call in http_mock.calls
    )
    assert all(
        call.request.method == "GET"
        or call.request.url.path == "/api/now/processflow/flow"
        and call.request.method == "PUT"
        for call in http_mock.calls
    )


@pytest.mark.parametrize(
    "change",
    [
        {"status": "published"},
        {"type": "subflow"},
        {"isPublished": True},
        {"snapshot": True},
        {"jsonSnapshot": True},
        {"masterSnapshot": True},
        {"flowEngineVersion": 1},
        {"active": "true"},
        {"masterSnapshotId": ""},
        {"latestSnapshot": "not-an-id"},
        {"remoteTriggerSysId": ""},
        {"latestSnapshot": {"value": SELECTED}},
        {"version": None},
        {"parentFlow": MAIN},
        {"security": {"can_write": False}},
        {"actionInstances": None},
    ],
)
async def test_unknown_active_profiles_never_stage(settings, monkeypatch, change):
    model = ActiveModel(settings, monkeypatch)
    model.flow.update(change)
    result = await model.preview()
    assert result["status"] == "error", result
    assert len(model.coordinator.store) == 0 and not model.writes


@pytest.mark.parametrize(
    "binding",
    [
        {"expected_scope": None},
        {"expected_revision": None},
        {"expected_scope": None, "expected_revision": None},
    ],
)
async def test_active_drafts_require_both_target_bindings(
    settings, monkeypatch, binding
):
    model = ActiveModel(settings, monkeypatch)
    result = await model.preview(**binding)
    assert (
        result["status"] == "error"
        and "require expected_scope" in result["error"]["message"]
    )
    assert not model.writes


@pytest.mark.parametrize(
    "defect",
    [
        "lock",
        "property_missing",
        "property_ambiguous",
        "source",
        "plan_ambiguous",
        "plan_owner",
        "plan_trigger",
        "plan_snapshot",
        "snapshot_owner",
        "snapshot_published",
        "snapshot_json",
        "snapshot_read",
        "main_owner",
        "main_version",
        "trigger_inactive",
    ],
)
async def test_missing_runtime_proof_blocks_preview(settings, monkeypatch, defect):
    model = ActiveModel(settings, monkeypatch)
    if defect == "lock":
        model.properties[0]["value"] = "true"
    elif defect == "property_missing":
        model.properties.clear()
    elif defect == "property_ambiguous":
        model.properties *= 2
    elif defect == "source":
        model.source = b"Unknown Studio build"
    elif defect == "plan_ambiguous":
        model.plans *= 2
    elif defect == "plan_owner":
        model.plans[0]["plan_id"] = MAIN
    elif defect == "plan_trigger":
        model.plans[0]["trigger"] = MAIN
    elif defect == "plan_snapshot":
        model.plans[0]["snapshot"] = PLAN
    elif defect == "snapshot_owner":
        model.snapshot["parentFlow"] = MAIN
    elif defect == "snapshot_published":
        model.snapshot["isPublished"] = False
    elif defect == "snapshot_json":
        model.snapshot["jsonSnapshot"] = False
    elif defect == "snapshot_read":
        model.snapshot["security"]["can_read"] = False
    elif defect == "main_owner":
        model.main["parent_flow"] = MAIN
    elif defect == "main_version":
        model.main["version"] = "14"
    elif defect == "trigger_inactive":
        model.trigger["active"] = "false"
    result = await model.preview()
    assert result["status"] == "error", result
    assert not model.writes and len(model.coordinator.store) == 0


@pytest.mark.parametrize(
    "defect",
    [
        "lifecycle",
        "scope",
        "revision",
        "definition",
        "source",
        "property",
        "plan",
        "trigger",
        "snapshot",
        "main",
    ],
)
async def test_apply_revalidates_all_preview_evidence(settings, monkeypatch, defect):
    model = ActiveModel(settings, monkeypatch)
    preview = await model.preview()
    assert preview["status"] == "success", preview
    if defect == "lifecycle":
        model.flow["latestSnapshot"] = PLAN
    elif defect == "scope":
        model.header["sys_scope"] = MAIN
    elif defect == "revision":
        model.header["sys_mod_count"] = "8"
    elif defect == "definition":
        model.flow["actionInstances"][0]["inputs"][0]["parameter"]["maxsize"] = 254
    elif defect == "source":
        model.source += b" changed"
    elif defect == "property":
        model.properties[0]["sys_mod_count"] = "1"
    elif defect == "plan":
        model.plans[0]["opaque"]["setting"] = 2
    elif defect == "trigger":
        model.trigger["opaque"]["setting"] = 2
    elif defect == "snapshot":
        model.snapshot["annotation"] = "Synthetic drift"
    elif defect == "main":
        model.main["sys_mod_count"] = "1"
    assert (await model.apply(preview))["status"] == "error"
    assert not model.writes
    assert (await model.apply(preview))["status"] == "error"


async def test_runtime_race_immediately_before_put_is_rejected(settings, monkeypatch):
    model = ActiveModel(settings, monkeypatch)
    preview = await model.preview()
    original = active_drafts.require_current

    async def race(*args):
        model.plans[0]["opaque"]["setting"] = 2
        await original(*args)

    monkeypatch.setattr(active_drafts, "require_current", race)
    result = await model.apply(preview)
    assert result["error"]["code"] == "STALE_PREVIEW", result
    assert not model.writes


@pytest.mark.parametrize(
    "defect",
    [
        "activation",
        "reference",
        "version",
        "plan",
        "trigger",
        "snapshot",
        "revision_jump",
        "revision_unchanged",
        "scope",
        "main",
        "configuration",
        "unknown_metadata",
        "echo_lifecycle",
        "echo_configuration",
    ],
)
async def test_post_save_drift_is_unknown_and_never_replayed(
    settings, monkeypatch, defect
):
    model = ActiveModel(settings, monkeypatch)

    def drift():
        if defect == "activation":
            model.flow["active"] = False
        elif defect == "reference":
            model.flow["latestSnapshot"] = MAIN
        elif defect == "version":
            model.flow["version"] = "14"
        elif defect == "plan":
            model.plans[0]["plan"] = '{"changed":true}'
        elif defect == "trigger":
            model.trigger["active"] = "false"
        elif defect == "snapshot":
            model.snapshot["annotation"] = "Synthetic drift"
        elif defect == "revision_jump":
            model.header["sys_mod_count"] = "9"
        elif defect == "revision_unchanged":
            model.header["sys_mod_count"] = "7"
        elif defect == "scope":
            model.header["sys_scope"] = MAIN
        elif defect == "main":
            model.main["version"] = "14"
        elif defect == "configuration":
            model.flow["actionInstances"][0]["inputs"][0]["value"] = "Unexpected value"
        elif defect == "unknown_metadata":
            model.flow["flowVariables"][0]["attributes"] = {
                "unprovenMetadata": "Synthetic normalization"
            }

    model.after_save = drift
    if defect == "echo_lifecycle":
        model.change_echo = lambda echo: echo.update(active=False)
    elif defect == "echo_configuration":
        model.change_echo = lambda echo: echo.update(annotation="Only echo changed")
    result = await model.apply(await model.preview())
    assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    if defect in {"configuration", "unknown_metadata", "echo_configuration"}:
        assert result["error"]["outcome"]["runtime_preservation"] == "verified", result
        assert "configuration verification" in result["error"]["message"]
    assert len(model.writes) == 1


async def test_main_snapshot_get_representation_is_not_a_runtime_invariant(
    settings, monkeypatch
):
    model = ActiveModel(settings, monkeypatch)
    main_get = http_mock.get(f"{BASE}/api/now/processflow/flow/{MAIN}").respond(
        200, json={"result": {"data": {"changingRepresentation": True}}}
    )
    result = await model.apply(await model.preview())
    assert result["status"] == "success", result
    assert not main_get.called


async def test_ambiguous_save_is_not_retried_and_consumes_token(settings, monkeypatch):
    model = ActiveModel(settings, monkeypatch)
    preview = await model.preview()
    model.write.mock(side_effect=httpx2.ReadTimeout("Synthetic timeout"))
    result = await model.apply(preview)
    assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    assert model.write.call_count == 1
    assert (await model.apply(preview))["status"] == "error"
    assert model.write.call_count == 1


async def test_authorization_generation_still_protects_active_apply(
    settings, monkeypatch
):
    model = ActiveModel(settings, monkeypatch)
    preview = await model.preview()
    model.coordinator.auth._generation += 1
    assert (await model.apply(preview))["status"] == "error"
    assert not model.writes


async def test_active_plan_dependency_catalog_is_revalidated(settings, monkeypatch):
    model = ActiveModel(settings, monkeypatch)
    preview = await model.preview(
        edits=[{"op": "insert_logic", "logic": "End", "after": "1"}]
    )
    assert preview["status"] == "success", preview
    changed = copy.deepcopy(DEFS["flow_logic"])
    changed[0]["name"] = "Synthetic catalog drift"
    http_mock.get(f"{BASE}/api/now/processflow/flow_logic/types").respond(
        200, json={"result": changed}
    )
    result = await model.apply(preview)
    assert result["status"] == "error", result
    assert not model.writes


async def test_same_value_active_preview_keeps_structural_semantics(
    settings, monkeypatch
):
    model = ActiveModel(settings, monkeypatch)
    model.flow["actionInstances"][0]["inputs"][0]["displayValue"] = "one"
    preview = await model.preview(
        edits=[{"op": "update_inputs", "step": "1", "inputs": {"log_message": "one"}}]
    )
    assert preview["status"] == "success", preview
    assert preview["data"]["summary"]["serialization_check"]["matches"] is True, (
        preview["data"]["summary"]["serialization_check"]
    )
    assert not model.writes


async def test_active_preview_expiry_is_still_enforced(settings, monkeypatch):
    from dataclasses import replace

    model = ActiveModel(settings, monkeypatch)
    preview = await model.preview()
    store = model.coordinator.store
    key = preview["data"]["preview_token"]
    entry = store._entries[key]
    store._entries[key] = replace(entry, created_at=entry.created_at - 301)
    result = await model.apply(preview)
    assert result["error"]["code"] == "PREVIEW_EXPIRED", result
    assert not model.writes


def test_semantics_opt_in_does_not_change_inactive_validation():
    flow = typed_flow()
    assert semantics.studio_semantics(flow) == semantics.studio_semantics(
        flow, allow_active_draft=True
    )


def test_active_structural_opt_in_does_not_broaden_local_validation(
    settings, monkeypatch
):
    model = ActiveModel(settings, monkeypatch)
    with pytest.raises(semantics.IncompleteSemantics):
        semantics.studio_semantics(model.flow)
    assert semantics.studio_semantics(model.flow, allow_active_draft=True)
