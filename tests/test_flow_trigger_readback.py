"""Offline Table API regressions for persisted V2 trigger readback."""

import base64
import copy
import gzip
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from mcp.server import MCPServer

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.mcp.tools.flow import register_tools
from tests._mock_transport import http_mock
from tests.helpers import decode_response, get_tool_functions

if TYPE_CHECKING:
    from servicenow_mcp.config import Settings


BASE = "https://test.service-now.com/api/now/table"
FLOW = "a" * 32
TRIGGER = "b" * 32
REMOTE = "c" * 32
INPUTS = [{"name": "run_flow_in", "value": "background"}]
CATALOG_URL = "https://test.service-now.com/api/now/trigger_api/trigger_categories"
CATALOG = json.loads(
    (Path(__file__).parent / "fixtures/flow_studio_definitions.json").read_text()
)["trigger_categories"]


@pytest.fixture(autouse=True)
def empty_trigger_catalog():
    http_mock.get(CATALOG_URL).respond(200, json={"result": []})


def blob(value: Any) -> str:
    return base64.b64encode(gzip.compress(json.dumps(value).encode(), mtime=0)).decode()


def service_catalog_row() -> dict[str, Any]:
    """Sanitize the reported live row; retain its column names and empty remote ID."""
    return {
        "sys_id": TRIGGER,
        "flow": FLOW,
        "trigger_type": {
            "value": "service_catalog",
            "display_value": "Service Catalog",
        },
        "trigger_definition": "d" * 32,
        "name": "Service Catalog",
        "remote_sys_id": "",
        "trigger_inputs": blob(INPUTS),
        "trigger_outputs": "",
    }


async def read_flow(
    settings: Settings,
    action: str,
    rows: list[dict[str, Any]],
    *,
    limit: int = 20,
    status: int = 200,
    sections: list[str] | None = None,
    header: dict[str, Any] | None = None,
    v1_rows: list[dict[str, Any]] | None = None,
):
    http_mock.get(f"{BASE}/sys_hub_flow/{FLOW}").respond(
        200,
        json={
            "result": header
            if header is not None
            else {"sys_id": FLOW, "type": "flow", "active": "true"}
        },
    )
    for table in (
        "sys_hub_action_instance_v2",
        "sys_hub_action_instance",
        "sys_hub_flow_logic_instance_v2",
        "sys_hub_flow_logic",
        "sys_hub_sub_flow_instance_v2",
        "sys_hub_trigger_instance",
    ):
        http_mock.get(f"{BASE}/{table}").respond(
            200,
            json={
                "result": v1_rows
                if table == "sys_hub_trigger_instance" and v1_rows is not None
                else []
            },
        )
    route = http_mock.get(f"{BASE}/sys_hub_trigger_instance_v2").respond(
        status, json={"result": rows}
    )
    mcp = MCPServer("test")
    register_tools(mcp, settings, OAuthPKCEProvider(settings))
    result = decode_response(
        await get_tool_functions(mcp)["flow"](
            action=action,
            sys_id=FLOW,
            sections=sections
            if sections is not None
            else ["triggers", "structural_summary", "warnings"],
            section_limit=limit,
        ),
    )
    assert result["status"] == "success", result
    assert route.calls.last.request.url.params["sysparm_limit"] == str(limit + 1)
    return result


