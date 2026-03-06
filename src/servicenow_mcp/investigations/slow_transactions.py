"""Investigation: find slow transactions via ServiceNow performance pattern tables."""

from typing import Any

from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.investigation_helpers import (
    build_investigation_result,
    fetch_and_explain,
    parse_int_param,
)
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields
from servicenow_mcp.utils import ServiceNowQuery


# ServiceNow performance pattern tables and their finding categories
PERFORMANCE_TABLES = [
    ("sys_query_pattern", "slow_query"),
    ("sys_transaction_pattern", "slow_transaction"),
    ("sys_script_pattern", "slow_script"),
    ("sys_mutex_pattern", "mutex_contention"),
    ("sysevent_pattern", "event_pattern"),
    ("sys_interaction_pattern", "slow_interaction"),
    ("syslog_cancellation", "cancelled_transaction"),
]

_ALLOWED_TABLES = {t[0] for t in PERFORMANCE_TABLES}


async def run(client: ServiceNowClient, params: dict[str, Any]) -> dict[str, Any]:
    """Find slow transactions by querying ServiceNow performance pattern tables.

    Queries 7 performance-related tables for active patterns. Pattern tables use
    window_endISEMPTY^window_startISEMPTY to find currently-tracked patterns.

    Params:
        hours: Lookback period in hours (default 24). Used for syslog_cancellation.
        limit: Maximum findings per table (default 20).
        categories: Optional comma-separated list of categories to filter.
    """
    hours = max(1, parse_int_param(params, "hours", 24))
    limit = parse_int_param(params, "limit", 20)
    categories_filter = params.get("categories")
    allowed_categories: set[str] | None = None
    if categories_filter:
        allowed_categories = {c.strip() for c in categories_filter.split(",")}

    findings: list[dict[str, Any]] = []

    for table_name, category in PERFORMANCE_TABLES:
        if allowed_categories and category not in allowed_categories:
            continue

        # Pattern tables use window queries; syslog_cancellation uses time-bounded query
        check_table_access(table_name)
        if table_name == "syslog_cancellation":
            query = ServiceNowQuery().hours_ago("sys_created_on", hours).build()
        else:
            query = (
                ServiceNowQuery()
                .is_empty("window_end")
                .is_empty("window_start")
                .hours_ago("sys_created_on", hours)
                .build()
            )

        try:
            result = await client.query_records(
                table_name,
                query,
                limit=limit,
            )
            for rec in result["records"]:
                masked_rec = mask_sensitive_fields(rec)
                findings.append(
                    {
                        "category": category,
                        "table": table_name,
                        "element_id": f"{table_name}:{masked_rec.get('sys_id', '')}",
                        "name": masked_rec.get("name", masked_rec.get("sys_id", "")),
                        "count": masked_rec.get("count", ""),
                        "detail": f"Performance pattern from {table_name}",
                        "sys_created_on": masked_rec.get("sys_created_on", ""),
                    }
                )
        except Exception:
            # Table may not exist or be inaccessible; skip
            continue

    return build_investigation_result(
        "slow_transactions",
        findings,
        params={
            "hours": hours,
            "limit": limit,
            "categories": categories_filter,
        },
        tables_queried=[t[0] for t in PERFORMANCE_TABLES],
    )


def _build_explanation(table: str, _sys_id: str, record: dict[str, Any]) -> list[str]:
    """Build explanation parts for a slow transaction finding."""
    explanation_parts = [
        f"Performance pattern from '{table}'.",
        f"Name: {record.get('name', 'N/A')}.",
    ]

    if record.get("count"):
        explanation_parts.append(f"Hit count: {record['count']}.")

    explanation_parts.append(
        (  # noqa: UP034
            "Review this pattern to determine if query optimization, "
            "script optimization, or architecture changes are needed."
        )
    )

    return explanation_parts


async def explain(client: ServiceNowClient, element_id: str) -> dict[str, Any]:
    """Provide rich context for a slow transaction finding.

    element_id format: "table:sys_id".
    """
    try:
        return await fetch_and_explain(client, element_id, _ALLOWED_TABLES, _build_explanation)
    except ValueError as e:
        return {"error": str(e)}
