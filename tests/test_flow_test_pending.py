"""One-shot test execution and source-derived native test-modal input payloads."""

import copy
import json

import httpx2
import pytest

from servicenow_mcp.capabilities.flows import declarations
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_completion import effect_routes
from tests.test_flow_editing import BASE, FLOW


@pytest.mark.parametrize("owner,state", [(FLOW, ""), ("", "QUEUED"), ("", "COMPLETE")])
async def test_accepted_owned_context_without_initial_state_is_not_reexecuted(
    settings, owner, state
):
    _, tools, _, _, args = effect_routes(settings, "test")
    context = "d" * 32
    http_mock.get(f"{BASE}/api/now/table/sys_flow_context/{context}").respond(
        200, json={"result": {"sys_id": context, "flow": owner, "state": state}}
    )
    write = http_mock.post(f"{BASE}/api/now/processflow/flow/{FLOW}/test").respond(
        200, json={"result": {"data": context}}
    )
    preview = decode_response(await tools["flow_preview"](**args))
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "success", result
    assert result["data"]["execution_sys_id"] == context
    assert (
        result["data"]["terminal"] is None
        and result["data"]["execution_succeeded"] is None
    )
    assert write.call_count == 1
    replay = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert replay["status"] == "error" and write.call_count == 1


def array_input(name, kind, children=None):
    item_name = name + "." + name.split(".")[-1] + "_child0"
    return {
        "name": name,
        "type": kind,
        "parent": name.rpartition(".")[0],
        "children": [
            {
                "name": item_name,
                "parent": name,
                "type": kind.split(".")[1],
                "uiUniqueId": item_name + "_ui",
                "children": children or [],
            }
        ],
    }


def subflow_test_routes(settings, schemas, inputs):
    coordinator, tools, flow, header, args = effect_routes(settings, "publish")
    flow["inputs"] = schemas
    args.update(action="test", inputs=inputs)
    return coordinator, tools, flow, header, args


def _test_execution_route():
    context = "d" * 32
    http_mock.get(f"{BASE}/api/now/table/sys_flow_context/{context}").respond(
        200, json={"result": {"sys_id": context, "flow": FLOW, "state": "WAITING"}}
    )
    return http_mock.post(f"{BASE}/api/now/processflow/flow/{FLOW}/test").respond(
        200, json={"result": {"data": context}}
    )


async def test_nested_object_and_array_object_use_test_modal_child_rows(settings):
    schema = declarations.pack(
        {
            "name": "payload",
            "type": "object",
            "children": [
                {"name": "payload.enabled", "type": "boolean", "mandatory": True},
                {
                    "name": "payload.details",
                    "type": "object",
                    "children": [
                        {"name": "payload.details.count", "type": "integer"},
                        array_input("payload.details.tags", "array.string"),
                    ],
                },
                array_input(
                    "payload.rows",
                    "array.object",
                    [
                        {"name": "payload.rows.rows_child0.label", "type": "string"},
                        array_input("payload.rows.rows_child0.flags", "array.boolean"),
                    ],
                ),
            ],
        }
    )
    inputs = {
        "payload": {
            "enabled": False,
            "details": {"count": 0, "tags": ["first", ""]},
            "rows": [
                {"label": "one", "flags": [False, True]},
                {"label": "two", "flags": []},
            ],
        }
    }
    before_schema, before_inputs = copy.deepcopy(schema), copy.deepcopy(inputs)
    coordinator, tools, _flow, _header, args = subflow_test_routes(
        settings, [schema], inputs
    )
    write = _test_execution_route()
    preview = decode_response(await tools["flow_preview"](**args))
    assert preview["status"] == "success", preview
    assert preview["data"]["summary"]["test_inputs"] == inputs
    assert not write.called
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "success" and result["data"]["state"] == "WAITING", (
        result
    )
    assert write.call_count == 1 and len(coordinator.store) == 0
    body = json.loads(
        next(
            call.request.content
            for call in http_mock.calls
            if call.request.method == "POST"
        )
    )
    assert body["runOnThread"] is False
    assert body["outputMap"] == {
        "payload": {
            "children": [
                {"name": "enabled", "value": False},
                {
                    "name": "details",
                    "value": "",
                    "children": [
                        {"name": "count", "value": 0},
                        {
                            "name": "tags",
                            "value": "",
                            "children": [
                                {"name": "tags_child0", "value": "first"},
                                {"name": "tags_child0", "value": ""},
                            ],
                        },
                    ],
                },
                {
                    "name": "rows",
                    "value": "",
                    "children": [
                        {
                            "name": "rows_child0",
                            "value": "",
                            "children": [
                                {"name": "label", "value": "one"},
                                {
                                    "name": "flags",
                                    "value": "",
                                    "children": [
                                        {"name": "flags_child0", "value": False},
                                        {"name": "flags_child0", "value": True},
                                    ],
                                },
                            ],
                        },
                        {
                            "name": "rows_child0",
                            "value": "",
                            "children": [
                                {"name": "label", "value": "two"},
                                {"name": "flags", "value": ""},
                            ],
                        },
                    ],
                },
            ]
        }
    }
    assert body["flow"]["inputs"] == [before_schema]
    assert schema == before_schema and inputs == before_inputs
    replay = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert replay["status"] == "error" and write.call_count == 1