@pytest.mark.parametrize("action", ["inspect", "contract"])
@pytest.mark.parametrize("remote", ["empty", "absent", "present"])
async def test_live_service_catalog_shape(settings: Settings, action: str, remote: str):
    row = service_catalog_row()
    if remote == "absent":
        row.pop("remote_sys_id")
    elif remote == "present":
        row["remote_sys_id"] = REMOTE
    result = await read_flow(settings, action, [row])
    trigger = result["data"]["triggers"][0]
    assert trigger["type"] == "service_catalog"
    assert trigger["active"] is None
    assert trigger["table"] == ""
    assert trigger["condition"] == ""
    assert "decode_error" not in trigger
    assert trigger["metadata_complete"] is False
    if action == "inspect":
        assert trigger["values_decoded"] == INPUTS
        assert trigger["remote_trigger_id"] == row.get("remote_sys_id", "")
        assert trigger["outputs_decoded"] is None
    else:
        assert trigger["configuration"][0]["value"] == "background"
    summary = result["data"]["structural_summary"]
    assert summary["counts"]["triggers_v2"] == 1
    assert summary["complete"] is True
    assert summary["completeness_scope"] == "structural_counts_only"
    assert summary["metadata_complete"] is False
    assert summary["metadata_sections"] == ["triggers"]
    assert "triggers" in result["truncation"]
    warnings = result["data"]["warnings"]
    assert trigger["activation"]["mode"] == "flow_managed"
    assert trigger["activation"]["flow_active"] is True
    assert any("definition" in warning for warning in warnings)
    assert not any("active" in warning for warning in warnings)
    assert not any(
        "table" in warning.lower() or "condition" in warning.lower()
        for warning in warnings
    )
    assert all(
        "sys_flow_record_trigger" not in str(call.request.url)
        for call in http_mock.calls
    )


@pytest.mark.parametrize("action", ["inspect", "contract"])
@pytest.mark.parametrize("column", ["trigger_inputs", "trigger_outputs"])
@pytest.mark.parametrize("value", ["H4sIAnope!", "bm90IGd6aXA=", blob([{}])[:-8]])
async def test_malformed_trigger_blob(
    settings: Settings, action: str, column: str, value: str
):
    row = {**service_catalog_row(), column: value, "active": "true"}
    result = await read_flow(settings, action, [row])
    trigger = result["data"]["triggers"][0]
    assert trigger["type"] == "service_catalog"
    assert column in trigger["decode_error"]
    assert value not in str(result)
    assert trigger["metadata_complete"] is False
    assert result["data"]["structural_summary"]["complete"] is True
    assert result["data"]["structural_summary"]["metadata_complete"] is False


@pytest.mark.parametrize("action", ["inspect", "contract"])
@pytest.mark.parametrize(
    "missing",
    ["trigger_inputs", "trigger_outputs", "trigger_type", "trigger_definition"],
)
async def test_missing_trigger_metadata(settings: Settings, action: str, missing: str):
    row = service_catalog_row()
    row.pop(missing)
    result = await read_flow(settings, action, [row])
    assert result["data"]["triggers"][0]["metadata_complete"] is False
    assert any(missing in warning for warning in result["data"]["warnings"])
    assert "triggers" in result["truncation"]


@pytest.mark.parametrize("action", ["inspect", "contract"])
@pytest.mark.parametrize("value", [None, {}, {"value": None, "display_value": ""}])
async def test_unreadable_blob_field_is_not_a_stored_empty_value(
    settings: Settings, action: str, value: Any
):
    result = await read_flow(
        settings, action, [{**service_catalog_row(), "trigger_inputs": value}]
    )
    assert any("trigger_inputs" in warning for warning in result["data"]["warnings"])
    assert result["data"]["triggers"][0]["metadata_complete"] is False


@pytest.mark.parametrize("action", ["inspect", "contract"])
@pytest.mark.parametrize("value", ["unexpected", "Service Catalog", ""])
async def test_unknown_trigger_type_is_not_reinterpreted(
    settings: Settings, action: str, value: str
):
    row = {**service_catalog_row(), "trigger_type": value}
    result = await read_flow(settings, action, [row])
    assert result["data"]["triggers"][0]["type"] == value
    assert any("type" in warning for warning in result["data"]["warnings"])
    assert all(
        "sys_flow_record_trigger" not in str(call.request.url)
        for call in http_mock.calls
    )


