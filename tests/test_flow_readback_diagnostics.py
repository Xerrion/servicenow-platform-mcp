"""Independent save failures retain safe locations without relaxing equality."""

import copy

import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import CapabilityError
from servicenow_mcp.capabilities.flows import semantics
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock
from tests.helpers import decode_response
from tests.test_flow_editing import BASE, FLOW, base_flow, fixtures


@pytest.mark.parametrize(
    ("field", "value", "path", "kind"),
    [
        (
            "value",
            "private-sensitive-marker",
            "flow.actionInstances[0].inputs[0].value",
            "VALUE_MISMATCH",
        ),
        ("value", False, "flow.actionInstances[0].inputs[0].value", "TYPE_MISMATCH"),
        (
            "displayValue",
            "private-sensitive-marker",
            "flow.actionInstances[0].inputs[0].displayValue",
            "MISSING_FIELD",
        ),
        (
            "parameter",
            {"type": "integer"},
            "flow.actionInstances[0].inputs[0].parameter.type",
            "VALUE_MISMATCH",
        ),
    ],
)
async def test_request_echo_mismatch_retains_phase_path_kind(
    settings, field, value, path, kind
):
    wanted = base_flow()
    echo = copy.deepcopy(wanted)
    echo["actionInstances"][0]["inputs"][0][field] = value
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(CapabilityError) as error:
            await semantics.verify_saved(client, wanted, echo, generated_ids=set())
    assert error.value.code == "UNKNOWN_OUTCOME"
    assert "request.echo" in str(error.value)
    assert path in str(error.value)
    assert kind in str(error.value)
    assert "private-sensitive-marker" not in str(error.value)
    assert not http_mock.calls


async def test_unavailable_readback_does_not_echo_raw_error(settings):
    flow = base_flow()
    http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").respond(
        403,
        json={"error": {"message": "private-sensitive-marker"}},
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(CapabilityError) as error:
            await semantics.verify_saved(
                client, flow, copy.deepcopy(flow), generated_ids=set()
            )
    assert "readback.get, flow UNAVAILABLE_READBACK" in str(error.value)
    assert "private-sensitive-marker" not in str(error.value)
    assert error.value.code == "UNKNOWN_OUTCOME"


async def test_incomplete_readback_retains_parser_invariant(settings):
    flow = base_flow()
    actual = copy.deepcopy(flow)
    actual["actionInstances"][0].pop("parent")
    http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").respond(
        200, json={"result": {"data": actual}}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(CapabilityError) as error:
            await semantics.verify_saved(
                client, flow, copy.deepcopy(flow), generated_ids=set()
            )
    assert (
        "request.readback, actual:flow.actionInstances[0].parent INCOMPLETE_SEMANTICS"
        in str(error.value)
    )
    assert "Root steps require an explicitly empty string." in str(error.value)


async def test_echo_readback_requires_stable_assigned_identity(settings):
    flow = base_flow()
    echo = copy.deepcopy(flow)
    actual = copy.deepcopy(flow)
    echo["actionInstances"][0]["id"] = "8" * 32
    actual["actionInstances"][0]["id"] = "9" * 32
    route = http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").respond(
        200, json={"result": {"data": actual}}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(CapabilityError) as error:
            await semantics.verify_saved(client, flow, echo, generated_ids={"1" * 32})
    assert "echo.readback, both:flow.actionInstances[0].id VALUE_MISMATCH" in str(
        error.value
    )
    assert error.value.code == "UNKNOWN_OUTCOME"
    assert route.call_count == 1


async def test_same_value_preview_exposes_serializer_delta_without_save(settings):
    flow = base_flow()
    _, tools = fixtures(settings, flow)
    before = copy.deepcopy(flow)
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {"op": "update_inputs", "step": "1", "inputs": {"log_message": "one"}}
            ],
        ),
    )
    check = preview["data"]["summary"]["serialization_check"]
    assert check["mode"] == "planned_vs_current_read_only"
    assert check["matches"] is False
    assert (check["path"], check["kind"], check["side"]) == (
        "flow.actionInstances[0].inputs[0].displayValue",
        "MISSING_FIELD",
        "actual",
    )
    assert flow == before
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_changed_value_preview_does_not_compare_against_unchanged_saved_values(
    settings,
):
    flow = base_flow()
    _, tools = fixtures(settings, flow)
    preview = decode_response(
        await tools["flow_preview"](
            action="edit",
            sys_id=FLOW,
            edits=[
                {"op": "update_inputs", "step": "1", "inputs": {"log_message": "new"}}
            ],
        ),
    )
    assert "serialization_check" not in preview["data"]["summary"]
    assert all(call.request.method == "GET" for call in http_mock.calls)