@pytest.mark.parametrize(
    "kind,values,expected",
    [
        (
            "array.string",
            ["", "text"],
            {
                "children": [
                    {"name": "items_child0", "value": ""},
                    {"name": "items_child0", "value": "text"},
                ]
            },
        ),
        (
            "array.integer",
            [0, 2],
            {
                "children": [
                    {"name": "items_child0", "value": 0},
                    {"name": "items_child0", "value": 2},
                ]
            },
        ),
        (
            "array.boolean",
            [False, True],
            {
                "children": [
                    {"name": "items_child0", "value": False},
                    {"name": "items_child0", "value": True},
                ]
            },
        ),
        (
            "array.object",
            [{"label": "one"}, {"label": "two"}],
            {
                "children": [
                    {
                        "name": "items_child0",
                        "value": "",
                        "children": [{"name": "label", "value": "one"}],
                    },
                    {
                        "name": "items_child0",
                        "value": "",
                        "children": [{"name": "label", "value": "two"}],
                    },
                ]
            },
        ),
        ("array.string", [], ""),
        ("array.integer", [], ""),
        ("array.boolean", [], ""),
        ("array.object", [], ""),
    ],
)
async def test_declared_arrays_use_modal_rows_and_native_empty_sentinel(
    settings, kind, values, expected
):
    schema = array_input(
        "items",
        kind,
        [{"name": "items.items_child0.label", "type": "string"}]
        if kind == "array.object"
        else None,
    )
    _, tools, _flow, _header, args = subflow_test_routes(
        settings, [declarations.pack(schema)], {"items": values}
    )
    write = _test_execution_route()
    preview = decode_response(await tools["flow_preview"](**args))
    assert preview["status"] == "success", preview
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "success", result
    assert write.call_count == 1
    body = json.loads(
        next(
            call.request.content
            for call in http_mock.calls
            if call.request.method == "POST"
        )
    )
    assert body["outputMap"] == {"items": expected}


