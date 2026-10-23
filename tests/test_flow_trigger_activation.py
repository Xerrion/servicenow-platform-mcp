"""Activation ownership from actual native trigger/table metadata, not defaults."""

import copy
import json
from pathlib import Path

import httpx2
import pytest

from tests._mock_transport import http_mock
from tests.test_flow_trigger_readback import (
    BASE,
    CATALOG_URL,
    FLOW,
    REMOTE,
    blob,
    instance_catalog,
    read_flow,
)

NATIVE = json.loads(
    (Path(__file__).parent / "fixtures/flow_trigger_activation.json").read_text()
)


class ActivationReadModel:
    """Captured Table API responses, with no writable endpoint or request echo."""

    def handle(self, request):
        assert request.method == "GET" and request.url.host == "test.service-now.com"
        path = request.url.path
        if path == "/api/now/table/sys_hub_flow/" + FLOW:
            result = NATIVE["flow"]
        elif path == "/api/now/table/sys_hub_trigger_instance_v2":
            assert request.url.params["sysparm_query"] == "flow=" + FLOW
            result = [NATIVE["trigger"]]
        elif path == "/api/now/trigger_api/trigger_categories":
            result = instance_catalog()
        else:
            assert path.rsplit("/", 1)[1] in {
                "sys_hub_trigger_instance",
                "sys_hub_action_instance",
                "sys_hub_action_instance_v2",
                "sys_hub_flow_logic",
                "sys_hub_flow_logic_instance_v2",
                "sys_hub_sub_flow_instance_v2",
            }, path
            result = []
        return httpx2.Response(200, json={"result": result}, request=request)


@pytest.mark.parametrize("action", ["inspect", "contract"])
@pytest.mark.parametrize("flow_active", ["true", "false"])
async def test_actual_native_trigger_has_flow_owned_activation_without_instance_flag(
    settings, action, flow_active
):
    header = copy.deepcopy(NATIVE["flow"])
    header["active"] = flow_active
    row = copy.deepcopy(NATIVE["trigger"])
    assert "active" not in row
    assert (
        next(t for t in NATIVE["tables"] if t["name"] == "sys_hub_trigger_instance_v2")[
            "super_class"
        ]
        == ""
    )
    assert NATIVE["active_dictionary"] == [
        {"name": "sys_hub_flow_base", "element": "active", "type": "boolean"}
    ]
    http_mock.get(CATALOG_URL).respond(200, json={"result": instance_catalog()})
    result = await read_flow(settings, action, [row], header=header)
    trigger = result["data"]["triggers"][0]
    assert trigger["metadata_complete"] is True, result
    assert trigger["active"] is None and trigger["active_source"] is None
    assert trigger["activation"] == {
        "mode": "flow_managed",
        "instance_active_applicable": False,
        "flow_sys_id": header["sys_id"],
        "flow_active": flow_active == "true",
        "source": "flow.active",
    }
    assert trigger["warnings"] == []
    assert result["data"]["structural_summary"]["complete"] is True
    assert result["data"]["structural_summary"]["metadata_complete"] is None
    assert "truncation" not in result


@pytest.mark.parametrize("action", ["inspect", "contract"])
@pytest.mark.parametrize(
    "value, expected",
    [
        ("true", True),
        ("false", False),
        (True, True),
        (False, False),
        ({"value": "false"}, False),
        (None, None),
        ("", None),
        ({"value": None}, None),
        ("invalid", None),
    ],
)
async def test_present_instance_flag_is_not_replaced_by_parent_state(
    settings, action, value, expected
):
    row = {**copy.deepcopy(NATIVE["trigger"]), "active": value}
    header = {**NATIVE["flow"], "active": "false" if expected is True else "true"}
    http_mock.get(CATALOG_URL).respond(200, json={"result": instance_catalog()})
    result = await read_flow(settings, action, [row], header=header)
    trigger = result["data"]["triggers"][0]
    assert trigger["active"] is expected
    assert "activation" not in trigger
    assert trigger["active_source"] == (
        "trigger.active" if expected is not None else None
    )
    assert trigger["metadata_complete"] is (expected is not None)
    assert ("triggers" in result.get("truncation", {})) is (expected is None)


@pytest.mark.parametrize("action", ["inspect", "contract"])
@pytest.mark.parametrize(
    "defect",
    [
        "missing_owner",
        "wrong_owner",
        "missing_header_id",
        "subflow",
        "missing_active",
        "null_active",
        "invalid_active",
    ],
)
async def test_flow_managed_activation_requires_readable_owner_and_parent_flag(
    settings, action, defect
):
    row = copy.deepcopy(NATIVE["trigger"])
    header = copy.deepcopy(NATIVE["flow"])
    if defect == "missing_owner":
        row.pop("flow")
    elif defect == "wrong_owner":
        row["flow"] = "e" * 32
    elif defect == "missing_header_id":
        header.pop("sys_id")
    elif defect == "subflow":
        header["type"] = "subflow"
    elif defect == "missing_active":
        header.pop("active")
    elif defect == "null_active":
        header["active"] = None
    else:
        header["active"] = "invalid"
    http_mock.get(CATALOG_URL).respond(200, json={"result": instance_catalog()})
    result = await read_flow(
        settings,
        action,
        [row],
        header=header,
        sections=["flow", "triggers", "structural_summary", "warnings"],
    )
    trigger = result["data"]["triggers"][0]
    assert trigger["active"] is None
    assert trigger["metadata_complete"] is False and trigger["warnings"]
    assert result["truncation"]["triggers"]["incomplete_metadata"]
    assert result["data"]["structural_summary"]["metadata_complete"] is False
    if "active" in defect:
        assert trigger["activation"]["flow_active"] is None
        assert result["data"]["flow"]["active"] is None
    else:
        assert "activation" not in trigger


