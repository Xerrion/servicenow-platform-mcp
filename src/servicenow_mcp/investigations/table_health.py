"""Investigation: comprehensive health report for a single table."""

import asyncio
from typing import Any

from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.investigation_helpers import build_investigation_result
from servicenow_mcp.policy import (
    INTERNAL_QUERY_LIMIT,
    check_table_access,
    mask_sensitive_fields,
)
from servicenow_mcp.utils import ServiceNowQuery, validate_identifier


# ``sys_security_acl`` is on ``DENIED_TABLES``; the health investigation
# reads it through the client's privileged-read path with an explicit,
# single-entry allowlist. All other tables this investigation hits
# (``sys_script``, ``sys_script_client``, ``sys_ui_policy``, ``syslog``)
# go through the normal ``query_records`` path.
_PRIVILEGED_ACL_TABLES: frozenset[str] = frozenset({"sys_security_acl"})


async def run(client: ServiceNowClient, params: dict[str, Any]) -> dict[str, Any]:
    """Generate a health report for a specific table.

    Queries record count, automation density (BRs, client scripts, ACLs, UI policies),
    and recent errors from syslog.

    Params:
        table: The table name to analyze (required).
        hours: Optional lookback period in hours (default None, queries all).
    """
    table = params.get("table")
    if not table:
        return build_investigation_result("table_health", [], error="Missing required parameter: table")

    validate_identifier(table)
    check_table_access(table)

    raw_hours = params.get("hours")
    hours: int | None = None
    if raw_hours is not None:
        try:
            hours = int(raw_hours)
        except (TypeError, ValueError):
            hours = 24

    # Build time-filtered query fragments for each query
    br_q = ServiceNowQuery().equals("collection", table).equals("active", "true")
    cs_q = ServiceNowQuery().equals("table", table).equals("active", "true")
    acl_q = ServiceNowQuery().equals("name", table).or_starts_with("name", f"{table}.")
    uip_q = ServiceNowQuery().equals("table", table).equals("active", "true")
    syslog_q = ServiceNowQuery().equals("level", "0").like("source", table)

    if hours is not None:
        br_q.hours_ago("sys_updated_on", hours)
        cs_q.hours_ago("sys_updated_on", hours)
        acl_q.hours_ago("sys_updated_on", hours)
        uip_q.hours_ago("sys_updated_on", hours)
        syslog_q.hours_ago("sys_created_on", hours)

    # Run all 6 health check queries in parallel
    (
        stats_result,
        br_result,
        cs_result,
        acl_result,
        uip_result,
        syslog_result,
    ) = await asyncio.gather(
        client.aggregate(table, query=""),
        client.query_records(
            "sys_script",
            br_q.build(),
            fields=["sys_id", "name", "when"],
            limit=INTERNAL_QUERY_LIMIT,
        ),
        client.query_records(
            "sys_script_client",
            cs_q.build(),
            fields=["sys_id", "name", "type"],
            limit=INTERNAL_QUERY_LIMIT,
        ),
        client.get_records_privileged(
            "sys_security_acl",
            allowed_tables=_PRIVILEGED_ACL_TABLES,
            query=acl_q.build(),
            fields="sys_id,name,operation",
            limit=INTERNAL_QUERY_LIMIT,
        ),
        client.query_records(
            "sys_ui_policy",
            uip_q.build(),
            fields=["sys_id", "short_description"],
            limit=INTERNAL_QUERY_LIMIT,
        ),
        client.query_records(
            "syslog",
            syslog_q.build(),
            fields=["sys_id", "message", "source", "sys_created_on"],
            limit=20,
            order_by="sys_created_on",
        ),
    )

    record_count = int(stats_result.get("stats", {}).get("count", 0))
    br_records = [mask_sensitive_fields(r) for r in br_result["records"]]
    cs_records = [mask_sensitive_fields(r) for r in cs_result["records"]]
    acl_records = [mask_sensitive_fields(r) for r in acl_result["records"]]
    uip_records = [mask_sensitive_fields(r) for r in uip_result["records"]]
    recent_errors = [mask_sensitive_fields(r) for r in syslog_result["records"]]

    # Build health indicators
    health_indicators: list[str] = []
    if len(br_records) > 10:
        health_indicators.append(f"High business rule count ({len(br_records)})")
    if len(acl_records) > 20:
        health_indicators.append(f"High ACL count ({len(acl_records)})")
    if len(recent_errors) > 0:
        health_indicators.append(f"{len(recent_errors)} recent errors in syslog")

    return build_investigation_result(
        "table_health",
        [{"category": "health_indicator", "detail": ind} for ind in health_indicators],
        table=table,
        hours=hours,
        record_count=record_count,
        automation={
            "business_rules": {
                "count": len(br_records),
                "records": br_records,
            },
            "client_scripts": {
                "count": len(cs_records),
                "records": cs_records,
            },
            "acl_count": len(acl_records),
            "ui_policies": {
                "count": len(uip_records),
                "records": uip_records,
            },
        },
        recent_errors=recent_errors,
        health_indicators=health_indicators,
    )


async def explain(client: ServiceNowClient, element_id: str) -> dict[str, Any]:
    """Provide rich context for a table health finding.

    element_id is the table name itself.
    """
    try:
        validate_identifier(element_id)
        check_table_access(element_id)
    except ValueError as e:
        return {"error": str(e)}

    # Re-run a lighter query to get basic table info
    stats_result = await client.aggregate(element_id, query="")
    record_count = int(stats_result.get("stats", {}).get("count", 0))

    explanation_parts = [
        f"Table '{element_id}' contains {record_count} records.",
        "Run a full table_health investigation for detailed automation and error analysis.",
    ]

    return {
        "element": element_id,
        "explanation": " ".join(explanation_parts),
        "record_count": record_count,
    }