@pytest.mark.parametrize("action", ["inspect", "contract"])
async def test_outputs_retain_only_stored_reference_metadata(
    settings: Settings, action: str
):
    outputs = [
        {
            "name": "request_item",
            "value": "",
            "parameter": {"type": "reference", "reference": "sc_req_item"},
        },
    ]
    row = {**service_catalog_row(), "trigger_outputs": blob(outputs), "active": "false"}
    result = await read_flow(settings, action, [row])
    trigger = result["data"]["triggers"][0]
    assert trigger["active"] is False
    assert trigger["table"] == ""  # An output reference is not a record-trigger table.
    if action == "inspect":
        assert trigger["outputs_decoded"] == outputs
    else:
        assert trigger["output_configuration"][0]["reference_table"] == "sc_req_item"


@pytest.mark.parametrize("action", ["inspect", "contract"])
@pytest.mark.parametrize("active", ["true", "false", "not-a-boolean"])
async def test_trigger_active_uses_source_not_flow_header(
    settings: Settings, action: str, active: str
):
    result = await read_flow(
        settings, action, [{**service_catalog_row(), "active": active}]
    )
    trigger = result["data"]["triggers"][0]
    assert trigger["active"] is {"true": True, "false": False}.get(active)
    assert trigger["active_source"] == (
        "trigger.active" if active != "not-a-boolean" else None
    )


@pytest.mark.parametrize("action", ["inspect", "contract"])
async def test_invalid_stored_choice_is_reported(settings: Settings, action: str):
    inputs = [
        {
            "name": "run_flow_in",
            "value": "invalid",
            "parameter": {"choices": [{"value": "background"}]},
        },
    ]
    result = await read_flow(
        settings, action, [{**service_catalog_row(), "trigger_inputs": blob(inputs)}]
    )
    assert any("choice" in warning for warning in result["data"]["warnings"])
    assert result["data"]["triggers"][0]["metadata_complete"] is False


@pytest.mark.parametrize("action", ["inspect", "contract"])
@pytest.mark.parametrize("modern", [True, False])
async def test_record_trigger_join_preserves_legacy_and_v2_fields(
    settings: Settings, action: str, modern: bool
):
    row = {
        "sys_id": TRIGGER,
        "trigger_type" if modern else "type": "record_update",
        "remote_sys_id" if modern else "remote_trigger_id": REMOTE,
        "trigger_inputs" if modern else "values": blob(INPUTS),
    }
    route = http_mock.get(f"{BASE}/sys_flow_record_trigger").respond(
        200,
        json={
            "result": [
                {
                    "sys_id": REMOTE,
                    "table": "incident",
                    "condition": "active=true",
                    "active": "true",
                },
            ],
        },
    )
    result = await read_flow(settings, action, [row])
    trigger = result["data"]["triggers"][0]
    assert trigger["type"] == "record_update"
    assert trigger["table"] == "incident"
    assert trigger["condition"] == "active=true"
    assert trigger["active"] is True
    assert trigger["active_source"] == "record_trigger.active"
    assert route.call_count == 1


@pytest.mark.parametrize("action", ["inspect", "contract"])
@pytest.mark.parametrize("status", [200, 403])
async def test_missing_record_join_is_partial_not_inactive(
    settings: Settings, action: str, status: int
):
    http_mock.get(f"{BASE}/sys_flow_record_trigger").respond(
        status, json={"result": []}
    )
    row = {"sys_id": TRIGGER, "trigger_type": "record_update", "remote_sys_id": REMOTE}
    result = await read_flow(settings, action, [row])
    assert result["data"]["triggers"][0]["active"] is None
    assert result["data"]["structural_summary"]["complete"] is True
    assert result["data"]["structural_summary"]["metadata_complete"] is False
    assert any("record" in warning.lower() for warning in result["data"]["warnings"])


