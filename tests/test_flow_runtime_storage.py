"""Native storage preservation, independent of editor getter projections."""

import base64
import copy
import gzip
import json
import zlib
from typing import Any

import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import active_drafts, compile_verification
from servicenow_mcp.platform.client import ServiceNowClient
from tests._compiled_snapshot_fixture import snapshot_storage
from tests._mock_transport import http_mock
from tests.test_flow_active_drafts import SELECTED, ActiveModel
from tests.test_flow_editing import BASE, FLOW

VERSION = "b1" * 16
INSTANCE = "b2" * 16
BLOCK = "b3" * 16
TIME = "2026-10-23 10:00:00"


def serialized_flow() -> dict[str, Any]:
    return {
        "id": SELECTED,
        "parentFlow": FLOW,
        "type": "subflow",
        "scope": "global",
        "version": "2",
        "authoredOnReleaseVersion": 27800,
        "flowEngineVersion": 2,
        "snapshot": True,
        "jsonSnapshot": True,
        "actionInstances": [
            {
                "id": INSTANCE,
                "flowSysId": VERSION,
                "order": "1",
                "inputs": [
                    {
                        "name": "message",
                        "value": "Synthetic log",
                        "parameter": {"type": "string"},
                    }
                ],
                "unknownExecutableSetting": {"ordered": [False, 0, "0"]},
            }
        ],
        "unknownRootSetting": {"nested": ["a", "b"]},
    }


def storage_routes(record, chunks):
    http_mock.get(f"{BASE}/api/now/table/sys_hub_snapshot/{SELECTED}").respond(
        200, json={"result": record}
    )
    http_mock.get(f"{BASE}/api/now/table/sys_hub_snapshot_chunk").respond(
        200, json={"result": chunks}
    )


async def read_storage(settings):
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        return await compile_verification.serialized_snapshot(SELECTED, FLOW, client)


def encode_document(chunks, document):
    encoded = base64.b64encode(
        gzip.compress(json.dumps(json.dumps(document)).encode(), mtime=0)
    ).decode()
    chunks[0].update(data=encoded, length=str(len(encoded)))


async def test_native_storage_retains_full_unknown_schema_order_and_values(settings):
    flow = serialized_flow()
    record, chunks = snapshot_storage(flow)
    storage_routes(record, chunks)
    document, raw_record, raw_chunks = await read_storage(settings)
    assert flow["flowEngineVersion"] == 2
    assert document["engineVersion"] == 0
    assert "flowEngineVersion" not in document
    assert document["isSnapshot"] is True and document["isJsonSnapshot"] is True
    assert document["unknownRootSetting"] == {"nested": ["a", "b"]}
    assert document["actionInstances"][0]["unknownExecutableSetting"] == {
        "ordered": [False, 0, "0"]
    }
    assert document["actionInstances"][0]["inputs"][0]["parameter"] == {
        "type": "string"
    }
    assert raw_record == record and raw_chunks == chunks
    assert all(call.request.method == "GET" for call in http_mock.calls)
    for call in http_mock.calls:
        assert call.request.url.params["sysparm_display_value"] == "false"
    assert (
        http_mock.calls[-1].request.url.params["sysparm_query"]
        == f"document_id={SELECTED}^ORDERBYposition"
    )


async def test_chunk_positions_are_bound_before_assembling_a_complete_document(
    settings,
):
    record, chunks = snapshot_storage(serialized_flow())
    encoded = chunks[0]["data"]
    middle = len(encoded) // 2
    chunks = [
        {**chunks[0], "data": encoded[:middle], "length": str(middle)},
        {
            **chunks[0],
            "sys_id": "fb" * 16,
            "position": "1",
            "data": encoded[middle:],
            "length": str(len(encoded) - middle),
        },
    ]
    storage_routes(record, list(reversed(chunks)))
    document, _, evidence = await read_storage(settings)
    assert document["version"] == "2"
    assert evidence == list(reversed(chunks))