def cache_flow():
    """Synthetic saved cache projection, not a captured target definition.

    processLabelCacheIncoming (docs/designer-bundle.min.js:92941-92973) and
    processLabelCacheOutgoing (101714-101728) both map absent attributes to null.
    PillResolver._field supplies null for the target's dot-walked state pill.
    """
    flow = base_flow()
    flow["label_cache"] = [
        {
            "name": "Service Catalog_1.request_item.state",
            "label": "State",
            "type": "choice",
            "base_type": "choice",
            "reference": "",
            "parent_table_name": "sc_req_item",
            "column_name": "state",
            "choices": [{"value": "1", "label": "Open"}],
            "usedInstances": {"ui-1": ["log_message"]},
        },
    ]
    return flow


@pytest.mark.parametrize("reverse", [False, True])
async def test_null_cache_attributes_match_omitted_in_echo_and_independent_read(
    settings, reverse
):
    omitted = cache_flow()
    explicit = copy.deepcopy(omitted)
    explicit["label_cache"][0]["attributes"] = None
    expected, actual = (omitted, explicit) if reverse else (explicit, omitted)
    before = copy.deepcopy((expected, actual))
    assert semantics.studio_semantics(expected) == semantics.studio_semantics(actual)
    route = http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").respond(
        200, json={"result": {"data": actual}}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        assert (
            await semantics.verify_saved(
                client, expected, copy.deepcopy(actual), generated_ids=set()
            )
            == actual
        )
    assert route.call_count == 1
    assert (expected, actual) == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attributes", {}),
        ("attributes", {"mandatory": True}),
        ("attributes", {"reference": "incident"}),
        ("name", "another.pill"),
        ("type", "reference"),
        ("base_type", "integer"),
        ("reference", "incident"),
        ("parent_table_name", "incident"),
        ("column_name", "number"),
        ("choices", [{"value": "2", "label": "Closed"}]),
        ("usedInstances", {"ui-2": ["log_message"]}),
        ("vendorSetting", "private-sensitive-marker"),
    ],
)
def test_cache_normalization_keeps_nonnull_attributes_and_other_fields_strict(
    field, value
):
    expected = cache_flow()
    expected["label_cache"][0]["attributes"] = None
    actual = copy.deepcopy(expected)
    actual["label_cache"][0][field] = value
    with pytest.raises(CapabilityError) as error:
        semantics.compare_saved(expected, actual, generated_ids=set())
    assert error.value.code == "UNKNOWN_OUTCOME"
    assert "private-sensitive-marker" not in str(error.value)


def test_nonempty_cache_attribute_changes_or_loss_remain_mismatches():
    expected = cache_flow()
    expected["label_cache"][0]["attributes"] = {"mandatory": True}
    for replacement in (None, {"mandatory": False}):
        actual = copy.deepcopy(expected)
        actual["label_cache"][0]["attributes"] = replacement
        with pytest.raises(CapabilityError):
            semantics.compare_saved(expected, actual, generated_ids=set())
    actual = copy.deepcopy(expected)
    actual["label_cache"][0].pop("attributes")
    with pytest.raises(CapabilityError):
        semantics.compare_saved(expected, actual, generated_ids=set())


def test_null_attributes_outside_label_cache_are_not_normalized():
    expected = cache_flow()
    expected["actionInstances"][0]["inputs"][0]["parameter"]["attributes"] = None
    actual = copy.deepcopy(expected)
    actual["actionInstances"][0]["inputs"][0]["parameter"].pop("attributes")
    with pytest.raises(CapabilityError):
        semantics.compare_saved(expected, actual, generated_ids=set())


def cache_with_tied_choices():
    """Public metadata projection, not the target's full Studio response.

    Live sys_choice sc_req_item.state has sequence=3 for values 61 and 9.
    The stored cache and table-specific choices match by value, including labels.
    ORDERBYsequence alone cannot define their relative order. Studio consumes
    cached choices by value and uses label for display (bundle:96149-96154).
    """
    flow = cache_flow()
    flow["label_cache"][0]["choices"] = [
        {
            "value": "1",
            "label": "New",
            "rawLabel": "New",
            "parameters": {"dependent_values": [""]},
        },
        {
            "value": "61",
            "label": "Awaiting User Info",
            "rawLabel": "Awaiting User Info",
            "parameters": {"dependent_values": [""]},
        },
        {
            "value": "9",
            "label": "Awaiting approval",
            "rawLabel": "Awaiting approval",
            "parameters": {"dependent_values": [""]},
        },
    ]
    return flow