@pytest.mark.parametrize("action", ["inspect", "contract"])
@pytest.mark.parametrize(
    "trigger_type", ["scheduled", "daily", "inbound_email", "service_catalog"]
)
@pytest.mark.parametrize("column", ["type", "trigger_type"])
async def test_nonrecord_legacy_configuration_is_preserved(
    settings: Settings,
    action: str,
    trigger_type: str,
    column: str,
):
    row = {
        "sys_id": TRIGGER,
        column: trigger_type,
        "active": "true",
        "values": blob(INPUTS),
    }
    result = await read_flow(settings, action, [row])
    trigger = result["data"]["triggers"][0]
    assert trigger["type"] == trigger_type
    assert trigger["active"] is True
    if action == "inspect":
        assert trigger["values_decoded"] == INPUTS
    else:
        assert trigger["configuration"][0]["value"] == "background"
    assert all(
        "sys_flow_record_trigger" not in str(call.request.url)
        for call in http_mock.calls
    )


@pytest.mark.parametrize("action", ["inspect", "contract"])
async def test_legacy_v1_trigger_is_unchanged(settings: Settings, action: str):
    row = {
        "sys_id": TRIGGER,
        "type": "record_update",
        "active": "false",
        "table": "incident",
        "condition": "active=true",
    }
    http_mock.get(f"{BASE}/sys_hub_flow/{FLOW}").respond(
        200, json={"result": {"sys_id": FLOW}}
    )
    http_mock.get(f"{BASE}/sys_hub_trigger_instance_v2").respond(
        200, json={"result": []}
    )
    http_mock.get(f"{BASE}/sys_hub_trigger_instance").respond(
        200, json={"result": [row]}
    )
    mcp = MCPServer("test")
    register_tools(mcp, settings, OAuthPKCEProvider(settings))
    result = decode_response(
        await get_tool_functions(mcp)["flow"](
            action=action, sys_id=FLOW, sections=["triggers"]
        )
    )
    expected = {
        "version": "v1",
        "type": "record_update",
        "active": False,
        "table": "incident",
        "condition": "active=true",
    }
    if action == "inspect":
        expected["sys_id"] = TRIGGER
    assert result["status"] == "success", result
    assert result["data"]["triggers"] == [expected]
    assert "truncation" not in result


@pytest.mark.parametrize("action", ["inspect", "contract"])
async def test_unloaded_definitions_never_establish_full_metadata(
    settings: Settings, action: str
):
    row = {**service_catalog_row(), "active": "true"}
    result = await read_flow(settings, action, [row])
    assert result["data"]["structural_summary"]["complete"] is True
    assert result["data"]["structural_summary"]["metadata_complete"] is False
    assert any("definition" in warning for warning in result["data"]["warnings"])


@pytest.mark.parametrize("action", ["inspect", "contract"])
async def test_capped_triggers_do_not_claim_metadata_completeness(
    settings: Settings, action: str
):
    result = await read_flow(
        settings, action, [service_catalog_row(), service_catalog_row()], limit=1
    )
    assert len(result["data"]["triggers"]) == 1
    assert result["data"]["structural_summary"]["complete"] is False
    assert result["data"]["structural_summary"]["metadata_complete"] is False
    assert result["truncation"]["triggers"]["omitted_at_least"] == 1


@pytest.mark.parametrize("action", ["inspect", "contract"])
async def test_unreadable_trigger_dataset_has_unknown_counts(
    settings: Settings, action: str
):
    result = await read_flow(settings, action, [], status=403)
    assert result["data"]["triggers"] == []
    summary = result["data"]["structural_summary"]
    assert summary["counts"]["triggers_v2"] is None
    assert summary["trigger_count"] is None
    assert summary["complete"] is False
    assert summary["metadata_complete"] is False
    assert result["truncation"]["triggers"]["incomplete_datasets"] == ["triggers_v2"]
    assert result["data"]["warnings"]


