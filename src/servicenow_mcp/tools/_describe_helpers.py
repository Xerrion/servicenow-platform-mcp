"""Shared helpers for the ``describe`` tool family.

``tools/describe.py:describe`` projects sys_dictionary rows into slim/verbose
shapes. The projection helpers live here so the tool module stays focused on
registration and dispatch.
"""

import collections
import logging
from typing import Any

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.policy import INTERNAL_QUERY_LIMIT
from servicenow_mcp.utils import ServiceNowQuery


logger = logging.getLogger(__name__)


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


async def _fetch_choice_counts(
    client: ServiceNowClient,
    table: str,
    warnings: list[str],
) -> dict[str, int]:
    """Fetch sys_choice records for ``table`` and return per-field counts.

    Non-fatal: any exception (typically an ACL block on sys_choice in
    restricted instances) results in an empty dict and a warning appended to
    ``warnings`` in place. The truncation warning is appended when the row
    count meets the internal query limit.
    """
    try:
        choices_resp = await client.query_records(
            "sys_choice",
            ServiceNowQuery().equals("name", table).build(),
            fields=["element"],
            limit=INTERNAL_QUERY_LIMIT,
        )
        choice_records = choices_resp.get("records", [])
        counts = dict(collections.Counter(c.get("element", "") for c in choice_records if c.get("element")))
        if len(choice_records) >= INTERNAL_QUERY_LIMIT:
            warnings.append(f"sys_choice records may be truncated at {INTERNAL_QUERY_LIMIT} entries")
    except Exception:
        logger.warning("sys_choice fetch failed for table %s; choice_count will be 0", table)
        warnings.append("Could not fetch sys_choice; choice_count is 0 for all fields")
        return {}
    return counts


async def _fetch_documentation(
    client: ServiceNowClient,
    table: str,
    warnings: list[str],
) -> dict[str, dict[str, Any]]:
    """Fetch sys_documentation rows for ``table`` keyed by element name.

    Always issues the query (the caller decides whether to invoke this helper
    via the ``include_docs`` flag). Appends a truncation warning to
    ``warnings`` in place when the row count meets the 500-row cap.
    """
    docs_result = await client.query_records(
        "sys_documentation",
        ServiceNowQuery().equals("name", table).build(),
        fields=["element", "label", "help", "hint", "url"],
        limit=500,
    )
    records = docs_result.get("records", [])
    if len(records) >= 500:
        warnings.append("Documentation records may be truncated at 500 entries")
    return {d["element"]: d for d in records if d.get("element")}


def _apply_fields_filter(
    field_list: list[dict[str, Any]],
    requested_fields: list[str],
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Restrict ``field_list`` to ``requested_fields`` and warn on unknown names.

    Preserves left-to-right order of ``requested_fields`` when collecting the
    ``unknown`` list so the warning text is stable across runs. The returned
    list keeps the original ``field_list`` ordering for matched fields.
    """
    wanted = set(requested_fields)
    present = {str(f.get("name") or f.get("element") or "") for f in field_list}
    unknown = [name for name in requested_fields if name not in present]
    filtered = [f for f in field_list if str(f.get("name") or f.get("element") or "") in wanted]
    if unknown:
        warnings.append(f"Unknown field(s): {','.join(unknown)}")
    return filtered


async def _describe_impl(
    table: str,
    *,
    verbose: bool,
    include_docs: bool,
    requested_fields: list[str],
    settings: Settings,
    auth_provider: BasicAuthProvider,
    client_factory: ServiceNowClientProvider,
) -> tuple[dict[str, Any], list[str]]:
    """Run the post-validation orchestration for the ``describe`` tool.

    Opens its own ``ServiceNowClient``, fetches sys_dictionary metadata, the
    ``sys_db_object`` row, choice counts, and optional documentation, then
    assembles the response ``data`` dict. Warnings are collected in the order
    callers expect: choice warnings -> documentation warnings -> unknown-field
    warnings. Returns ``(data, warnings)`` for the caller to feed straight into
    ``format_response``.
    """
    warnings: list[str] = []
    async with client_factory() as client:
        metadata = await client.get_metadata(table)
        table_meta = await client.query_records(
            "sys_db_object",
            ServiceNowQuery().equals("name", table).build(),
            fields=["label", "super_class", "is_extendable", "number_ref", "sys_id"],
            limit=1,
        )
        table_info = table_meta.get("records", [{}])[0] if table_meta.get("records") else {}
        choice_counts = await _fetch_choice_counts(client, table, warnings)
        docs: dict[str, dict[str, Any]] = {}
        if include_docs:
            docs = await _fetch_documentation(client, table, warnings)

    field_list = (
        _build_verbose_field_list(metadata, choice_counts)
        if verbose
        else _build_slim_field_list(metadata, choice_counts)
    )
    if requested_fields:
        field_list = _apply_fields_filter(field_list, requested_fields, warnings)

    data: dict[str, Any] = {
        "table": table_info,
        "fields": field_list,
        "field_count": len(field_list),
    }
    if include_docs:
        data["documentation"] = docs
    return data, warnings
