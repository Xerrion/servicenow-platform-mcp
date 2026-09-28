"""Owned contract models dump to the exact legacy dict shapes."""

from servicenow_mcp.response import format_response
from servicenow_mcp.tools._flow_models import ActionTypeRef, FlowNode, V2Trigger
from servicenow_mcp.tools._record_write_models import DiffEntry, WritePreview, WritePreviewResult, WriteResult


def test_write_models_match_legacy_bytes() -> None:
    nested = {"a": [1, {"b": None}], "n": 0}
    cases = [
        (
            WritePreviewResult(action="create", table="t", preview_token="k", preview=WritePreview(data=nested)),
            {"action": "create", "table": "t", "preview_token": "k", "preview": {"data": nested}},
        ),
        (
            WritePreviewResult(
                action="update",
                table="t",
                sys_id="s",
                preview_token="k",
                preview=WritePreview(diff={"f": DiffEntry(old="", new=None)}),
            ),
            {
                "action": "update",
                "table": "t",
                "sys_id": "s",
                "preview_token": "k",
                "preview": {"diff": {"f": {"old": "", "new": None}}},
            },
        ),
        (
            WritePreviewResult(
                action="delete", table="t", sys_id="s", preview_token="k", preview=WritePreview(record_snapshot={})
            ),
            {"action": "delete", "table": "t", "sys_id": "s", "preview_token": "k", "preview": {"record_snapshot": {}}},
        ),
        (
            WriteResult(action="create", table="t", sys_id=None, record=nested),
            {"action": "create", "table": "t", "sys_id": None, "record": nested},
        ),
        (
            WriteResult(action="delete", table="t", sys_id="s", deleted=True),
            {"action": "delete", "table": "t", "sys_id": "s", "deleted": True},
        ),
    ]
    for model, legacy in cases:
        assert format_response(data=model.to_payload()) == format_response(data=legacy)


def test_flow_node_and_trigger_omit_absent_optional_keys() -> None:
    base: dict[str, object] = {
        "kind": "logic",
        "version": "v2",
        "sys_id": "s",
        "ui_uuid": "u",
        "parent_ui_id": "",
        "order": "1",
        "label": "l",
        "name": "n",
        "comment": "",
        "values_decoded": None,
        "children": [],
    }
    assert list(FlowNode.model_validate(base).to_payload()) == [*base]
    node = FlowNode.model_validate({**base, "kind": "action", "action_type": ActionTypeRef(sys_id="a", name="b")})
    assert node.to_payload()["action_type"] == {"sys_id": "a", "name": "b"}
    trig = V2Trigger(
        sys_id="s", type="t", active=True, table="x", remote_trigger_id="", condition="", values_decoded=None
    )
    assert "decode_error" not in trig.to_payload()


def test_investigation_models_match_legacy_shapes() -> None:
    from servicenow_mcp.investigations._models import ParamSpec, RecordFinding, param_specs

    assert param_specs(t=ParamSpec(type="str", required=True, default=None, description="d")) == {
        "t": {"type": "str", "required": True, "default": None, "description": "d"}
    }
    assert param_specs(h=ParamSpec(type="int", default=None, description="d")) == {
        "h": {"type": "int", "default": None, "description": "d"}
    }
    assert RecordFinding(category="c", element_id="e", name="n", detail="d").to_payload() == {
        "category": "c",
        "element_id": "e",
        "name": "n",
        "detail": "d",
    }