def instance_catalog() -> list[dict[str, Any]]:
    catalog = copy.deepcopy(CATALOG)
    catalog[0]["triggers"]["Service Catalog"]["id"] = "d" * 32
    return catalog


@pytest.mark.parametrize("action", ["inspect", "contract"])
async def test_live_empty_choice_uses_loaded_definition_without_resolving_default(
    settings: Settings, action: str
):
    catalog = instance_catalog()
    spec = catalog[0]["triggers"]["Service Catalog"]["inputs"][0]
    inputs = [
        {
            "name": "run_flow_in",
            "value": "",
            "displayValue": "",
            "parameter": spec["parameter"],
        },
    ]
    route = http_mock.get(CATALOG_URL).respond(200, json={"result": catalog})
    result = await read_flow(
        settings, action, [{**service_catalog_row(), "trigger_inputs": blob(inputs)}]
    )
    trigger = result["data"]["triggers"][0]
    definition = trigger["definition"]
    assert route.call_count == 1
    assert definition["metadata_complete"] is True
    assert definition["inputs"][0]["default"] == ""
    assert definition["inputs"][0]["required"] is False
    assert definition["outputs"][0]["reference_table"] == "sc_req_item"
    assert definition["outputs"][0]["reference_display"] == "Requested Item"
    assert trigger["active"] is None
    assert trigger["warnings"] == []
    assert trigger["activation"]["instance_active_applicable"] is False
    assert trigger["activation"]["flow_active"] is True
    assert trigger["metadata_complete"] is True
    assert "truncation" not in result
    if action == "inspect":
        assert trigger["values_decoded"] == inputs
        assert trigger["outputs_decoded"] is None
    else:
        assert trigger["configuration"][0]["value"] == ""
        assert "output_configuration" not in trigger


@pytest.mark.parametrize("action", ["inspect", "contract"])
@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize(
    "value", ["", None, "background", "foreground", "invalid", "omitted"]
)
async def test_definition_choice_constraints(
    settings: Settings, action: str, required: bool, value: Any
):
    catalog = instance_catalog()
    spec = catalog[0]["triggers"]["Service Catalog"]["inputs"][0]
    spec["mandatory"] = spec["parameter"]["mandatory"] = required
    http_mock.get(CATALOG_URL).respond(200, json={"result": catalog})
    inputs = [] if value == "omitted" else [{"name": "run_flow_in", "value": value}]
    result = await read_flow(
        settings,
        action,
        [{**service_catalog_row(), "trigger_inputs": blob(inputs), "active": "true"}],
    )
    trigger = result["data"]["triggers"][0]
    invalid = (
        value is None or value == "invalid" or (required and value in ("", "omitted"))
    )
    assert trigger["metadata_complete"] is (not invalid)
    assert bool(trigger["warnings"]) is invalid
    if invalid:
        assert result["data"]["structural_summary"]["metadata_complete"] is False
    else:
        assert "truncation" not in result


@pytest.mark.parametrize("action", ["inspect", "contract"])
@pytest.mark.parametrize("status", [403, 404, 202])
async def test_definition_access_failure_preserves_config(
    settings: Settings, action: str, status: int
):
    route = http_mock.get(CATALOG_URL).respond(status, json={"result": []})
    result = await read_flow(settings, action, [service_catalog_row()])
    definition = result["data"]["triggers"][0]["definition"]
    assert definition["metadata_complete"] is False
    assert definition["inputs"] is None
    assert definition["outputs"] is None
    assert definition["read_status"] == f"http_{status}"
    assert result["data"]["structural_summary"]["complete"] is True
    assert result["data"]["structural_summary"]["metadata_complete"] is False
    assert route.call_count == 1


@pytest.mark.parametrize("action", ["inspect", "contract"])
async def test_definition_timeout_does_not_leak_error_or_retry(
    settings: Settings, action: str
):
    route = http_mock.get(CATALOG_URL).mock(
        side_effect=httpx2.ReadTimeout("private-marker")
    )
    result = await read_flow(settings, action, [service_catalog_row()])
    assert route.call_count == 1
    assert "private-marker" not in str(result)
    assert result["data"]["triggers"][0]["definition"]["read_status"] == "unavailable"


