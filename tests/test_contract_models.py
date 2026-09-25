"""Owned contract models dump to the exact legacy dict shapes."""

from servicenow_mcp.response import format_response
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
