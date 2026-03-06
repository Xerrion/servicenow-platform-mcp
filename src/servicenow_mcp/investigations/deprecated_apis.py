"""Investigation: find deprecated API patterns in scripts."""

from typing import Any

from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.investigation_helpers import (
    build_investigation_result,
    fetch_and_explain,
    parse_int_param,
)


# Deprecated patterns to scan for
DEPRECATED_PATTERNS = [
    "Packages.",
    "gs.include(",
    "current.setWorkflow(false)",
    "GlideRecordSecure(",
    "g_form.flash(",
]

_ALLOWED_TABLES = {
    "sys_script_include",
    "sys_script",
    "sys_ws_operation",
    "sys_ui_script",
    "sys_processor",
    "sp_widget",
}


async def run(client: ServiceNowClient, params: dict[str, Any]) -> dict[str, Any]:
    """Scan scripts for deprecated API usage patterns.

    Uses Code Search API to find deprecated patterns across all script tables.

    Params:
        limit: Maximum findings per pattern (default 20).
    """
    limit = parse_int_param(params, "limit", 20)
    findings: list[dict[str, Any]] = []

    for pattern in DEPRECATED_PATTERNS:
        try:
            result = await client.code_search(term=pattern, limit=limit)
            search_results = result.get("search_results", [])
            findings.extend(
                {
                    "pattern": pattern,
                    "element_id": f"{match.get('className', 'unknown')}:{match.get('sys_id', '')}",
                    "name": match.get("name", ""),
                    "table": match.get("className", ""),
                    "detail": f"Uses deprecated pattern '{pattern}'",
                }
                for match in search_results
            )
        except Exception:
            # Code Search API may not be available; skip pattern
            continue

    return build_investigation_result(
        "deprecated_apis",
        findings,
        params={"limit": limit},
        patterns_searched=DEPRECATED_PATTERNS,
    )


def _build_explanation(table: str, _sys_id: str, record: dict[str, Any]) -> list[str]:
    """Build explanation parts for a deprecated API finding."""
    return [
        f"Script '{record.get('name', '')}' in table '{table}' uses deprecated API patterns.",
        "Deprecated APIs may be removed in future ServiceNow versions and can cause upgrade issues.",
        "Review the script and replace deprecated calls with supported alternatives.",
    ]


async def explain(client: ServiceNowClient, element_id: str) -> dict[str, Any]:
    """Provide rich context for a deprecated API finding.

    element_id format: "table:sys_id".
    """
    try:
        return await fetch_and_explain(client, element_id, _ALLOWED_TABLES, _build_explanation)
    except ValueError as e:
        return {"error": str(e)}