@pytest.mark.parametrize("action", ["inspect", "contract"])
@pytest.mark.parametrize(
    "defect",
    [
        "inputs",
        "outputs",
        "mandatory",
        "choices",
        "default",
        "reference",
        "dynamic",
        "duplicate",
        "category",
        "type",
    ],
)
async def test_definition_partial_metadata_is_not_complete(
    settings: Settings, action: str, defect: str
):
    catalog = instance_catalog()
    definition = catalog[0]["triggers"]["Service Catalog"]
    if defect in {"inputs", "outputs"}:
        definition.pop(defect)
    elif defect == "mandatory":
        definition["inputs"][0].pop("mandatory")
        definition["inputs"][0]["parameter"].pop("mandatory")
    elif defect == "choices":
        definition["inputs"][0]["parameter"]["choices"] = None
    elif defect == "default":
        definition["inputs"][0]["default_value"] = None
        definition["inputs"][0]["parameter"]["defaultValue"] = None
    elif defect == "reference":
        definition["outputs"][0]["parameter"].pop("reference")
    elif defect == "dynamic":
        definition["has_dynamic_outputs"] = True
    elif defect == "duplicate":
        definition["inputs"].append(copy.deepcopy(definition["inputs"][0]))
    elif defect == "category":
        catalog[0].pop("name")
    else:
        definition["type"] = "unexpected"
    http_mock.get(CATALOG_URL).respond(200, json={"result": catalog})
    result = await read_flow(
        settings, action, [{**service_catalog_row(), "active": "true"}]
    )
    trigger = result["data"]["triggers"][0]
    assert trigger["metadata_complete"] is False
    assert trigger["warnings"]
    assert result["data"]["structural_summary"]["metadata_complete"] is False


@pytest.mark.parametrize("action", ["inspect", "contract"])
async def test_shared_catalog_read_is_independent_of_trigger_count(
    settings: Settings, action: str
):
    catalog = instance_catalog()
    scheduled = copy.deepcopy(catalog[0]["triggers"]["Service Catalog"])
    scheduled.update(id="e" * 32, type="scheduled")
    catalog.append({"name": "Scheduled", "triggers": {"Scheduled": scheduled}})
    route = http_mock.get(CATALOG_URL).respond(200, json={"result": catalog})
    rows = [{**service_catalog_row(), "sys_id": f"{index:032x}"} for index in range(8)]
    rows[-1].update(trigger_definition="e" * 32, trigger_type="scheduled")
    result = await read_flow(settings, action, rows)
    assert route.call_count == 1
    assert len(result["data"]["triggers"]) == 8
    assert all(
        trigger["definition"]["metadata_complete"]
        for trigger in result["data"]["triggers"]
    )


@pytest.mark.parametrize("action", ["inspect", "contract"])
async def test_definition_identity_cap_is_disclosed(
    settings: Settings, action: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        "servicenow_mcp.capabilities.flows.flow.INTERNAL_QUERY_LIMIT", 2
    )
    catalog = instance_catalog()[:1]
    catalog[0]["triggers"]["Service Catalog"].update(inputs=[], outputs=[])
    route = http_mock.get(CATALOG_URL).respond(200, json={"result": catalog})
    rows = [
        {**service_catalog_row(), "trigger_definition": identity * 32}
        for identity in ("d", "e", "f")
    ]
    result = await read_flow(settings, action, rows)
    definitions = [trigger["definition"] for trigger in result["data"]["triggers"]]
    assert definitions[0]["metadata_complete"] is True
    assert definitions[-1]["metadata_complete"] is False
    assert any("identity limit" in warning for warning in definitions[-1]["warnings"])
    assert result["data"]["structural_summary"]["complete"] is True
    assert route.call_count == 1


