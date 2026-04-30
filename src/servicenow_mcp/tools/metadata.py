"""Metadata tools for listing, inspecting, and searching ServiceNow platform artifacts."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.mcp_state import get_query_store
from servicenow_mcp.policy import (
    INTERNAL_QUERY_LIMIT,
    check_table_access,
    enforce_query_safety,
    mask_sensitive_fields,
)
from servicenow_mcp.state import QueryTokenStore
from servicenow_mcp.utils import (
    ServiceNowQuery,
    format_response,
    resolve_query_token,
    validate_identifier,
)


# Mapping from human-friendly artifact type names to ServiceNow tables
ARTIFACT_TABLES: dict[str, str] = {
    "business_rule": "sys_script",
    "script_include": "sys_script_include",
    "ui_policy": "sys_ui_policy",
    "ui_action": "sys_ui_action",
    "client_script": "sys_script_client",
    "scheduled_job": "sysauto_script",
    "fix_script": "sys_script_fix",
}

# Tables that contain script bodies (used for cross-table reference search)
SCRIPT_TABLES: list[str] = [
    "sys_script",
    "sys_script_include",
    "sys_script_client",
    "sys_ui_action",
    "sysauto_script",
    "sys_script_fix",
]


def _resolve_artifact_table(artifact_type: str) -> str:
    """Resolve artifact_type to its ServiceNow table name.

    Raises:
        ValueError: If artifact_type is not in ARTIFACT_TABLES.
    """
    table = ARTIFACT_TABLES.get(artifact_type)
    if table is None:
        valid_types = ", ".join(sorted(ARTIFACT_TABLES.keys()))
        raise ValueError(f"Unknown artifact_type '{artifact_type}'. Valid types: {valid_types}")
    return table


def _search_via_code_search_api(
    cs_result: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract matches from the Code Search API response, filtering out inaccessible tables."""
    matches: list[dict[str, Any]] = []
    for sr in cs_result.get("search_results", []):
        result_table = sr.get("className", "")
        try:
            check_table_access(result_table)
        except Exception:
            continue
        matches.append(
            {
                "table": result_table,
                "sys_id": sr.get("sys_id", ""),
                "name": sr.get("name", ""),
                "sys_class_name": result_table,
            }
        )
    return matches


async def _search_via_table_scan(
    client: ServiceNowClient,
    target: str,
    effective_limit: int,
) -> list[dict[str, Any]]:
    """Fallback: search for a target string across all script tables using scriptCONTAINS queries."""
    matches: list[dict[str, Any]] = []
    for table in SCRIPT_TABLES:
        query = ServiceNowQuery().contains("script", target).build()
        try:
            check_table_access(table)
            result = await client.query_records(
                table,
                query,
                fields=["sys_id", "name", "sys_class_name"],
                limit=effective_limit,
            )
            matches.extend(
                {
                    "table": table,
                    "sys_id": record.get("sys_id", ""),
                    "name": record.get("name", ""),
                    "sys_class_name": record.get("sys_class_name", table),
                }
                for record in result["records"]
            )
        except Exception:
            continue
    return matches


TOOL_NAMES: list[str] = [
    "meta_list_artifacts",
    "meta_get_artifact",
    "meta_find_references",
    "meta_what_writes",
]


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register metadata tools on the MCP server."""
    query_store: QueryTokenStore = get_query_store(mcp)

    @mcp.tool()
    @tool_handler
    async def meta_list_artifacts(
        artifact_type: str,
        query_token: str = "",
        limit: int = 100,
        *,
        correlation_id: str,
    ) -> str:
        """List platform artifacts (business rules, script includes, etc.) filtered by type and optional query.

        Args:
            artifact_type: The type of artifact to list (business_rule, script_include, ui_policy, ui_action, client_script, scheduled_job, fix_script).
            query_token: Token from the build_query tool for additional filtering.
                Use build_query to create a query first, then pass the returned query_token here.
                Leave empty for no additional filter.
            limit: Maximum number of artifacts to return.
        """
        table = _resolve_artifact_table(artifact_type)

        check_table_access(table)

        query = await resolve_query_token(query_token, query_store, correlation_id)
        encoded_query = query
        safety = enforce_query_safety(table, encoded_query, limit, settings)
        effective_limit = safety["limit"]

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                table,
                encoded_query,
                limit=effective_limit,
            )

        return format_response(
            data={
                "artifact_type": artifact_type,
                "table": table,
                "artifacts": [mask_sensitive_fields(r) for r in result["records"]],
                "total": result["count"],
            },
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def meta_get_artifact(
        artifact_type: str,
        sys_id: str,
        *,
        correlation_id: str,
    ) -> str:
        """Get full details of a platform artifact including its script body.

        Args:
            artifact_type: The type of artifact (business_rule, script_include, etc.).
            sys_id: The sys_id of the artifact to retrieve.
        """
        table = _resolve_artifact_table(artifact_type)

        check_table_access(table)

        async with ServiceNowClient(settings, auth_provider) as client:
            record = mask_sensitive_fields(await client.get_record(table, sys_id))

        return format_response(data=record, correlation_id=correlation_id)

    @mcp.tool()
    @tool_handler
    async def meta_find_references(
        target: str,
        limit: int = 20,
        *,
        correlation_id: str,
    ) -> str:
        """Search across all script tables for artifacts that reference a target string (API name, table name, function, etc.).

        Uses the Code Search API when available (single indexed call), with automatic
        fallback to per-table scriptCONTAINS queries if Code Search is not installed.

        Args:
            target: The string to search for in script bodies (e.g., 'GlideRecord', 'incident', a function name).
            limit: Maximum number of matches to return per table.
        """
        search_method = "code_search_api"
        effective_limit = min(limit, settings.max_row_limit)

        async with ServiceNowClient(settings, auth_provider) as client:
            try:
                cs_result = await client.code_search(term=target, limit=effective_limit * len(SCRIPT_TABLES))
                matches = _search_via_code_search_api(cs_result)
            except Exception:
                search_method = "table_scan_fallback"
                matches = await _search_via_table_scan(client, target, effective_limit)

        return format_response(
            data={
                "target": target,
                "matches": matches,
                "total_matches": len(matches),
                "search_method": search_method,
            },
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def meta_what_writes(
        table: str,
        field: str = "",
        *,
        correlation_id: str,
    ) -> str:
        """Find business rules and other mechanisms that write to a specific table (and optionally a specific field).

        Args:
            table: The ServiceNow table to investigate (e.g., 'incident').
            field: Optional field name to narrow results. When provided, only returns writers whose script references this field.
        """
        validate_identifier(table)
        if field:
            validate_identifier(field)
        check_table_access(table)

        writers: list[dict[str, Any]] = []

        async with ServiceNowClient(settings, auth_provider) as client:
            # Query business rules for the target table
            result = await client.query_records(
                "sys_script",
                ServiceNowQuery().equals("collection", table).build(),
                limit=INTERNAL_QUERY_LIMIT,
            )

            for record in result["records"]:
                script = record.get("script", "")
                # If a field is specified, only include BRs that reference it
                if field and field not in script:
                    continue
                writers.append(mask_sensitive_fields(record))

        return format_response(
            data={
                "table": table,
                "field": field or None,
                "writers": writers,
                "total": len(writers),
            },
            correlation_id=correlation_id,
        )