@pytest.mark.parametrize(
    "schema,value",
    [
        ({"name": "payload", "type": "object", "children": []}, {}),
        ({"name": "payload", "type": "object"}, {}),
        (array_input("payload", "array.string"), [1]),
        (array_input("payload", "array.string"), ["{{producer.value}}"]),
        (array_input("payload", "array.string"), ["broken {{pill"]),
        (array_input("payload", "array.integer"), [True]),
        (array_input("payload", "array.integer"), [None]),
        (array_input("payload", "array.boolean"), ["false"]),
        (array_input("payload", "array.string"), {}),
        ({"name": "payload", "type": "array.string", "children": []}, []),
        (
            {
                "name": "payload",
                "type": "array.string",
                "children": [{"name": "item", "type": "integer"}],
            },
            [],
        ),
        (
            array_input(
                "payload", "array.object", [{"name": "label", "type": "string"}]
            ),
            [{"label": "ok", "other": 1}],
        ),
        (
            array_input(
                "payload", "array.object", [{"name": "label", "type": "string"}]
            ),
            ["not-an-object"],
        ),
        (
            array_input(
                "payload", "array.object", [{"name": "label", "type": "string"}]
            ),
            [{"label": "{{producer.value}}"}],
        ),
        (
            {
                "name": "payload",
                "type": "object",
                "children": [
                    {"name": "one.leaf", "type": "string"},
                    {"name": "two.leaf", "type": "string"},
                ],
            },
            {"leaf": "ambiguous"},
        ),
        ({"name": "payload", "type": "records", "reference": "sc_req_item"}, "f" * 32),
        (
            {"name": "payload", "type": "glide_list", "reference": "sc_req_item"},
            "f" * 32,
        ),
        (
            {
                "name": "payload",
                "type": "array.reference",
                "reference": "sc_req_item",
                "children": [
                    {"name": "item", "type": "reference", "reference": "sc_req_item"}
                ],
            },
            [],
        ),
        ({"name": "payload", "type": "unknown"}, None),
    ],
)
async def test_malformed_complex_schema_or_value_never_stages(settings, schema, value):
    coordinator, tools, _flow, _header, args = subflow_test_routes(
        settings, [copy.deepcopy(schema)], {"payload": value}
    )
    write = _test_execution_route()
    result = decode_response(await tools["flow_preview"](**args))
    assert result["status"] == "error", result
    assert len(coordinator.store) == 0 and not write.called
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "constraint,value",
    [
        ({}, {"unknown": "text"}),
        ({}, {"leaf": 1}),
        ({}, {"leaf": None}),
        ({"maxsize": 3}, {"leaf": "four"}),
        ({"mandatory": True}, {"leaf": ""}),
        ({"mandatory": True}, {}),
        ({"defaultValue": "not-selected"}, {}),
        ({}, {"leaf": "{{producer.value}}"}),
        ({}, {"leaf": "malformed {{pill"}),
        ({}, {"leaf": "javascript:gs.getUserID()"}),
        ({"readonly": True}, {"leaf": "text"}),
        ({"readOnly": True}, {"leaf": "text"}),
        ({"attributes": {"test_input_read_only": "true"}}, {"leaf": "text"}),
        ({"attributes": {"test_input_hidden": "true"}}, {"leaf": "text"}),
        ({"dynamic": True}, {"leaf": "text"}),
        ({"dependent_on": "table"}, {"leaf": "text"}),
        ({"scriptActive": True}, {"leaf": "text"}),
        ({"type": "choice", "choices": [{"value": "allowed"}]}, {"leaf": "other"}),
    ],
)
async def test_nested_constraints_do_not_select_defaults_or_accept_scripts(
    settings, constraint, value
):
    schema = {
        "name": "payload",
        "type": "object",
        "children": [{"name": "payload.leaf", "type": "string", **constraint}],
    }
    coordinator, tools, _flow, _header, args = subflow_test_routes(
        settings, [schema], {"payload": value}
    )
    write = _test_execution_route()
    result = decode_response(await tools["flow_preview"](**args))
    assert result["status"] == "error", result
    assert len(coordinator.store) == 0 and not write.called
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "kind", ["array.string", "array.integer", "array.boolean", "array.object"]
)
async def test_mandatory_declared_array_rejects_explicit_empty_list(settings, kind):
    schema = array_input(
        "payload",
        kind,
        [{"name": "payload.payload_child0.label", "type": "string"}]
        if kind == "array.object"
        else None,
    )
    schema["mandatory"] = True
    coordinator, tools, _flow, _header, args = subflow_test_routes(
        settings, [schema], {"payload": []}
    )
    result = decode_response(await tools["flow_preview"](**args))
    assert result["status"] == "error" and len(coordinator.store) == 0, result
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize("kind", ["reference", "document_id"])
@pytest.mark.parametrize(
    "values", [[], [{"record": "f" * 32}], [{"record": ""}], [{"record": None}]]
)
async def test_nested_record_schema_is_rejected_without_hidden_record_access(
    settings, kind, values
):
    schema = array_input(
        "payload",
        "array.object",
        [
            {
                "name": "payload.payload_child0.record",
                "type": kind,
                "reference": "sc_req_item",
            }
        ],
    )
    coordinator, tools, _flow, _header, args = subflow_test_routes(
        settings, [schema], {"payload": values}
    )
    result = decode_response(await tools["flow_preview"](**args))
    assert result["error"]["code"] == "UNAVAILABLE" and len(coordinator.store) == 0, (
        result
    )
    assert all(
        "/table/sc_req_item/" not in call.request.url.path for call in http_mock.calls
    )
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "defect", ["missing", "identity", "revision", "changed", "denied", "invalid_sys_id"]
)
async def test_complex_inputs_retain_explicit_root_record_guards(
    settings, defect, monkeypatch
):
    from servicenow_mcp import policy

    record_id = "f" * 32
    schema = {
        "name": "payload",
        "type": "object",
        "children": [{"name": "note", "type": "string"}],
    }
    coordinator, tools, _flow, _header, args = subflow_test_routes(
        settings,
        [schema, {"name": "record", "type": "reference", "reference": "sc_req_item"}],
        {"payload": {"note": "explicit"}, "record": record_id},
    )
    record = {"sys_id": record_id, "sys_mod_count": "0"}
    if defect == "identity":
        record["sys_id"] = "e" * 32
    elif defect == "revision":
        record.pop("sys_mod_count")
    elif defect == "denied":
        monkeypatch.setattr(
            policy, "DENIED_TABLES", policy.DENIED_TABLES | {"sc_req_item"}
        )
    elif defect == "invalid_sys_id":
        args["inputs"]["record"] = "not-an-id"
    record_route = http_mock.get(f"{BASE}/api/now/table/sc_req_item/{record_id}").mock(
        side_effect=lambda _: (
            httpx2.Response(403)
            if defect == "missing"
            else httpx2.Response(200, json={"result": record})
        )
    )
    write = _test_execution_route()
    preview = decode_response(await tools["flow_preview"](**args))
    if defect == "changed":
        assert preview["status"] == "success", preview
        record["sys_mod_count"] = "1"
        result = decode_response(
            await tools["mutation_apply"](
                preview_token=preview["data"]["preview_token"]
            )
        )
        assert result["error"]["code"] == "STALE_PREVIEW", result
    else:
        assert preview["status"] == "error", preview
    assert len(coordinator.store) == 0 and not write.called
    if defect in {"denied", "invalid_sys_id"}:
        assert not record_route.called
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_hidden_root_record_is_not_defaulted_or_looked_up(settings):
    _, tools, _flow, _header, args = subflow_test_routes(
        settings,
        [
            {
                "name": "payload",
                "type": "object",
                "children": [{"name": "note", "type": "string"}],
            },
            {
                "name": "record",
                "type": "reference",
                "reference": "sc_req_item",
                "mandatory": True,
                "defaultValue": "f" * 32,
                "attributes": {"test_input_hidden": "true"},
            },
        ],
        {"payload": {"note": "explicit"}},
    )
    write = _test_execution_route()
    preview = decode_response(await tools["flow_preview"](**args))
    assert preview["status"] == "success", preview
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "success" and write.call_count == 1, result
    body = json.loads(
        next(
            call.request.content
            for call in http_mock.calls
            if call.request.method == "POST"
        )
    )
    assert body["outputMap"] == {
        "payload": {"children": [{"name": "note", "value": "explicit"}]}
    }
    assert all(
        "/table/sc_req_item/" not in call.request.url.path for call in http_mock.calls
    )


