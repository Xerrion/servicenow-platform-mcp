"""Flow authoring uses target scope/revision, never update-set or capture access."""

import pytest
from pydantic import ValidationError

from servicenow_mcp.capabilities.mutations.models import FlowCommand
from servicenow_mcp.platform.client import ServiceNowClient
from tests._complete_flow_fixture import create_request, edit_request
from tests.helpers import decode_response
from tests.test_flow_complete_creation import apply, setup


@pytest.fixture(autouse=True)
def forbid_update_set_reads(monkeypatch):
    original = ServiceNowClient.flow_record

    async def read(client, table, *args, **kwargs):
        assert table not in {"sys_update_set", "sys_update_xml"}
        return await original(client, table, *args, **kwargs)

    monkeypatch.setattr(ServiceNowClient, "flow_record", read)
    for name in ("get_record", "query_records"):
        original_method = getattr(ServiceNowClient, name)

        def guarded(original):
            async def call(client, table, *args, **kwargs):
                assert table not in {"sys_update_set", "sys_update_xml"}
                return await original(client, table, *args, **kwargs)

            return call

        monkeypatch.setattr(ServiceNowClient, name, guarded(original_method))


@pytest.mark.parametrize("complete", [False, True])
async def test_create_and_edit_without_update_set_access(settings, complete):
    model, _, tools = setup(settings)
    args = create_request()
    if not complete:
        args.pop("edits")
    result = await apply(tools, decode_response(await tools["flow_preview"](**args)))
    assert result["status"] == "success", result
    assert "capture" not in result["data"]
    assert result["data"]["revision"] == ("14" if complete else "13")
    if complete:
        args = edit_request(result["data"])
        result = await apply(
            tools, decode_response(await tools["flow_preview"](**args))
        )
        assert result["status"] == "success", result
        assert result["data"]["revision"] == "15" and "capture" not in result["data"]
    assert all(
        "sys_update_" not in path and "concoursepicker" not in path
        for _, path in model.calls
    )


async def test_obsolete_update_set_argument_is_rejected_not_silently_bound(settings):
    model, _, tools = setup(settings)
    args = {**create_request(), "expected_update_set": "e" * 32}
    result = decode_response(await tools["flow_preview"](**args))
    assert result["status"] == "error" and not model.calls
    assert "expected_update_set" not in FlowCommand.model_fields
    with pytest.raises(ValidationError):
        FlowCommand.model_validate(args)


@pytest.mark.parametrize("selected", ["missing", "ignored", "changed", "unreadable"])
async def test_selected_update_set_is_irrelevant_to_flow_success(settings, selected):
    model, _, tools = setup(settings)
    preview = decode_response(await tools["flow_preview"](**create_request()))
    model.context = {"scope": "other-selected-application", "update_set": selected}
    result = await apply(tools, preview)
    assert result["status"] == "success", result
    assert "capture" not in result["data"]
    assert all(
        "sys_update_" not in path and "concoursepicker" not in path
        for _, path in model.calls
    )


@pytest.mark.parametrize("complete", [False, True])
async def test_creation_scope_mismatch_still_blocks_before_reads(settings, complete):
    model, _, tools = setup(settings)
    args = create_request()
    args["expected_scope"] = "1" * 32
    if not complete:
        args.pop("edits")
    result = decode_response(await tools["flow_preview"](**args))
    assert result["error"]["code"] == "CONTEXT_MISMATCH"
    assert not model.calls and not model.bodies