async def test_cache_choice_order_is_presentation_only_in_echo_and_readback(settings):
    expected = cache_with_tied_choices()
    echo = copy.deepcopy(expected)
    echo["label_cache"][0]["choices"].reverse()
    actual = copy.deepcopy(expected)
    choices = actual["label_cache"][0]["choices"]
    choices[1], choices[2] = choices[2], choices[1]
    before = copy.deepcopy((expected, echo, actual))
    route = http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").respond(
        200, json={"result": {"data": actual}}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        assert (
            await semantics.verify_saved(client, expected, echo, generated_ids=set())
            == actual
        )
    assert (expected, echo, actual) == before
    assert route.call_count == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("value", "another"),
        ("value", 61),
        ("label", "Changed"),
        ("rawLabel", "Changed"),
        ("disabled", True),
        ("parameters", {"dependent_values": ["restricted"]}),
        ("selected", True),
        ("missing", True),
        ("order", 100),
        ("vendorSetting", "private-sensitive-marker"),
    ],
)
def test_cache_choice_order_normalization_retains_all_choice_fields(field, value):
    expected = cache_with_tied_choices()
    actual = copy.deepcopy(expected)
    actual["label_cache"][0]["choices"][1][field] = value
    with pytest.raises(CapabilityError) as error:
        semantics.compare_saved(expected, actual, generated_ids=set())
    assert error.value.code == "UNKNOWN_OUTCOME"
    assert "private-sensitive-marker" not in str(error.value)


def test_cache_choice_order_normalization_preserves_duplicate_multiplicity_and_type():
    expected = cache_with_tied_choices()
    actual = copy.deepcopy(expected)
    actual["label_cache"][0]["choices"].append(
        copy.deepcopy(actual["label_cache"][0]["choices"][0])
    )
    with pytest.raises(CapabilityError):
        semantics.compare_saved(expected, actual, generated_ids=set())
    expected["label_cache"][0]["choices"][0]["value"] = True
    actual = copy.deepcopy(expected)
    actual["label_cache"][0]["choices"][0]["value"] = 1
    with pytest.raises(CapabilityError):
        semantics.compare_saved(expected, actual, generated_ids=set())


def test_definition_and_binding_choice_order_remains_strict():
    expected = cache_with_tied_choices()
    parameter = expected["actionInstances"][0]["inputs"][0]["parameter"]
    parameter["choices"] = copy.deepcopy(expected["label_cache"][0]["choices"])
    actual = copy.deepcopy(expected)
    actual["actionInstances"][0]["inputs"][0]["parameter"]["choices"].reverse()
    with pytest.raises(CapabilityError):
        semantics.compare_saved(expected, actual, generated_ids=set())


@pytest.mark.parametrize("prior", [None, "", "2026-10-15 12:59:00"])
async def test_server_updated_advance_reaches_independent_get_without_mutating_request(
    settings, prior
):
    expected = base_flow()
    if prior is not None:
        expected["updated"] = prior
    actual = copy.deepcopy(expected)
    actual["updated"] = "2026-10-15 13:00:25"
    before = copy.deepcopy(expected)
    route = http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").respond(
        200, json={"result": {"data": actual}}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        assert (
            await semantics.verify_saved(
                client, expected, copy.deepcopy(actual), generated_ids=set()
            )
            == actual
        )
    assert route.call_count == 1
    assert expected == before


@pytest.mark.parametrize(
    ("updated", "kind"),
    [
        ("2026-10-15 12:00:00", "SERVER_REVISION_REGRESSION"),
        ("private-sensitive-marker", "INVALID_SERVER_REVISION"),
        ("2026-02-30 13:00:25", "INVALID_SERVER_REVISION"),
        (None, "INVALID_SERVER_REVISION"),
        (True, "INVALID_SERVER_REVISION"),
        ({"value": "private-sensitive-marker"}, "INVALID_SERVER_REVISION"),
        ("", "MISSING_SERVER_REVISION"),
    ],
)
async def test_invalid_or_regressed_save_revision_fails_without_get(
    settings, updated, kind
):
    expected = base_flow()
    expected["updated"] = "2026-10-15 12:59:00"
    echo = copy.deepcopy(expected)
    echo["updated"] = updated
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(CapabilityError) as error:
            await semantics.verify_saved(client, expected, echo, generated_ids=set())
    assert error.value.code == "UNKNOWN_OUTCOME"
    assert "request.echo" in str(error.value)
    assert "flow.updated" in str(error.value)
    assert kind in str(error.value)
    assert "private-sensitive-marker" not in str(error.value)
    assert not http_mock.calls