@pytest.mark.parametrize(
    "kind", ["scheduled", "daily", "weekly", "monthly", "repeat", "run_once"]
)
async def test_native_schedule_configuration_has_no_independent_instance_switch(
    settings, kind
):
    definition = {
        "id": "d" * 32,
        "type": kind,
        "label": "Schedule",
        "has_dynamic_outputs": False,
        "inputs": [],
        "outputs": [],
    }
    http_mock.get(CATALOG_URL).respond(
        200,
        json={"result": [{"name": "Scheduled", "triggers": {"Schedule": definition}}]},
    )
    row = {
        **copy.deepcopy(NATIVE["trigger"]),
        "trigger_type": kind,
        "trigger_inputs": blob([]),
    }
    result = await read_flow(
        settings, "contract", [row], header=copy.deepcopy(NATIVE["flow"])
    )
    trigger = result["data"]["triggers"][0]
    assert trigger["active"] is None and trigger["activation"]["flow_active"] is False
    assert trigger["activation"]["mode"] == "flow_managed"
    assert trigger["metadata_complete"] is True and "truncation" not in result


@pytest.mark.parametrize("kind", ["inbound_email", "custom_app"])
async def test_unmodeled_activation_families_remain_unknown(settings, kind):
    definition = {
        "id": "d" * 32,
        "type": kind,
        "label": "Other",
        "has_dynamic_outputs": False,
        "inputs": [],
        "outputs": [],
    }
    http_mock.get(CATALOG_URL).respond(
        200,
        json={"result": [{"name": "Application", "triggers": {"Other": definition}}]},
    )
    row = {
        **copy.deepcopy(NATIVE["trigger"]),
        "trigger_type": kind,
        "trigger_inputs": blob([]),
    }
    result = await read_flow(
        settings, "contract", [row], header=copy.deepcopy(NATIVE["flow"])
    )
    trigger = result["data"]["triggers"][0]
    assert trigger["active"] is None and "activation" not in trigger
    assert trigger["metadata_complete"] is False
    assert result["truncation"]["triggers"]["incomplete_metadata"]


@pytest.mark.parametrize(
    "value, expected",
    [
        ("true", True),
        ("false", False),
        (True, True),
        (False, False),
        ("absent", None),
        (None, None),
        ({"value": None}, None),
        ("invalid", None),
    ],
)
@pytest.mark.parametrize("action", ["inspect", "contract"])
async def test_v1_flag_remains_source_owned_and_missing_does_not_mean_false(
    settings, action, value, expected
):
    row = {
        "sys_id": "b" * 32,
        "type": "record_update",
        "flow": FLOW,
        "table": "incident",
        "condition": "",
    }
    if value != "absent":
        row["active"] = value
    result = await read_flow(
        settings,
        action,
        [],
        v1_rows=[row],
        header={**NATIVE["flow"], "active": "false" if expected is True else "true"},
    )
    trigger = result["data"]["triggers"][0]
    assert trigger["active"] is expected and "activation" not in trigger
    assert ("triggers" in result.get("truncation", {})) is (expected is None)


@pytest.mark.parametrize(
    "value, expected",
    [("true", True), ("false", False), (None, None), ("absent", None)],
)
async def test_v2_record_trigger_keeps_its_remote_activation_source(
    settings, value, expected
):
    definition = {
        "id": "d" * 32,
        "type": "record_update",
        "label": "Updated",
        "has_dynamic_outputs": False,
        "inputs": [],
        "outputs": [],
    }
    http_mock.get(CATALOG_URL).respond(
        200, json={"result": [{"name": "Record", "triggers": {"Updated": definition}}]}
    )
    record = {"sys_id": REMOTE, "table": "incident", "condition": ""}
    if value != "absent":
        record["active"] = value
    http_mock.get(BASE + "/sys_flow_record_trigger").respond(
        200, json={"result": [record]}
    )
    row = {
        **copy.deepcopy(NATIVE["trigger"]),
        "trigger_type": "record_update",
        "remote_sys_id": REMOTE,
        "trigger_inputs": blob([]),
    }
    result = await read_flow(
        settings,
        "contract",
        [row],
        header={**NATIVE["flow"], "active": "false" if expected is True else "true"},
    )
    trigger = result["data"]["triggers"][0]
    assert trigger["active"] is expected
    assert trigger["active_source"] == (
        "record_trigger.active" if expected is not None else None
    )
    assert "activation" not in trigger
    assert trigger["metadata_complete"] is (expected is not None)


async def test_definition_activation_is_not_instance_or_parent_activation(settings):
    catalog = instance_catalog()
    catalog[0]["triggers"]["Service Catalog"]["active"] = True
    http_mock.get(CATALOG_URL).respond(200, json={"result": catalog})
    result = await read_flow(
        settings,
        "contract",
        [copy.deepcopy(NATIVE["trigger"])],
        header=copy.deepcopy(NATIVE["flow"]),
    )
    trigger = result["data"]["triggers"][0]
    assert trigger["active"] is None and trigger["activation"]["flow_active"] is False
    assert trigger["active_source"] is None
