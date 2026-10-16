"""Owned contract models preserve optional fields and native values."""

from servicenow_mcp.capabilities.flows._flow_models import (
    ActionTypeRef,
    FlowNode,
    V2Trigger,
)
from servicenow_mcp.capabilities.mutations.models import MutationPreview
from servicenow_mcp.capabilities.records.models import DiffEntry


def test_write_model_native_values() -> None:
    nested = {"a": [1, {"b": None}], "n": 0}
    assert DiffEntry(old=nested, new=None).to_payload() == {"old": nested, "new": None}
    model = MutationPreview(
        family="records",
        action="create",
        preview_token="k",
        expires_in_seconds=300,
        summary={"data": nested},
        limitations=[],
    )
    assert model.model_dump(mode="json")["summary"] == {"data": nested}


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
    node = FlowNode.model_validate(
        {**base, "kind": "action", "action_type": ActionTypeRef(sys_id="a", name="b")}
    )
    assert node.to_payload()["action_type"] == {"sys_id": "a", "name": "b"}
    trig = V2Trigger(
        sys_id="s",
        type="t",
        active=True,
        table="x",
        remote_trigger_id="",
        condition="",
        values_decoded=None,
    )
    assert "decode_error" not in trig.to_payload()


def test_investigation_model_fields() -> None:
    from servicenow_mcp.capabilities.investigations.checks._models import (
        ParamSpec,
        RecordFinding,
        param_specs,
    )

    assert param_specs(
        t=ParamSpec(type="str", required=True, default=None, description="d")
    ) == {
        "t": {"type": "str", "required": True, "default": None, "description": "d"},
    }
    assert param_specs(h=ParamSpec(type="int", default=None, description="d")) == {
        "h": {"type": "int", "default": None, "description": "d"},
    }
    assert RecordFinding(
        category="c", element_id="e", name="n", detail="d"
    ).to_payload() == {
        "category": "c",
        "element_id": "e",
        "name": "n",
        "detail": "d",
    }