@pytest.mark.parametrize("action", ["inspect", "contract"])
async def test_definition_field_cap_keeps_partial_schema_and_counts(
    settings: Settings, action: str
):
    route = http_mock.get(CATALOG_URL).respond(200, json={"result": instance_catalog()})
    result = await read_flow(settings, action, [service_catalog_row()], limit=1)
    definition = result["data"]["triggers"][0]["definition"]
    assert len(definition["outputs"]) == 1
    assert definition["truncation"]["outputs"] == {
        "returned": 1,
        "observed": 4,
        "limit": 1,
    }
    assert definition["metadata_complete"] is False
    assert result["data"]["structural_summary"]["complete"] is True
    assert route.call_count == 1


@pytest.mark.parametrize("action", ["inspect", "contract"])
async def test_omitted_trigger_section_does_not_read_catalog(
    settings: Settings, action: str
):
    route = http_mock.get(CATALOG_URL).respond(200, json={"result": instance_catalog()})
    await read_flow(
        settings,
        action,
        [service_catalog_row()],
        sections=["structural_summary", "warnings"],
    )
    assert route.call_count == 0


@pytest.mark.parametrize("action", ["inspect", "contract"])
@pytest.mark.parametrize(
    ("trigger_type", "category"),
    [
        ("record_create", "Record"),
        ("scheduled", "Scheduled"),
        ("custom_app", "Application"),
    ],
)
async def test_definition_lookup_uses_identity_across_families(
    settings: Settings,
    action: str,
    trigger_type: str,
    category: str,
):
    definition = {
        "id": "d" * 32,
        "type": trigger_type,
        "label": "Sanitized trigger",
        "has_dynamic_outputs": False,
        "inputs": [],
        "outputs": [],
    }
    route = http_mock.get(CATALOG_URL).respond(
        200,
        json={"result": [{"name": category, "triggers": {"trigger": definition}}]},
    )
    row = {
        **service_catalog_row(),
        "trigger_type": trigger_type,
        "trigger_inputs": blob([]),
        "active": "true",
    }
    result = await read_flow(settings, action, [row])
    trigger = result["data"]["triggers"][0]
    assert trigger["definition"]["metadata_complete"] is True
    assert trigger["definition"]["type"] == trigger_type
    assert trigger["type"] == trigger_type
    assert route.call_count == 1
    if trigger_type != "record_create":
        assert trigger["warnings"] == []


@pytest.mark.parametrize("action", ["inspect", "contract"])
@pytest.mark.parametrize(
    "defect",
    ["missing_id", "invalid_id", "not_list", "category", "duplicate_id", "catalog_cap"],
)
async def test_definition_join_does_not_guess_from_type(
    settings: Settings, action: str, defect: str
):
    catalog = instance_catalog()
    row = service_catalog_row()
    if defect == "missing_id":
        row["trigger_definition"] = "e" * 32
    elif defect == "invalid_id":
        row["trigger_definition"] = "invalid"
    elif defect == "not_list":
        catalog = {"triggers": {}}
    elif defect == "category":
        catalog[0]["triggers"] = []
    elif defect == "duplicate_id":
        catalog.append(copy.deepcopy(catalog[0]))
    elif defect == "catalog_cap":
        catalog[1]["triggers"] = {str(index): {} for index in range(1001)}
    route = http_mock.get(CATALOG_URL).respond(200, json={"result": catalog})
    result = await read_flow(settings, action, [row])
    definition = result["data"]["triggers"][0]["definition"]
    assert definition["metadata_complete"] is False
    assert definition["inputs"] is None
    assert definition["outputs"] is None
    assert result["data"]["structural_summary"]["complete"] is True
    assert result["data"]["structural_summary"]["metadata_complete"] is False
    assert route.call_count == (0 if defect == "invalid_id" else 1)


