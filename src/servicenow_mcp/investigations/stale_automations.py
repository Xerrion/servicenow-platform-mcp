"""Investigation: find stale automations — stuck flows, disabled scripts, stale jobs."""

from typing import Any

from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.investigation_helpers import (
    build_investigation_result,
    fetch_and_explain,
    parse_int_param,
)
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields
from servicenow_mcp.utils import ServiceNowQuery


_ALLOWED_TABLES = {
    "flow_context",
    "sys_script",
    "sys_script_include",
    "sysauto_script",
}


async def run(client: ServiceNowClient, params: dict[str, Any]) -> dict[str, Any]:
    """Find stale automations across the instance.

    Checks:
    - Flow contexts stuck in IN_PROGRESS for > stale_days
    - Disabled business rules
    - Disabled script includes
    - Stale scheduled jobs (not run in > stale_days)

    Params:
        stale_days: Number of days to consider stale (default 30).
        limit: Maximum findings per category (default 20).
    """
    stale_days = max(1, parse_int_param(params, "stale_days", 30))
    limit = parse_int_param(params, "limit", 20)

    findings: list[dict[str, Any]] = []

    # 1. Stuck flow contexts (IN_PROGRESS created before cutoff)
    check_table_access("flow_context")
    flow_query = ServiceNowQuery().equals("state", "IN_PROGRESS").older_than_days("sys_created_on", stale_days).build()
    flow_result = await client.query_records(
        "flow_context",
        flow_query,
        fields=["sys_id", "name", "state", "sys_created_on"],
        limit=limit,
    )
    for rec in flow_result["records"]:
        masked_rec = mask_sensitive_fields(rec)
        findings.append(
            {
                "category": "stuck_flow",
                "element_id": f"flow_context:{masked_rec.get('sys_id', '')}",
                "name": masked_rec.get("name", ""),
                "detail": f"Flow stuck in IN_PROGRESS since {masked_rec.get('sys_created_on', '')}",
            }
        )

    # 2. Disabled business rules
    check_table_access("sys_script")
    br_query = ServiceNowQuery().equals("active", "false").build()
    br_result = await client.query_records(
        "sys_script",
        br_query,
        fields=["sys_id", "name", "collection", "sys_updated_on"],
        limit=limit,
    )
    for rec in br_result["records"]:
        masked_rec = mask_sensitive_fields(rec)
        findings.append(
            {
                "category": "disabled_business_rule",
                "element_id": f"sys_script:{masked_rec.get('sys_id', '')}",
                "name": masked_rec.get("name", ""),
                "detail": f"Disabled BR on table '{masked_rec.get('collection', '')}'",
            }
        )

    # 3. Disabled script includes
    check_table_access("sys_script_include")
    si_query = ServiceNowQuery().equals("active", "false").build()
    si_result = await client.query_records(
        "sys_script_include",
        si_query,
        fields=["sys_id", "name", "api_name", "sys_updated_on"],
        limit=limit,
    )
    for rec in si_result["records"]:
        masked_rec = mask_sensitive_fields(rec)
        findings.append(
            {
                "category": "disabled_script_include",
                "element_id": f"sys_script_include:{masked_rec.get('sys_id', '')}",
                "name": masked_rec.get("name", ""),
                "detail": f"Disabled script include '{masked_rec.get('api_name', '')}'",
            }
        )

    # 4. Stale scheduled jobs (not run in > stale_days)
    check_table_access("sysauto_script")
    sj_query = ServiceNowQuery().older_than_days("last_run", stale_days).build()
    sj_result = await client.query_records(
        "sysauto_script",
        sj_query,
        fields=["sys_id", "name", "run_type", "last_run"],
        limit=limit,
    )
    for rec in sj_result["records"]:
        masked_rec = mask_sensitive_fields(rec)
        findings.append(
            {
                "category": "stale_scheduled_job",
                "element_id": f"sysauto_script:{masked_rec.get('sys_id', '')}",
                "name": masked_rec.get("name", ""),
                "detail": f"Scheduled job last run {masked_rec.get('last_run', 'never')}",
            }
        )

    return build_investigation_result(
        "stale_automations",
        findings,
        params={"stale_days": stale_days, "limit": limit},
    )


def _build_explanation(table: str, sys_id: str, record: dict[str, Any]) -> list[str]:
    """Build explanation parts for a stale automation record."""
    explanation_parts = [f"Record from '{table}' with sys_id '{sys_id}'."]

    if table == "flow_context":
        explanation_parts.append(
            (  # noqa: UP034
                f"Flow '{record.get('name', '')}' has been in state "
                f"'{record.get('state', '')}' since {record.get('sys_created_on', '')}."
            )
        )
        explanation_parts.append(
            "Consider cancelling this flow if it is no longer needed, or investigate why it is stuck."
        )
    elif table == "sys_script":
        explanation_parts.append(
            f"Business rule '{record.get('name', '')}' is disabled. Review whether it should be removed or re-enabled."
        )
    elif table == "sys_script_include":
        explanation_parts.append(
            f"Script include '{record.get('name', '')}' is disabled. Check if any other scripts reference it."
        )
    elif table == "sysauto_script":
        last_run = record.get("last_run", "never")
        explanation_parts.append(
            f"Scheduled job '{record.get('name', '')}' last run was {last_run}. Verify it is still needed."
        )

    return explanation_parts


async def explain(client: ServiceNowClient, element_id: str) -> dict[str, Any]:
    """Provide rich context for a stale automation finding.

    element_id format: "table:sys_id" (e.g. "flow_context:fc001").
    """
    try:
        return await fetch_and_explain(client, element_id, _ALLOWED_TABLES, _build_explanation)
    except ValueError as e:
        return {"error": str(e)}
