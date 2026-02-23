"""Investigation: find performance bottlenecks — heavy automation, frequent jobs, long flows."""

from collections import Counter
from typing import Any

from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.policy import mask_sensitive_fields

HEAVY_AUTOMATION_THRESHOLD = 10


async def run(client: ServiceNowClient, params: dict[str, Any]) -> dict[str, Any]:
    """Find performance bottlenecks across the instance.

    Checks:
    - Tables with excessive active business rules (>10 = heavy_automation)
    - Frequently-running scheduled jobs
    - Long-running flow contexts

    Params:
        limit: Maximum findings per category (default 20).
    """
    limit = params.get("limit", 20)
    findings: list[dict[str, Any]] = []

    # 1. Active BRs grouped by collection (table)
    br_result = await client.query_records(
        "sys_script",
        "active=true",
        fields=["sys_id", "name", "collection", "active"],
        limit=500,
    )
    br_records = [mask_sensitive_fields(r) for r in br_result["records"]]

    # Count BRs per table
    table_counts: Counter[str] = Counter()
    for br in br_records:
        table_counts[br.get("collection", "")] += 1

    for table_name, count in table_counts.most_common(limit):
        if count > HEAVY_AUTOMATION_THRESHOLD:
            findings.append(
                {
                    "category": "heavy_automation",
                    "element_id": table_name,
                    "name": table_name,
                    "detail": f"Table '{table_name}' has {count} active business rules (threshold: {HEAVY_AUTOMATION_THRESHOLD})",
                    "br_count": count,
                }
            )

    # 2. Frequent scheduled jobs
    sj_result = await client.query_records(
        "sysauto_script",
        "active=true",
        fields=["sys_id", "name", "run_type", "run_dayofweek", "sys_updated_on"],
        limit=limit,
    )
    for rec in sj_result["records"]:
        masked_rec = mask_sensitive_fields(rec)
        findings.append(
            {
                "category": "frequent_job",
                "element_id": f"sysauto_script:{masked_rec.get('sys_id', '')}",
                "name": masked_rec.get("name", ""),
                "detail": f"Active scheduled job: {masked_rec.get('name', '')}",
                "run_type": masked_rec.get("run_type", ""),
            }
        )

    # 3. Long-running flows (still IN_PROGRESS)
    flow_result = await client.query_records(
        "flow_context",
        "state=IN_PROGRESS",
        fields=["sys_id", "name", "state", "sys_created_on"],
        limit=limit,
    )
    for rec in flow_result["records"]:
        masked_rec = mask_sensitive_fields(rec)
        findings.append(
            {
                "category": "long_running_flow",
                "element_id": f"flow_context:{masked_rec.get('sys_id', '')}",
                "name": masked_rec.get("name", ""),
                "detail": f"Flow in progress since {masked_rec.get('sys_created_on', '')}",
            }
        )

    return {
        "investigation": "performance_bottlenecks",
        "finding_count": len(findings),
        "findings": findings,
        "params": {"limit": limit},
    }


async def explain(client: ServiceNowClient, element_id: str) -> dict[str, Any]:
    """Provide rich context for a performance bottleneck finding.

    element_id can be "table:sys_id" or just a table name (for heavy_automation).
    """
    if ":" in element_id:
        table, sys_id = element_id.split(":", 1)
        record = mask_sensitive_fields(await client.get_record(table, sys_id))
        explanation_parts = [
            f"Record from '{table}': {record.get('name', '')}.",
            "This record is flagged as a potential performance bottleneck.",
            "Review its configuration and execution frequency.",
        ]
        return {
            "element": element_id,
            "explanation": " ".join(explanation_parts),
            "record": record,
        }
    else:
        # element_id is a table name (heavy_automation category)
        stats_result = await client.aggregate(element_id, query="")
        record_count = int(stats_result.get("stats", {}).get("count", 0))

        br_result = await client.query_records(
            "sys_script",
            f"collection={element_id}^active=true",
            fields=["sys_id", "name", "when"],
            limit=50,
        )
        br_count = len(br_result["records"])

        explanation_parts = [
            f"Table '{element_id}' has {br_count} active business rules and {record_count} records.",
            f"Tables with more than {HEAVY_AUTOMATION_THRESHOLD} business rules can cause performance issues.",
            "Consider consolidating or disabling unnecessary rules.",
        ]

        return {
            "element": element_id,
            "explanation": " ".join(explanation_parts),
            "record_count": record_count,
            "br_count": br_count,
        }
