"""Shared helpers for the ``describe`` tool family.

``tools/describe.py:describe`` projects sys_dictionary rows into slim/verbose
shapes. The projection helpers live here so the tool module stays focused on
registration and dispatch.
"""

from typing import Any


# Keys stripped from each sys_dictionary row when verbose=True. These are either
# noisy/system-managed (sys_*), high-volume script bodies (calculation,
# dynamic_default_value), or rarely-useful flags. The slim default shape never
# includes them either.
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


def _ref_value(raw: Any) -> str:
    """Extract a ServiceNow reference field value.

    sys_dictionary returns reference-typed fields either as a plain string or as
    ``{"value": "...", "display_value": "..."}``. Normalize to the bare string.
    """
    if isinstance(raw, dict):
        value = raw.get("value", "")
        return str(value) if value is not None else ""
    if raw is None:
        return ""
    return str(raw)


def _bool_value(raw: Any) -> bool:
    """ServiceNow booleans arrive as the strings 'true'/'false'. Anything else is False."""
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() == "true"


def _int_value(raw: Any) -> int:
    """Coerce a sys_dictionary length field to int; return 0 when missing or non-numeric."""
    if raw is None or raw == "":
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _build_slim_field_list(
    columns: list[dict[str, Any]],
    choice_counts: dict[str, int],
) -> list[dict[str, Any]]:
    """Project sys_dictionary rows into the 8-key slim shape used by describe."""
    fields: list[dict[str, Any]] = []
    for col in columns:
        name = str(col.get("element", "") or "")
        label = str(col.get("column_label", "") or "") or name
        fields.append(
            {
                "name": name,
                "label": label,
                "type": _ref_value(col.get("internal_type", "")),
                "max_length": _int_value(col.get("max_length")),
                "mandatory": _bool_value(col.get("mandatory")),
                "read_only": _bool_value(col.get("read_only")),
                "reference_table": _ref_value(col.get("reference", "")),
                "choice_count": int(choice_counts.get(name, 0)),
            }
        )
    return fields


def _build_verbose_field_list(
    columns: list[dict[str, Any]],
    choice_counts: dict[str, int],
) -> list[dict[str, Any]]:
    """Return each sys_dictionary row minus the deny-list, with choice_count merged in."""
    fields: list[dict[str, Any]] = []
    for col in columns:
        cleaned = {k: v for k, v in col.items() if k not in DESCRIBE_NOISE_FIELDS}
        name = str(col.get("element", "") or "")
        cleaned["choice_count"] = int(choice_counts.get(name, 0))
        fields.append(cleaned)
    return fields


def _parse_fields_filter(fields: str) -> list[str]:
    """Split a comma-separated fields argument into a clean list (whitespace-tolerant)."""
    if not fields:
        return []
    return [f.strip() for f in fields.split(",") if f.strip()]
