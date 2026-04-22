"""Investigation: detect conflicting or overlapping ACL rules."""

from collections import defaultdict
from typing import Any

from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.investigation_helpers import build_investigation_result
from servicenow_mcp.policy import (
    INTERNAL_QUERY_LIMIT,
    check_table_access,
)
from servicenow_mcp.utils import ServiceNowQuery, validate_identifier


# ``sys_security_acl`` is on ``DENIED_TABLES`` because it discloses security
# posture. This investigation legitimately needs to read it; we do so via the
# client's privileged-read path with an explicit, single-entry allowlist.
_PRIVILEGED_ACL_TABLES: frozenset[str] = frozenset({"sys_security_acl"})


async def run(client: ServiceNowClient, params: dict[str, Any]) -> dict[str, Any]:
    """Find ACL conflicts for a table - multiple ACLs with the same name.

    Two or more ACLs with the same name and operation but different conditions
    can cause unpredictable access control behavior.

    Params:
        table: The table name to check (required).
    """
    table = params.get("table")
    if not table:
        return build_investigation_result("acl_conflicts", [], error="Missing required parameter: table")

    check_table_access(table)

    # Query ACLs for the table name and field-level ACLs
    query = ServiceNowQuery().equals("name", table).or_starts_with("name", f"{table}.").build()
    acl_result = await client.get_records_privileged(
        "sys_security_acl",
        allowed_tables=_PRIVILEGED_ACL_TABLES,
        query=query,
        fields="sys_id,name,operation,condition,script,active",
        limit=INTERNAL_QUERY_LIMIT,
    )
    # ``get_records_privileged`` already applies table-aware masking.
    acls = list(acl_result["records"])

    # Group by (name, operation) to find true duplicates
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for acl in acls:
        key = f"{acl.get('name', '')}|{acl.get('operation', '')}"
        groups[key].append(acl)

    # Find conflicts: groups with 2+ ACLs sharing the same name and operation
    findings: list[dict[str, Any]] = []
    for key, group in groups.items():
        if len(group) >= 2:
            name, operation = key.split("|", 1)
            findings.append(
                {
                    "category": "acl_conflict",
                    "name": name,
                    "operation": operation,
                    "count": len(group),
                    "acls": [
                        {
                            "sys_id": a.get("sys_id", ""),
                            "operation": a.get("operation", ""),
                            "condition": a.get("condition", ""),
                            "active": a.get("active", ""),
                        }
                        for a in group
                    ],
                    "detail": f"ACL '{name}' (operation: {operation}) has {len(group)} overlapping rules with different conditions",
                }
            )

    return build_investigation_result(
        "acl_conflicts",
        findings,
        table=table,
        total_acls_checked=len(acls),
    )


async def explain(client: ServiceNowClient, element_id: str) -> dict[str, Any]:
    """Provide rich context for an ACL conflict finding.

    element_id is an ACL sys_id.
    """
    try:
        validate_identifier(element_id)
    except ValueError as e:
        return {"error": str(e)}

    # ``sys_security_acl`` is denied for tool callers; this investigation
    # fetches the single matching row through the privileged-read path.
    acl_result = await client.get_records_privileged(
        "sys_security_acl",
        allowed_tables=_PRIVILEGED_ACL_TABLES,
        query=ServiceNowQuery().equals("sys_id", element_id).build(),
        fields="sys_id,name,operation,condition,script,active",
        limit=1,
    )
    records = acl_result["records"]
    if not records:
        return {"error": f"ACL with sys_id '{element_id}' not found"}
    record = records[0]

    explanation_parts = [
        f"ACL '{record.get('name', '')}' controls {record.get('operation', '')} access.",
        f"Condition: '{record.get('condition', '(none)')}'.",
        (
            "When multiple ACLs share the same name, ServiceNow evaluates all of them. "
            "Conflicting conditions can lead to unexpected access behavior."
        ),
        "Review whether these ACLs should be consolidated.",
    ]

    return {
        "element": element_id,
        "explanation": " ".join(explanation_parts),
        "record": record,
    }
