"""Investigation: analyze and cluster syslog errors."""

from collections import defaultdict
from typing import Any

from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.investigation_helpers import (
    build_investigation_result,
    fetch_and_explain,
    parse_int_param,
)
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields
from servicenow_mcp.utils import ServiceNowQuery


async def run(client: ServiceNowClient, params: dict[str, Any]) -> dict[str, Any]:
    """Analyze syslog errors and cluster them by source.

    Queries syslog for level=0 (error) entries, groups them by the `source` field,
    and computes frequency, first/last seen, and sample messages.

    Params:
        hours: How many hours back to look (default 24).
        source: Optional source filter.
        limit: Maximum log entries to fetch (default 100).
    """
    hours = max(1, parse_int_param(params, "hours", 24))
    source_filter = params.get("source")
    limit = parse_int_param(params, "limit", 100)

    check_table_access("syslog")

    q = ServiceNowQuery().equals("level", "0").hours_ago("sys_created_on", hours)
    if source_filter:
        q.like("source", source_filter)
    q.order_by("sys_created_on", descending=True)

    syslog_result = await client.query_records(
        "syslog",
        q.build(),
        fields=["sys_id", "message", "source", "level", "sys_created_on"],
        limit=limit,
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

    return build_investigation_result(
        "error_analysis",
        findings,
        params={"hours": hours, "source": source_filter, "limit": limit},
        total_errors=len(logs),
    )


def _build_explanation(_table: str, _sys_id: str, record: dict[str, Any]) -> list[str]:
    """Build explanation parts for a syslog error record."""
    source = record.get("source", "")
    return [
        f"Syslog error from source '{source}'.",
        f"Message: {record.get('message', '')}",
        f"Logged at: {record.get('sys_created_on', '')}.",
        "Check the source script or process for the root cause of this error.",
    ]


async def explain(client: ServiceNowClient, element_id: str) -> dict[str, Any]:
    """Provide rich context for an error analysis finding.

    element_id format: "syslog:sys_id".
    """
    try:
        return await fetch_and_explain(client, element_id, {"syslog"}, _build_explanation)
    except ValueError as e:
        return {"error": str(e)}