@pytest.mark.parametrize(
    "defect",
    [
        "owner",
        "identity",
        "revision",
        "pointer",
        "missing_data",
        "length",
        "position",
        "duplicate",
        "row_bound",
        "encoded_bound",
        "truncated",
        "trailing",
        "double_member",
        "depth",
        "nodes",
        "duplicate_key",
        "nan",
        "wrong_version_profile",
        "wrong_payload_owner",
        "raw_engine_dto_value",
        "raw_engine_boolean",
        "raw_snapshot_flag",
        "raw_json_flag",
    ],
)
async def test_incomplete_or_unrecognized_storage_never_establishes_preservation(
    settings, monkeypatch, defect
):
    record, chunks = snapshot_storage(serialized_flow())
    document = json.loads(
        json.loads(gzip.decompress(base64.b64decode(chunks[0]["data"])))
    )
    if defect == "owner":
        record["parent"] = VERSION
    elif defect == "identity":
        record["sys_id"] = VERSION
    elif defect == "revision":
        record.pop("sys_mod_count")
    elif defect == "pointer":
        record["payload"] = json.dumps(
            {
                "table": "sys_hub_flow_snapshot",
                "id": SELECTED,
                "name": "payload",
                "type": "java.lang.String",
            }
        )
    elif defect == "missing_data":
        chunks[0].pop("data")
    elif defect == "length":
        chunks[0]["length"] = "1"
    elif defect == "position":
        chunks[0]["position"] = "1"
    elif defect == "duplicate":
        chunks.append(copy.deepcopy(chunks[0]))
    elif defect == "row_bound":
        monkeypatch.setattr(compile_verification, "MAX_CHUNKS", 1)
    elif defect == "encoded_bound":
        monkeypatch.setattr(compile_verification, "MAX_ENCODED_BYTES", 10)
    elif defect in {"truncated", "trailing", "double_member"}:
        data = base64.b64decode(chunks[0]["data"])
        data = (
            data[:-4]
            if defect == "truncated"
            else data + (b"extra" if defect == "trailing" else gzip.compress(b"{}"))
        )
        chunks[0].update(
            data=base64.b64encode(data).decode(),
            length=str(len(base64.b64encode(data))),
        )
    elif defect == "depth":
        document["unknownRootSetting"] = [[[[[[[[0]]]]]]]]
        encode_document(chunks, document)
        monkeypatch.setattr(compile_verification, "MAX_DEPTH", 6)
    elif defect == "nodes":
        monkeypatch.setattr(compile_verification, "MAX_NODES", 5)
    elif defect in {"duplicate_key", "nan"}:
        text = (
            '{"id":"' + SELECTED + '","id":"' + SELECTED + '"}'
            if defect == "duplicate_key"
            else '{"value":NaN}'
        )
        encoded = base64.b64encode(
            gzip.compress(json.dumps(text).encode(), mtime=0)
        ).decode()
        chunks[0].update(data=encoded, length=str(len(encoded)))
    elif defect == "wrong_version_profile":
        document["engineVersion"] = True
        encode_document(chunks, document)
    elif defect == "raw_engine_dto_value":
        document["engineVersion"] = 2
        encode_document(chunks, document)
    elif defect == "raw_engine_boolean":
        document["engineVersion"] = False
        encode_document(chunks, document)
    elif defect == "raw_snapshot_flag":
        document["isSnapshot"] = False
        encode_document(chunks, document)
    elif defect == "raw_json_flag":
        document.pop("isJsonSnapshot")
        encode_document(chunks, document)
    elif defect == "wrong_payload_owner":
        document["parentFlow"] = VERSION
        encode_document(chunks, document)
    storage_routes(record, chunks)
    with pytest.raises((ValueError, zlib.error)):
        await read_storage(settings)
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("change", ["value", "schema", "order", "unknown"])
async def test_raw_runtime_drift_invisible_to_getter_is_detected_after_one_save(
    settings, monkeypatch, change
):
    model = ActiveModel(settings, monkeypatch)
    before = copy.deepcopy(model.snapshot)

    def drift():
        document = json.loads(
            json.loads(
                gzip.decompress(base64.b64decode(model.serialized_chunks[0]["data"]))
            )
        )
        if change == "value":
            document["actionInstances"][0]["inputs"][0]["value"] = (
                "Unexpected runtime value"
            )
        elif change == "schema":
            parameter = document["actionInstances"][0]["inputs"][0]["parameter"]
            parameter["mandatory"] = not parameter.get("mandatory", False)
        elif change == "order":
            document["actionInstances"][0]["order"] = "9"
        else:
            document["unknownExecutableSetting"] = {"new": False}
        encode_document(model.serialized_chunks, document)

    monkeypatch.setattr(model, "after_save", drift)
    result = await model.apply(await model.preview())
    assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
    assert result["error"]["outcome"]["runtime_preservation"] == "unknown"
    assert model.snapshot == before
    assert len(model.writes) == 1


async def test_raw_runtime_drift_before_apply_consumes_token_without_saving(
    settings, monkeypatch
):
    model = ActiveModel(settings, monkeypatch)
    preview = await model.preview()
    model.serialized_record["sys_mod_count"] = "2"
    result = await model.apply(preview)
    assert result["error"]["code"] == "STALE_PREVIEW", result
    assert not model.writes
    assert (await model.apply(preview))["status"] == "error"


def subflow_row() -> dict[str, Any]:
    return {
        "sys_id": INSTANCE,
        "flow": VERSION,
        "sys_mod_count": "0",
        "sys_updated_on": TIME,
        "order": "1",
        "parent_ui_id": "",
        "ui_id": "call-ui",
        "attributes": "",
        "comment": "",
        "display_text": "",
        "generation_source": "",
        "subflow": "c1" * 16,
        "subflow_inputs": "Synthetic opaque native binding bytes",
        "wait_for_completion": "false",
        "unknownNativeSetting": {"enabled": True},
    }


def native_routes(subflows=None, logic=None, block=None):
    for section, (table, _owner) in active_drafts.RUNTIME_TABLES.items():
        rows = []
        if section == "subFlowInstances":
            rows = subflows or []
        elif section == "flowLogicInstances":
            rows = logic or []
        http_mock.get(f"{BASE}/api/now/table/{table}").respond(
            200, json={"result": rows}
        )
    if block is not None:
        http_mock.get(f"{BASE}/api/now/table/sys_hub_flow_block/{BLOCK}").respond(
            200, json={"result": block}
        )


