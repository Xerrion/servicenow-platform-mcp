"""Investigation: analyze and cluster syslog errors."""

from collections import defaultdict
from typing import Any

from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields
from servicenow_mcp.utils import ServiceNowQuery, sanitize_query_value


async def run(client: ServiceNowClient, params: dict[str, Any]) -> dict[str, Any]:
    """Analyze syslog errors and cluster them by source.

    Queries syslog for level=0 (error) entries, groups them by the `source` field,
    and computes frequency, first/last seen, and sample messages.

    Params:
        hours: How many hours back to look (default 24).
        source: Optional source filter.
        limit: Maximum log entries to fetch (default 100).
    """
    try:
        hours = max(0, int(params.get("hours", 24)))
    except (TypeError, ValueError):
        hours = 24
    source_filter = params.get("source")
    limit = params.get("limit", 100)

    q = ServiceNowQuery().equals("level", "0").hours_ago("sys_created_on", hours)
    if source_filter:
        q.like("source", sanitize_query_value(source_filter))

    syslog_result = await client.query_records(
        "syslog",
        q.build(),
        fields=["sys_id", "message", "source", "level", "sys_created_on"],
        limit=limit,
        order_by="sys_created_on",
    )
    logs = [mask_sensitive_fields(r) for r in syslog_result["records"]]

    # Cluster by source
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for log in logs:
        clusters[log.get("source", "unknown")].append(log)

    # Build findings sorted by frequency (descending)
    findings: list[dict[str, Any]] = []
    for src, entries in sorted(clusters.items(), key=lambda x: -len(x[1])):
        timestamps = [e.get("sys_created_on", "") for e in entries]
        sample_messages = [e.get("message", "") for e in entries[:3]]
        findings.append(
            {
                "category": "error_cluster",
                "source": src,
                "frequency": len(entries),
                "first_seen": min(timestamps) if timestamps else "",
                "last_seen": max(timestamps) if timestamps else "",
                "sample_messages": sample_messages,
                "element_id": f"syslog:{entries[0].get('sys_id', '')}",
            }
        )

    return {
        "investigation": "error_analysis",
        "finding_count": len(findings),
        "findings": findings,
        "params": {"hours": hours, "source": source_filter, "limit": limit},
        "total_errors": len(logs),
    }


async def explain(client: ServiceNowClient, element_id: str) -> dict[str, Any]:
    """Provide rich context for an error analysis finding.

    element_id format: "syslog:sys_id".
    """
    table, sys_id = element_id.split(":", 1)
    if table != "syslog":
        return {
            "element": element_id,
            "error": f"Table '{table}' is not allowed for this investigation; expected 'syslog'",
        }
    check_table_access("syslog")
    record = mask_sensitive_fields(await client.get_record(table, sys_id))

    source = record.get("source", "")
    explanation_parts = [
        f"Syslog error from source '{source}'.",
        f"Message: {record.get('message', '')}",
        f"Logged at: {record.get('sys_created_on', '')}.",
        "Check the source script or process for the root cause of this error.",
    ]

    return {
        "element": element_id,
        "explanation": " ".join(explanation_parts),
        "record": record,
    }
