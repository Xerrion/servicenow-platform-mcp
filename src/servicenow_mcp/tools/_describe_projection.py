"""Project normalized dictionary metadata into describe response fields."""

from typing import Any

from servicenow_mcp.tools._dictionary import DictionaryField


DESCRIBE_NOISE_FIELDS: frozenset[str] = frozenset(
    {
        "calculation",
        "default_value",
        "dynamic_default_value",
        "sys_scope",
        "sys_package",
        "sys_update_name",
        "sys_class_name",
        "sys_id",
        "sys_created_on",
        "sys_created_by",
        "sys_updated_on",
        "sys_updated_by",
        "sys_mod_count",
        "sys_customer_update",
        "sys_replace_on_upgrade",
        "sys_policy",
        "audit",
        "active",
        "function_definition",
        "function_field",
        "calculation_type",
        "use_dynamic_default",
        "use_reference_qualifier",
        "reference_qual",
        "reference_qual_condition",
        "dynamic_creation",
        "dynamic_creation_script",
        "attributes",
        "element_reference",
        "primary",
        "spell_check",
        "sizeclass",
    }
)
_SLIM_METADATA_FIELDS: tuple[str, ...] = (
    "element",
    "column_label",
    "internal_type",
    "max_length",
    "mandatory",
    "read_only",
    "reference",
)


def parse_fields_filter(fields: str) -> list[str]:
    """Parse a comma-separated describe field filter."""
    return [field.strip() for field in fields.split(",") if field.strip()]


def select_dictionary_fields(
    fields: list[DictionaryField],
    requested_fields: list[str],
    field_offset: int,
    field_limit: int,
) -> list[DictionaryField]:
    """Select requested fields or a bounded sorted page."""
    ordered = sorted(fields, key=lambda item: item.name)
    if requested_fields:
        wanted = set(requested_fields)
        return [field for field in ordered if field.name in wanted]
    if field_limit >= 1_000_000:
        return ordered
    return ordered[field_offset : field_offset + field_limit]


def project_fields(
    fields: list[DictionaryField],
    choice_counts: dict[str, int],
    *,
    verbose: bool,
) -> list[dict[str, Any]]:
    """Project dictionary fields into the public slim or verbose shape."""
    metadata = [_metadata_row(field, verbose=verbose) for field in fields]
    if verbose:
        return [_verbose_field(row, choice_counts) for row in metadata]
    return [_slim_field(row, choice_counts) for row in metadata]


def filter_projected_fields(
    fields: list[dict[str, Any]],
    requested_fields: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Filter projected fields and return unknown requested names."""
    wanted = set(requested_fields)
    present = {_field_name(field) for field in fields}
    unknown = [name for name in requested_fields if name not in present]
    return [field for field in fields if _field_name(field) in wanted], unknown


def build_selection(
    fields: list[dict[str, Any]],
    requested_fields: list[str],
    field_offset: int,
    field_limit: int,
    total_field_count: int,
) -> dict[str, Any]:
    """Build describe selection metadata without changing its response contract."""
    returned_fields = [_field_name(field) for field in fields]
    if requested_fields:
        return {
            "mode": "explicit",
            "requested_fields": requested_fields,
            "returned_fields": returned_fields,
            "omitted_count": len(requested_fields) - len(fields),
            "truncated": False,
        }

    is_all_fields = field_limit >= 1_000_000
    end = field_offset + len(fields)
    return {
        "mode": "all" if is_all_fields else "compact",
        "requested_fields": None,
        "returned_fields": returned_fields,
        "omitted_count": max(total_field_count - len(fields), 0),
        "truncated": not is_all_fields and end < total_field_count,
        "next_offset": end if not is_all_fields and end < total_field_count else None,
    }


def _metadata_row(field: DictionaryField, *, verbose: bool) -> dict[str, Any]:
    row = dict(field.metadata)
    row["element"] = field.name
    row["internal_type"] = field.internal_type
    row["attributes"] = field.attributes
    row["inherited_from"] = field.inherited_from
    if verbose:
        return row
    return {key: row.get(key) for key in _SLIM_METADATA_FIELDS} | {"inherited_from": field.inherited_from}


def _slim_field(column: dict[str, Any], choice_counts: dict[str, int]) -> dict[str, Any]:
    name = str(column.get("element", "") or "")
    return {
        "name": name,
        "label": str(column.get("column_label", "") or "") or name,
        "type": _reference_value(column.get("internal_type", "")),
        "max_length": _integer_value(column.get("max_length")),
        "mandatory": _boolean_value(column.get("mandatory")),
        "read_only": _boolean_value(column.get("read_only")),
        "reference_table": _reference_value(column.get("reference", "")),
        "choice_count": int(choice_counts.get(name, 0)),
        "inherited_from": column.get("inherited_from"),
    }


def _verbose_field(column: dict[str, Any], choice_counts: dict[str, int]) -> dict[str, Any]:
    cleaned = {key: value for key, value in column.items() if key not in DESCRIBE_NOISE_FIELDS}
    cleaned["choice_count"] = int(choice_counts.get(str(column.get("element", "") or ""), 0))
    cleaned["inherited_from"] = column.get("inherited_from")
    return cleaned


def _reference_value(raw: Any) -> str:
    if isinstance(raw, dict):
        value = raw.get("value", "")
        return str(value) if value is not None else ""
    return "" if raw is None else str(raw)


def _boolean_value(raw: Any) -> bool:
    return raw if isinstance(raw, bool) else str(raw).strip().lower() == "true"


def _integer_value(raw: Any) -> int:
    if raw is None or raw == "":
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _field_name(field: dict[str, Any]) -> str:
    return str(field.get("name") or field.get("element") or "")
