"""Shared helpers for investigation modules."""

from collections.abc import Callable
from typing import Any

from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields
from servicenow_mcp.utils import validate_identifier


def parse_int_param(params: dict[str, Any], key: str, default: int) -> int:
    """Parse an integer parameter from a dict with a fallback default.

    Args:
        params: Parameter dictionary (e.g. from investigation run()).
        key: Key to look up.
        default: Fallback value if key is missing or not an integer.
    """
    try:
        return int(params.get(key, default))
    except (TypeError, ValueError):
        return default


def parse_element_id(
    element_id: str,
    allowed_tables: set[str] | None = None,
) -> tuple[str, str]:
    """Parse a 'table:sys_id' element ID into its components.

    Args:
        element_id: String in 'table:sys_id' format.
        allowed_tables: Optional set of permitted table names.

    Raises:
        ValueError: If format is invalid or table is not in the allowed set.
    """
    if ":" not in element_id:
        raise ValueError(f"Invalid element_id format: expected 'table:sys_id', got '{element_id}'")
    table, sys_id = element_id.split(":", 1)
    if allowed_tables is not None and table not in allowed_tables:
        raise ValueError(f"Table '{table}' is not in the allowed tables: {allowed_tables}")
    return table, sys_id


def build_investigation_result(
    name: str,
    findings: list[dict[str, Any]],
    **extra: Any,
) -> dict[str, Any]:
    """Build a standard investigation result envelope.

    Args:
        name: Investigation name (e.g. 'stale_automations').
        findings: List of finding dicts.
        **extra: Additional keys to include in the envelope.
    """
    provenance = {"investigation": name}
    for finding in findings:
        finding["provenance"] = provenance.copy()
    return {
        "investigation": name,
        "provenance": provenance,
        "finding_count": len(findings),
        "findings": findings,
        **extra,
    }


async def fetch_and_explain(
    client: ServiceNowClient,
    element_id: str,
    allowed_tables: set[str] | None,
    build_explanation: Callable[[str, str, dict[str, Any]], list[str]],
) -> dict[str, Any]:
    """Standard explain() implementation: parse ID, fetch record, build explanation.

    Args:
        client: An active ServiceNowClient instance.
        element_id: String in 'table:sys_id' format.
        allowed_tables: Optional set of permitted table names.
        build_explanation: Callback that receives (table, sys_id, masked_record)
            and returns a list of explanation parts to be joined with spaces.
    """
    table, sys_id = parse_element_id(element_id, allowed_tables)
    validate_identifier(sys_id)
    check_table_access(table)
    record = mask_sensitive_fields(await client.get_record(table, sys_id))
    explanation_parts = build_explanation(table, sys_id, record)
    return {
        "element": element_id,
        "explanation": " ".join(explanation_parts),
        "record": record,
    }
