"""Mask catalog secrets and common personal fields, including variable envelopes."""

import re
from typing import Any

from servicenow_mcp.policy import MASK_VALUE, is_sensitive_field

_PERSONAL = re.compile(
    r"email|phone|address|street|postal|zip_code|first_name|last_name|full_name|invalidUsers|requested_for_user",
    re.IGNORECASE,
)
_SECRET_KEY_LABEL = re.compile(r"(?:api|private)[\s_-]*key", re.IGNORECASE)
_ANSWER_FIELDS = frozenset({"value", "displayvalue", "display_value", "default_value"})


def _private_name(name: str) -> bool:
    return is_sensitive_field(name) or bool(
        _PERSONAL.search(name) or _SECRET_KEY_LABEL.search(name)
    )


def _private_definition(value: dict[str, Any]) -> bool:
    return any(
        isinstance(metadata, str) and _private_name(metadata)
        for metadata in (value.get("name"), value.get("label"))
    )


def _variable_metadata(definitions: Any) -> dict[str, bool]:
    """Collect nested definitions. Duplicate names cannot downgrade sensitive metadata."""
    metadata: dict[str, bool] = {}
    pending = [definitions]
    while pending:
        definition = pending.pop()
        if isinstance(definition, list):
            pending.extend(definition)
        elif isinstance(definition, dict):
            name, label = definition.get("name"), definition.get("label")
            if isinstance(name, str) and isinstance(label, str) and label.strip():
                metadata[name] = metadata.get(name, False) or _private_definition(
                    definition
                )
            pending.extend(
                item
                for key, item in definition.items()
                if key not in _ANSWER_FIELDS and isinstance(item, dict | list)
            )
    return metadata


def mask_catalog(
    value: Any,
    *,
    definitions: Any = None,
    definitions_by_item: dict[str, Any] | None = None,
) -> Any:
    """Return a masked copy. Variable maps require metadata to expose an answer."""
    return _mask_catalog(
        value,
        _variable_metadata(definitions),
        {
            item_id: _variable_metadata(item_definitions)
            for item_id, item_definitions in (definitions_by_item or {}).items()
        },
    )


def _mask_catalog(
    value: Any, metadata: dict[str, bool], item_metadata: dict[str, dict[str, bool]]
) -> Any:
    if isinstance(value, list):
        return [_mask_catalog(item, metadata, item_metadata) for item in value]
    if not isinstance(value, dict):
        return value
    item_id = value.get("catalog_item_id") or value.get("item_id")
    if not item_id and "variables" in value:
        item_id = value.get("sys_id")
    if isinstance(item_id, str):
        metadata = item_metadata.get(item_id, {})
    local_definitions = value.get("definitions")
    if isinstance(value.get("variables"), list):
        # Response envelopes can prove sensitivity, but cannot downgrade observed labels.
        local_definitions = [local_definitions, value["variables"]]
    local_metadata = _variable_metadata(local_definitions)
    metadata = {
        **metadata,
        **{
            name: metadata.get(name, False) or private
            for name, private in local_metadata.items()
        },
    }
    private_variable = _private_definition(value)
    masked: dict[str, Any] = {}
    for key, item in value.items():
        if key.casefold() == "invalidusers" and isinstance(item, list):
            # Cardinality is the validation outcome. Only the names are private.
            masked[key] = [MASK_VALUE] * len(item)
        elif (
            _private_name(key)
            or metadata.get(key) is True
            or (private_variable and key in _ANSWER_FIELDS)
        ):
            masked[key] = MASK_VALUE
        elif key == "variables" and isinstance(item, dict):
            masked[key] = {
                name: MASK_VALUE
                if _private_name(name) or metadata.get(name) is not False
                else _mask_catalog(answer, metadata, item_metadata)
                for name, answer in item.items()
            }
        elif key == "items":
            # A cart can contain unrelated items with the same variable names.
            masked[key] = _mask_catalog(item, {}, item_metadata)
        else:
            masked[key] = _mask_catalog(item, metadata, item_metadata)
    return masked
