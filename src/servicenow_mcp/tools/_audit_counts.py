"""Fetch bounded, grouped audit counts without one scan per requested field."""

from typing import Any

from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.errors import ServiceNowMCPError
from servicenow_mcp.query_builder import ServiceNowQuery


def _parse_field_counts(result: Any, fields: list[str]) -> dict[str, int]:
    """Reject malformed grouped counts instead of treating them as inactivity."""
    if not isinstance(result, list):
        raise ServiceNowMCPError("sys_audit Stats API returned a malformed grouped response; expected a list.")

    counts = dict.fromkeys(fields, 0)
    seen: set[str] = set()
    for row in result:
        groups = row.get("groupby_fields") if isinstance(row, dict) else None
        if (
            not isinstance(groups, list)
            or len(groups) != 1
            or not isinstance(groups[0], dict)
            or groups[0].get("field") != "fieldname"
        ):
            raise ServiceNowMCPError("sys_audit Stats API returned an invalid fieldname group; counts are unknown.")
        name = groups[0].get("value")
        if not isinstance(name, str) or name not in counts or name in seen:
            raise ServiceNowMCPError("sys_audit Stats API returned an unexpected or duplicate fieldname group.")
        stats = row.get("stats")
        raw_count = stats.get("count") if isinstance(stats, dict) else None
        if isinstance(raw_count, str) and raw_count.isascii() and raw_count.isdecimal():
            raw_count = int(raw_count)
        if type(raw_count) is not int or raw_count < 0:
            raise ServiceNowMCPError("sys_audit Stats API returned an invalid stats.count; field counts are unknown.")
        seen.add(name)
        counts[name] = raw_count
    return counts


async def fetch_field_counts(
    client: ServiceNowClient,
    *,
    table: str,
    fields: list[str],
    since: str,
) -> dict[str, int]:
    """Count requested audit fields in one live, date-bounded Stats API request.

    The caller supplies at most 50 validated, dictionary-confirmed field names.
    Missing groups have zero matching rows; malformed responses raise an error.
    """
    if not fields:
        return {}
    query = (
        ServiceNowQuery()
        .equals("tablename", table)
        .in_list("fieldname", fields)
        .greater_or_equal("sys_created_on", since)
        .build()
    )
    result = await client.aggregate("sys_audit", query, group_by="fieldname")
    return _parse_field_counts(result, fields)