@pytest.mark.parametrize("action", ["inspect", "contract"])
async def test_definition_lookup_excludes_omitted_trigger_rows(
    settings: Settings, action: str
):
    catalog = instance_catalog()
    catalog[0]["triggers"]["Service Catalog"].update(inputs=[], outputs=[])
    route = http_mock.get(CATALOG_URL).respond(200, json={"result": catalog})
    rows = [
        service_catalog_row(),
        {**service_catalog_row(), "trigger_definition": "e" * 32},
    ]
    result = await read_flow(settings, action, rows, limit=1)
    assert result["data"]["triggers"][0]["definition"]["metadata_complete"] is True
    assert result["truncation"]["triggers"]["omitted_at_least"] == 1
    assert route.call_count == 1


@pytest.mark.parametrize("action", ["inspect", "contract"])
@pytest.mark.parametrize(
    "defect",
    [
        "stale_choices",
        "missing_value",
        "unknown_field",
        "duplicate_field",
        "partial_outputs",
    ],
)
async def test_loaded_definition_is_authoritative_for_input_checks(
    settings: Settings, action: str, defect: str
):
    catalog = instance_catalog()
    if defect == "partial_outputs":
        catalog[0]["triggers"]["Service Catalog"].pop("outputs")
    http_mock.get(CATALOG_URL).respond(200, json={"result": catalog})
    inputs = [{"name": "run_flow_in", "value": "foreground"}]
    if defect in {"stale_choices", "partial_outputs"}:
        inputs[0]["parameter"] = {
            "type": "choice",
            "mandatory": True,
            "choices": [{"value": "background"}],
        }
    elif defect == "missing_value":
        inputs[0].pop("value")
    elif defect == "unknown_field":
        inputs[0]["name"] = "unknown"
    else:
        inputs.append(copy.deepcopy(inputs[0]))
    result = await read_flow(
        settings,
        action,
        [{**service_catalog_row(), "trigger_inputs": blob(inputs), "active": "true"}],
    )
    trigger = result["data"]["triggers"][0]
    assert trigger["definition"]["metadata_complete"] is (defect != "partial_outputs")
    assert trigger["metadata_complete"] is (defect == "stale_choices")
    if defect == "partial_outputs":
        assert not any("choice" in warning for warning in trigger["warnings"])


@pytest.mark.parametrize("action", ["inspect", "contract"])
async def test_partial_input_schema_does_not_declare_omitted_fields_invalid(
    settings: Settings, action: str
):
    catalog = instance_catalog()
    inputs = catalog[0]["triggers"]["Service Catalog"]["inputs"]
    inputs.append({**copy.deepcopy(inputs[0]), "name": "other"})
    http_mock.get(CATALOG_URL).respond(200, json={"result": catalog})
    row = {
        **service_catalog_row(),
        "trigger_inputs": blob([*INPUTS, {"name": "other", "value": "background"}]),
    }
    result = await read_flow(settings, action, [row], limit=1)
    trigger = result["data"]["triggers"][0]
    assert trigger["definition"]["truncation"]["inputs"]["returned"] == 1
    assert trigger["metadata_complete"] is False
    assert not any("absent from" in warning for warning in trigger["warnings"])


@pytest.mark.parametrize("action", ["inspect", "contract"])
async def test_unreadable_input_blob_does_not_establish_missing_required_value(
    settings: Settings, action: str
):
    catalog = instance_catalog()
    catalog[0]["triggers"]["Service Catalog"]["inputs"][0]["mandatory"] = True
    http_mock.get(CATALOG_URL).respond(200, json={"result": catalog})
    result = await read_flow(
        settings, action, [{**service_catalog_row(), "trigger_inputs": "H4sIAnope!"}]
    )
    trigger = result["data"]["triggers"][0]
    assert "decode_error" in trigger
    assert not any("omit a required" in warning for warning in trigger["warnings"])