@pytest.mark.parametrize("mandatory", [False, True])
async def test_optional_and_mandatory_root_objects_never_select_declared_defaults(
    settings, mandatory
):
    coordinator, tools, _flow, _header, args = subflow_test_routes(
        settings,
        [
            {
                "name": "payload",
                "type": "object",
                "mandatory": mandatory,
                "defaultValue": {"note": "not-selected"},
                "children": [{"name": "note", "type": "string"}],
            }
        ],
        {},
    )
    result = decode_response(await tools["flow_preview"](**args))
    assert result["status"] == "error" and len(coordinator.store) == 0, result
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "restriction",
    [
        {"readonly": True},
        {"readOnly": True},
        {"attributes": {"test_input_read_only": "true"}},
        {"attributes": {"test_input_hidden": "true"}},
    ],
)
async def test_root_complex_hidden_and_read_only_overrides_are_rejected(
    settings, restriction
):
    coordinator, tools, _flow, _header, args = subflow_test_routes(
        settings,
        [
            {
                "name": "payload",
                "type": "object",
                "children": [{"name": "note", "type": "string"}],
                **restriction,
            }
        ],
        {"payload": {"note": "explicit"}},
    )
    result = decode_response(await tools["flow_preview"](**args))
    assert result["status"] == "error" and len(coordinator.store) == 0, result
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_trigger_array_output_remains_unavailable(settings):
    coordinator, tools, flow, _header, args = effect_routes(settings, "test")
    flow["triggerInstances"][0]["outputs"].append(
        array_input("unexpected_array", "array.string")
    )
    args["inputs"]["unexpected_array"] = ["explicit"]
    write = _test_execution_route()
    result = decode_response(await tools["flow_preview"](**args))
    assert result["status"] == "error" and len(coordinator.store) == 0, result
    assert not write.called
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_hidden_nested_field_is_omitted_without_selecting_default(settings):
    _, tools, _flow, _header, args = subflow_test_routes(
        settings,
        [
            {
                "name": "payload",
                "type": "object",
                "children": [
                    {"name": "note", "type": "string"},
                    {
                        "name": "hidden",
                        "type": "string",
                        "mandatory": True,
                        "defaultValue": "not-selected",
                        "attributes": {"test_input_hidden": "true"},
                    },
                ],
            }
        ],
        {"payload": {"note": "explicit"}},
    )
    write = _test_execution_route()
    preview = decode_response(await tools["flow_preview"](**args))
    assert preview["status"] == "success", preview
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "success" and write.call_count == 1, result
    body = json.loads(
        next(
            call.request.content
            for call in http_mock.calls
            if call.request.method == "POST"
        )
    )
    assert body["outputMap"] == {
        "payload": {"children": [{"name": "note", "value": "explicit"}]}
    }
