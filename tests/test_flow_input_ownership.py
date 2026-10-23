"""Actual Table API storage names and strict native declaration ownership."""

import copy

import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.flows import semantics, triggers
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests._native_full_fixture import CAPTURE
from tests.test_flow_editing import BASE


def case():
    actual = copy.deepcopy(CAPTURE["flow"])
    expected = copy.deepcopy(actual)
    expected["inputs"] = []
    definition = next(
        d
        for c in CAPTURE["trigger_categories"]
        for d in c["triggers"].values()
        if d["type"] == "service_catalog"
    )
    manifest = {
        "definition_id": definition["id"],
        "declarations": triggers.service_catalog_inputs(definition),
    }
    return expected, actual, manifest, copy.deepcopy(CAPTURE["input_records"])


def install(actual, records):
    for original, record in zip(CAPTURE["input_records"], records, strict=True):
        http_mock.get(
            f"{BASE}/api/now/table/sys_hub_flow_input/{original['sys_id']}"
        ).respond(200, json={"result": record})
    http_mock.get(f"{BASE}/api/now/processflow/flow/{actual['id']}").respond(
        200, json={"result": {"data": actual}}
    )


async def test_actual_virtual_table_names_bind_both_declarations_by_element(settings):
    expected, actual, manifest, records = case()
    assert [r["name"] for r in actual["inputs"]] == ["table_name", "request_item"]
    assert [r["element"] for r in records] == ["table_name", "request_item"]
    assert {r["name"] for r in records} == {"var__m_sys_hub_flow_input_" + actual["id"]}
    assert all(
        r["sys_id"] == d["id"] and r["model"]["value"] == actual["id"]
        for d, r in zip(actual["inputs"], records, strict=True)
    )
    install(actual, records)
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        bound = await semantics._bind_trigger_inputs(client, expected, actual, manifest)
        assert bound["inputs"] == actual["inputs"]
        # All root/step/definition/cache/variable fields also compare strictly.
        verified = await semantics.verify_saved(
            client,
            expected,
            actual,
            generated_ids=set(),
            generated_trigger_inputs=manifest,
        )
    assert verified == actual
    assert expected["inputs"] == []
    owner_reads = [
        call.request
        for call in http_mock.calls
        if "/sys_hub_flow_input/" in call.request.url.path
    ]
    assert len(owner_reads) == 6
    assert all(
        set(r.url.params["sysparm_fields"].split(","))
        == {"sys_id", "model", "element", "sys_class_name"}
        for r in owner_reads
    )


@pytest.mark.parametrize(
    "defect",
    [
        "swapped_inputs",
        "duplicate_dto_name",
        "dto_order",
        "extra_input",
        "unknown_id",
        "duplicate_id",
        "wrong_model",
        "wrong_record_id",
        "wrong_class",
        "swapped_elements",
        "duplicate_element",
        "missing_element",
        "old_name_only",
        "extra_record",
        "dto_name",
        "dto_type",
        "dto_reference",
        "dto_label",
        "dto_value",
        "dto_default",
        "unknown_metadata",
    ],
)
async def test_actual_owner_mapping_and_entire_input_dtos_reject_drift(
    settings, defect
):
    expected, actual, manifest, records = case()
    if defect == "swapped_inputs":
        actual["inputs"].reverse()
    elif defect == "duplicate_dto_name":
        actual["inputs"][1]["name"] = actual["inputs"][0]["name"]
    elif defect == "dto_order":
        actual["inputs"][0]["order"] = 101
    elif defect == "extra_input":
        actual["inputs"].append(copy.deepcopy(actual["inputs"][0]))
    elif defect == "unknown_id":
        actual["inputs"][0]["id"] = "f" * 32
    elif defect == "duplicate_id":
        actual["inputs"][1]["id"] = actual["inputs"][0]["id"]
    elif defect == "wrong_model":
        records[0]["model"]["value"] = "f" * 32
    elif defect == "wrong_record_id":
        records[0]["sys_id"] = "f" * 32
    elif defect == "wrong_class":
        records[0]["sys_class_name"] = "sys_hub_flow_output"
    elif defect == "swapped_elements":
        records[0]["element"], records[1]["element"] = (
            records[1]["element"],
            records[0]["element"],
        )
    elif defect == "duplicate_element":
        records[1]["element"] = records[0]["element"]
    elif defect == "missing_element":
        records[0].pop("element")
    elif defect == "old_name_only":
        records[0].pop("element")
        records[0]["name"] = "table_name"
    elif defect == "extra_record":
        records[0] = [records[0], copy.deepcopy(records[1])]
    elif defect == "dto_name":
        actual["inputs"][0]["name"] = "wrong_technical_name"
    elif defect == "dto_type":
        actual["inputs"][1]["type"] = "string"
    elif defect == "dto_reference":
        actual["inputs"][1]["reference"] = "sys_user"
    elif defect == "dto_label":
        actual["inputs"][0]["label"] = "Changed label"
    elif defect == "dto_value":
        actual["inputs"][0]["value"] = "Changed value"
    elif defect == "dto_default":
        actual["inputs"][0]["defaultValue"] = "wrong_table"
    else:
        actual["inputs"][0]["attributes"]["unknown"] = None
    install(actual, records)
    if defect == "unknown_id":
        http_mock.get(f"{BASE}/api/now/table/sys_hub_flow_input/{'f' * 32}").respond(
            404, json={"error": "Unknown synthetic record"}
        )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(semantics.CapabilityError) as failure:
            await semantics.verify_saved(
                client,
                expected,
                actual,
                generated_ids=set(),
                generated_trigger_inputs=manifest,
            )
    assert failure.value.code == "UNKNOWN_OUTCOME"