async def read_native(settings):
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        return await active_drafts.persisted_content(VERSION, client)


async def test_native_subflow_uses_its_real_input_and_wait_columns(settings):
    row = subflow_row()
    assert "values" not in row
    native_routes(subflows=[row])
    before = await read_native(settings)
    assert before["subFlowInstances"] == [row]
    changed = copy.deepcopy(row)
    changed["wait_for_completion"] = "true"
    changed["unknownNativeSetting"]["enabled"] = False
    native_routes(subflows=[changed])
    after = await read_native(settings)
    assert after != before
    assert after["subFlowInstances"][0]["subflow_inputs"] == row["subflow_inputs"]


@pytest.mark.parametrize(
    "field",
    ["subflow_inputs", "subflow", "wait_for_completion", "attributes", "comment"],
)
async def test_missing_native_executable_configuration_fails_closed(settings, field):
    row = subflow_row()
    row.pop(field)
    native_routes(subflows=[row])
    with pytest.raises(CapabilityError, match="missing configuration"):
        await read_native(settings)


async def test_duplicate_native_rows_cannot_prove_a_complete_saved_version(settings):
    row = subflow_row()
    native_routes(subflows=[row, copy.deepcopy(row)])
    with pytest.raises(CapabilityError, match="duplicate record identities"):
        await read_native(settings)


async def test_native_logic_retains_block_reference_and_unknown_binding_documents(
    settings,
):
    logic = {
        "sys_id": INSTANCE,
        "flow": VERSION,
        "sys_mod_count": "0",
        "sys_updated_on": TIME,
        "order": "1",
        "parent_ui_id": "",
        "ui_id": "assignment-ui",
        "attributes": "",
        "comment": "",
        "display_text": "",
        "generation_source": "",
        "values": "opaque",
        "logic_definition": "c2" * 16,
        "flow_variables_assigned": "",
        "outputs_assigned": "",
        "block": BLOCK,
        "connected_to": "",
        "workflow_reference": "",
        "decision_table": "",
        "unknownSchemaSetting": {"ordered": [1, 2]},
    }
    block = {
        "sys_id": BLOCK,
        "sys_mod_count": "1",
        "sys_updated_on": TIME,
        "sys_class_name": "sys_hub_flow_block",
        "name": "Assignment block",
        "label_cache": "",
    }
    native_routes(logic=[logic], block=block)
    before = await read_native(settings)
    assert before["flowLogicInstances"] == [logic]
    assert before["blocks"] == [block]
    changed = copy.deepcopy(block)
    changed["label_cache"] = "Changed native schema cache"
    native_routes(logic=[logic], block=changed)
    assert await read_native(settings) != before
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("family", ["subflow", "logic", "block"])
@pytest.mark.parametrize("drift", [False, True])
async def test_native_active_edit_preserves_published_calls_logic_and_shared_blocks(
    settings, monkeypatch, family, drift
):
    model = ActiveModel(settings, monkeypatch)
    model.snapshot["jsonSnapshot"] = False
    version_record = {**model.main, "sys_id": SELECTED, "master": "false"}
    http_mock.get(f"{BASE}/api/now/table/sys_hub_flow_snapshot/{SELECTED}").respond(
        200, json={"result": version_record}
    )
    row = subflow_row()
    row["flow"] = SELECTED
    block = None
    if family != "subflow":
        for key in ("subflow", "subflow_inputs", "wait_for_completion"):
            row.pop(key)
        row.update(
            logic_definition="c2" * 16,
            values="Opaque native logic bindings",
            flow_variables_assigned="",
            outputs_assigned="",
            block=BLOCK if family == "block" else "",
            connected_to="",
            workflow_reference="",
            decision_table="",
        )
        if family == "block":
            block = {
                "sys_id": BLOCK,
                "sys_mod_count": "0",
                "sys_updated_on": TIME,
                "sys_class_name": "sys_hub_flow_block",
                "name": "Assignment",
                "label_cache": "",
            }

    def install_rows():
        native_routes(
            subflows=[row] if family == "subflow" else [],
            logic=[row] if family != "subflow" else [],
            block=block,
        )

    install_rows()
    if drift:

        def change_runtime():
            if family == "subflow":
                row["wait_for_completion"] = "true"
            elif family == "logic":
                row["values"] = "Unexpected compiled binding change"
            else:
                assert block is not None
                block["label_cache"] = "Changed shared block"
            install_rows()

        monkeypatch.setattr(model, "after_save", change_runtime)
    result = await model.apply(await model.preview())
    if drift:
        assert result["error"]["code"] == "UNKNOWN_OUTCOME", result
        assert result["error"]["outcome"]["runtime_preservation"] == "unknown"
    else:
        assert result["status"] == "success", result
        assert result["data"]["runtime_preservation"] == "verified"
    assert len(model.writes) == 1
