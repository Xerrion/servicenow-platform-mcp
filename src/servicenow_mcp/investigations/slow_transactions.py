"""Investigation: find slow transactions via ServiceNow performance pattern tables."""

from datetime import UTC, datetime, timedelta
from typing import Any

from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields
from servicenow_mcp.utils import validate_identifier

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
    try:
        hours = max(0, int(params.get("hours", 24)))
    except (TypeError, ValueError):
        hours = 24
    limit = params.get("limit", 20)
    categories_filter = params.get("categories")
    allowed_categories: set[str] | None = None
    if categories_filter:
        allowed_categories = {c.strip() for c in categories_filter.split(",")}

    cutoff = datetime.now(tz=UTC) - timedelta(hours=hours)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    findings: list[dict[str, Any]] = []

    for table_name, category in PERFORMANCE_TABLES:
        if allowed_categories and category not in allowed_categories:
            continue

        # Pattern tables use window queries; syslog_cancellation uses time-bounded query
        if table_name == "syslog_cancellation":
            query = f"sys_created_on>={cutoff_str}"
        else:
            query = "window_endISEMPTY^window_startISEMPTY"

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

    return {
        "investigation": "slow_transactions",
        "finding_count": len(findings),
        "findings": findings,
        "params": {"hours": hours, "limit": limit, "categories": categories_filter},
        "tables_queried": [t[0] for t in PERFORMANCE_TABLES],
    }


async def explain(client: ServiceNowClient, element_id: str) -> dict[str, Any]:
    """Provide rich context for a slow transaction finding.

    element_id format: "table:sys_id".
    """
    table, sys_id = element_id.split(":", 1)
    if table not in _ALLOWED_TABLES:
        return {
            "element": element_id,
            "error": f"Table '{table}' is not in the allowed tables for this investigation",
        }
    validate_identifier(sys_id)
    check_table_access(table)
    record = mask_sensitive_fields(await client.get_record(table, sys_id))

    explanation_parts = [
        f"Performance pattern from '{table}'.",
        f"Name: {record.get('name', 'N/A')}.",
    ]

    if record.get("count"):
        explanation_parts.append(f"Hit count: {record['count']}.")

    explanation_parts.append(
        "Review this pattern to determine if query optimization, "
        "script optimization, or architecture changes are needed."
    )

    return {
        "element": element_id,
        "explanation": " ".join(explanation_parts),
        "record": record,
    }
