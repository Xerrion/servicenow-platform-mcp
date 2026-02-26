"""Metadata tools for listing, inspecting, and searching ServiceNow platform artifacts."""

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields
from servicenow_mcp.utils import (
    ServiceNowQuery,
    format_response,
    generate_correlation_id,
    sanitize_query_value,
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


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register metadata tools on the MCP server."""

    @mcp.tool()
    async def meta_list_artifacts(
        artifact_type: str,
        query: str = "",
        limit: int = 100,
    ) -> str:
        """List platform artifacts (business rules, script includes, etc.) filtered by type and optional query.

        Args:
            artifact_type: The type of artifact to list (business_rule, script_include, ui_policy, ui_action, client_script, scheduled_job, fix_script).
            query: Optional ServiceNow encoded query string to further filter results.
            limit: Maximum number of artifacts to return.
        """
        correlation_id = generate_correlation_id()
        try:
            table = ARTIFACT_TABLES.get(artifact_type)
            if table is None:
                valid_types = ", ".join(sorted(ARTIFACT_TABLES.keys()))
                raise ValueError(f"Unknown artifact type '{artifact_type}'. Valid types: {valid_types}")

            check_table_access(table)

            encoded_query = query if query else ""

            async with ServiceNowClient(settings, auth_provider) as client:
                result = await client.query_records(
                    table,
                    encoded_query,
                    limit=limit,
                )

            return json.dumps(
                format_response(
                    data={
                        "artifact_type": artifact_type,
                        "table": table,
                        "artifacts": [mask_sensitive_fields(r) for r in result["records"]],
                        "total": result["count"],
                    },
                    correlation_id=correlation_id,
                ),
                indent=2,
            )
        except Exception as e:
            return json.dumps(
                format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=str(e),
                ),
                indent=2,
            )

    @mcp.tool()
    async def meta_get_artifact(
        artifact_type: str,
        sys_id: str,
    ) -> str:
        """Get full details of a platform artifact including its script body.

        Args:
            artifact_type: The type of artifact (business_rule, script_include, etc.).
            sys_id: The sys_id of the artifact to retrieve.
        """
        correlation_id = generate_correlation_id()
        try:
            table = ARTIFACT_TABLES.get(artifact_type)
            if table is None:
                valid_types = ", ".join(sorted(ARTIFACT_TABLES.keys()))
                raise ValueError(f"Unknown artifact type '{artifact_type}'. Valid types: {valid_types}")

            check_table_access(table)

            async with ServiceNowClient(settings, auth_provider) as client:
                record = mask_sensitive_fields(await client.get_record(table, sys_id))

            return json.dumps(
                format_response(data=record, correlation_id=correlation_id),
                indent=2,
            )
        except Exception as e:
            return json.dumps(
                format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=str(e),
                ),
                indent=2,
            )

    @mcp.tool()
    async def meta_find_references(
        target: str,
        limit: int = 20,
    ) -> str:
        """Search across all script tables for artifacts that reference a target string (API name, table name, function, etc.).

        Uses the Code Search API when available (single indexed call), with automatic
        fallback to per-table scriptCONTAINS queries if Code Search is not installed.

        Args:
            target: The string to search for in script bodies (e.g., 'GlideRecord', 'incident', a function name).
            limit: Maximum number of matches to return per table.
        """
        correlation_id = generate_correlation_id()
        try:
            matches: list[dict[str, Any]] = []
            search_method = "code_search_api"

            async with ServiceNowClient(settings, auth_provider) as client:
                # Try Code Search API first (indexed, single call)
                try:
                    cs_result = await client.code_search(term=target, limit=limit * len(SCRIPT_TABLES))
                    search_results = cs_result.get("search_results", [])
                    for sr in search_results:
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
                except Exception:
                    # Fallback to per-table scriptCONTAINS search
                    search_method = "table_scan_fallback"
                    for table in SCRIPT_TABLES:
                        query = ServiceNowQuery().contains("script", sanitize_query_value(target)).build()
                        try:
                            check_table_access(table)
                            result = await client.query_records(
                                table,
                                query,
                                fields=["sys_id", "name", "sys_class_name"],
                                limit=limit,
                            )
                            for record in result["records"]:
                                matches.append(
                                    {
                                        "table": table,
                                        "sys_id": record.get("sys_id", ""),
                                        "name": record.get("name", ""),
                                        "sys_class_name": record.get("sys_class_name", table),
                                    }
                                )
                        except Exception:
                            # Skip tables that fail (e.g., access issues)
                            continue

            return json.dumps(
                format_response(
                    data={
                        "target": target,
                        "matches": matches,
                        "total_matches": len(matches),
                        "search_method": search_method,
                    },
                    correlation_id=correlation_id,
                ),
                indent=2,
            )
        except Exception as e:
            return json.dumps(
                format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=str(e),
                ),
                indent=2,
            )

    @mcp.tool()
    async def meta_what_writes(
        table: str,
        field: str = "",
    ) -> str:
        """Find business rules and other mechanisms that write to a specific table (and optionally a specific field).

        Args:
            table: The ServiceNow table to investigate (e.g., 'incident').
            field: Optional field name to narrow results. When provided, only returns writers whose script references this field.
        """
        correlation_id = generate_correlation_id()
        try:
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
                    limit=200,
                )

                for record in result["records"]:
                    script = record.get("script", "")
                    # If a field is specified, only include BRs that reference it
                    if field and field not in script:
                        continue
                    writers.append(mask_sensitive_fields(record))

            return json.dumps(
                format_response(
                    data={
                        "table": table,
                        "field": field if field else None,
                        "writers": writers,
                        "total": len(writers),
                    },
                    correlation_id=correlation_id,
                ),
                indent=2,
            )
        except Exception as e:
            return json.dumps(
                format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=str(e),
                ),
                indent=2,
            )
