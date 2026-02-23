"""Investigation: find deprecated API patterns in scripts."""

from typing import Any

from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.policy import mask_sensitive_fields

# Deprecated patterns to scan for
DEPRECATED_PATTERNS = [
    "Packages.",
    "gs.include(",
    "current.setWorkflow(false)",
    "GlideRecordSecure(",
    "g_form.flash(",
]


async def run(client: ServiceNowClient, params: dict[str, Any]) -> dict[str, Any]:
    """Scan scripts for deprecated API usage patterns.

    Uses Code Search API to find deprecated patterns across all script tables.

    Params:
        limit: Maximum findings per pattern (default 20).
    """
    limit = params.get("limit", 20)
    findings: list[dict[str, Any]] = []

    for pattern in DEPRECATED_PATTERNS:
        try:
            result = await client.code_search(term=pattern, limit=limit)
            search_results = result.get("search_results", [])
            for match in search_results:
                findings.append(
                    {
                        "pattern": pattern,
                        "element_id": f"{match.get('className', 'unknown')}:{match.get('sys_id', '')}",
                        "name": match.get("name", ""),
                        "table": match.get("className", ""),
                        "detail": f"Uses deprecated pattern '{pattern}'",
                    }
                )
        except Exception:
            # Code Search API may not be available; skip pattern
            continue

    return {
        "investigation": "deprecated_apis",
        "finding_count": len(findings),
        "findings": findings,
        "params": {"limit": limit},
        "patterns_searched": DEPRECATED_PATTERNS,
    }


async def explain(client: ServiceNowClient, element_id: str) -> dict[str, Any]:
    """Provide rich context for a deprecated API finding.

    element_id format: "table:sys_id".
    """
    table, sys_id = element_id.split(":", 1)
    record = mask_sensitive_fields(await client.get_record(table, sys_id))

    explanation_parts = [
        f"Script '{record.get('name', '')}' in table '{table}' uses deprecated API patterns.",
        "Deprecated APIs may be removed in future ServiceNow versions and can cause upgrade issues.",
        "Review the script and replace deprecated calls with supported alternatives.",
    ]

    return {
        "element": element_id,
        "explanation": " ".join(explanation_parts),
        "record": record,
    }