async def test_missing_postsave_revision_cannot_replace_populated_timestamp(settings):
    expected = base_flow()
    expected["updated"] = "2026-10-15 12:59:00"
    echo = copy.deepcopy(expected)
    echo.pop("updated")
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(CapabilityError) as error:
            await semantics.verify_saved(client, expected, echo, generated_ids=set())
    assert "flow.updated MISSING_SERVER_REVISION" in str(error.value)


async def test_echo_and_independent_get_must_observe_the_same_revision(settings):
    expected = base_flow()
    expected["updated"] = "2026-10-15 12:59:00"
    echo = copy.deepcopy(expected)
    echo["updated"] = "2026-10-15 13:00:25"
    actual = copy.deepcopy(echo)
    actual["updated"] = "2026-10-15 13:00:26"
    route = http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").respond(
        200, json={"result": {"data": actual}}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(CapabilityError) as error:
            await semantics.verify_saved(client, expected, echo, generated_ids=set())
    assert "echo.readback" in str(error.value)
    assert "flow.updated SERVER_REVISION_MISMATCH" in str(error.value)
    assert route.call_count == 1


@pytest.mark.parametrize(
    "field", ["updatedBy", "created", "sys_updated_on", "sys_mod_count", "version"]
)
def test_unproven_actor_and_other_revision_fields_are_not_excluded(field):
    expected = base_flow()
    expected[field] = "before"
    actual = copy.deepcopy(expected)
    actual[field] = "after"
    with pytest.raises(CapabilityError):
        semantics.compare_saved(expected, actual, generated_ids=set())


def test_readonly_comparison_requires_equal_updated_metadata():
    expected = base_flow()
    expected["updated"] = "2026-10-15 12:59:00"
    actual = copy.deepcopy(expected)
    actual["updated"] = "2026-10-15 13:00:25"
    with pytest.raises(CapabilityError) as error:
        semantics.compare_saved(expected, actual, generated_ids=set())
    assert "flow.updated SERVER_REVISION_MISMATCH" in str(error.value)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("2026-10-15 12:59:00", "2026-10-15 12:59:00"),
        ("1792069140000", "1792069225000"),
        ("2026-10-15 12:59:00", "1792069225000"),
        ("", ""),
    ],
)
async def test_documented_updated_shapes_and_same_second_save_are_supported(
    settings, before, after
):
    expected = base_flow()
    expected["updated"] = before
    actual = copy.deepcopy(expected)
    actual["updated"] = after
    route = http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").respond(
        200, json={"result": {"data": actual}}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        assert (
            await semantics.verify_saved(
                client, expected, copy.deepcopy(actual), generated_ids=set()
            )
            == actual
        )
    assert route.call_count == 1


@pytest.mark.parametrize(
    "defect",
    [
        "parent",
        "definition",
        "interface",
        "opaque",
        "type",
        "active",
        "status",
        "identity",
    ],
)
async def test_server_revision_advance_does_not_hide_semantic_changes(settings, defect):
    expected = base_flow()
    expected["updated"] = "2026-10-15 12:59:00"
    echo = copy.deepcopy(expected)
    echo["updated"] = "2026-10-15 13:00:25"
    actual = copy.deepcopy(echo)
    if defect == "parent":
        actual["actionInstances"][0]["parent"] = "missing"
    elif defect == "definition":
        actual["actionInstances"][0]["actionTypeSysId"] = "9" * 32
    elif defect == "interface":
        actual["outputs"] = [{"name": "result", "type": "string"}]
    elif defect == "opaque":
        actual["vendorSetting"] = "private-sensitive-marker"
    elif defect == "identity":
        actual["id"] = "9" * 32
    else:
        actual[defect] = {"type": "subflow", "active": True, "status": "published"}[
            defect
        ]
    route = http_mock.get(f"{BASE}/api/now/processflow/flow/{FLOW}").respond(
        200, json={"result": {"data": actual}}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(CapabilityError) as error:
            await semantics.verify_saved(client, expected, echo, generated_ids=set())
    assert error.value.code == "UNKNOWN_OUTCOME"
    assert "request.readback" in str(error.value)
    assert "private-sensitive-marker" not in str(error.value)
    assert route.call_count == 1
