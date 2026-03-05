"""Documentation tools for generating logic maps, summaries, test scenarios, and review notes."""

import asyncio
import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import (
    INTERNAL_QUERY_LIMIT,
    check_table_access,
    mask_sensitive_fields,
)
from servicenow_mcp.tools.metadata import ARTIFACT_TABLES
from servicenow_mcp.utils import (
    ServiceNowQuery,
    format_response,
    validate_identifier,
)


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register documentation tools on the MCP server."""

    @mcp.tool()
    @tool_handler
    async def docs_logic_map(table: str, *, correlation_id: str) -> str:
        """Generate a lifecycle logic map of all automations on a table.

        Groups business rules, client scripts, UI policies, and UI actions by
        lifecycle phase (before_insert, after_update, display, async, etc.).

        Args:
            table: The table name to map.
        """
        validate_identifier(table)
        check_table_access(table)

        async with ServiceNowClient(settings, auth_provider) as client:
            # Fetch all four artifact types in parallel
            (
                br_result,
                cs_result,
                uip_result,
                uia_result,
            ) = await asyncio.gather(
                client.query_records(
                    "sys_script",
                    ServiceNowQuery().equals("collection", table).equals("active", "true").build(),
                    fields=[
                        "sys_id",
                        "name",
                        "when",
                        "action_insert",
                        "action_update",
                        "action_delete",
                        "order",
                        "active",
                    ],
                    limit=INTERNAL_QUERY_LIMIT,
                ),
                client.query_records(
                    "sys_script_client",
                    ServiceNowQuery().equals("table", table).equals("active", "true").build(),
                    fields=["sys_id", "name", "type", "active"],
                    limit=INTERNAL_QUERY_LIMIT,
                ),
                client.query_records(
                    "sys_ui_policy",
                    ServiceNowQuery().equals("table", table).equals("active", "true").build(),
                    fields=["sys_id", "short_description", "active"],
                    limit=INTERNAL_QUERY_LIMIT,
                ),
                client.query_records(
                    "sys_ui_action",
                    ServiceNowQuery().equals("table", table).equals("active", "true").build(),
                    fields=["sys_id", "name", "action_name", "active"],
                    limit=INTERNAL_QUERY_LIMIT,
                ),
            )

        # Build phase map from business rules
        phases: dict[str, list[dict[str, Any]]] = {}
        for br in br_result["records"]:
            when = br.get("when", "before")
            operations = []
            if br.get("action_insert") == "true":
                operations.append("insert")
            if br.get("action_update") == "true":
                operations.append("update")
            if br.get("action_delete") == "true":
                operations.append("delete")
            if not operations:
                operations = ["all"]

            for op in operations:
                phase_key = f"{when}_{op}"
                if phase_key not in phases:
                    phases[phase_key] = []
                phases[phase_key].append(
                    {
                        "type": "business_rule",
                        "sys_id": br.get("sys_id", ""),
                        "name": br.get("name", ""),
                        "order": br.get("order", ""),
                    }
                )

        # Add client scripts under 'client' phase
        cs_records = cs_result["records"]
        if cs_records:
            for cs in cs_records:
                cs_type = cs.get("type", "onChange")
                phase_key = f"client_{cs_type}"
                if phase_key not in phases:
                    phases[phase_key] = []
                phases[phase_key].append(
                    {
                        "type": "client_script",
                        "sys_id": cs.get("sys_id", ""),
                        "name": cs.get("name", ""),
                    }
                )

        # Add UI policies under 'ui_policy' phase
        uip_records = uip_result["records"]
        if uip_records:
            phases["ui_policy"] = [
                {
                    "type": "ui_policy",
                    "sys_id": p.get("sys_id", ""),
                    "name": p.get("short_description", ""),
                }
                for p in uip_records
            ]

        # Add UI actions under 'ui_action' phase
        uia_records = uia_result["records"]
        if uia_records:
            phases["ui_action"] = [
                {
                    "type": "ui_action",
                    "sys_id": a.get("sys_id", ""),
                    "name": a.get("name", ""),
                }
                for a in uia_records
            ]

        total = len(br_result["records"]) + len(cs_records) + len(uip_records) + len(uia_records)

        return format_response(
            data={
                "table": table,
                "phases": phases,
                "total_automations": total,
            },
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def docs_artifact_summary(artifact_type: str, sys_id: str, *, correlation_id: str) -> str:
        """Generate a summary for a platform artifact including dependencies.

        Parses the artifact's script for referenced GlideRecord tables and uses
        code search to find what else references this artifact.

        Args:
            artifact_type: The artifact type (e.g. business_rule, script_include).
            sys_id: The sys_id of the artifact.
        """
        table = ARTIFACT_TABLES.get(artifact_type)
        if not table:
            valid_types = ", ".join(sorted(ARTIFACT_TABLES.keys()))
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error=f"Unknown artifact type '{artifact_type}'. Valid: {valid_types}",
            )

        check_table_access(table)

        async with ServiceNowClient(settings, auth_provider) as client:
            record = mask_sensitive_fields(await client.get_record(table, sys_id))

            # Parse script for GlideRecord('table_name') references
            script = record.get("script", "")
            referenced_tables = _extract_gliderecord_tables(script)

            # Search for what references this artifact
            artifact_name = record.get("name", record.get("api_name", ""))
            referenced_by: list[dict[str, Any]] = []
            if artifact_name:
                try:
                    search_result = await client.code_search(term=artifact_name)
                    referenced_by = search_result.get("search_results", [])
                except Exception:
                    pass

        return format_response(
            data={
                "artifact": record,
                "referenced_tables": referenced_tables,
                "referenced_by": referenced_by,
                "summary": f"Artifact '{artifact_name}' references {len(referenced_tables)} table(s) "
                f"and is referenced by {len(referenced_by)} other artifact(s).",
            },
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def docs_test_scenarios(artifact_type: str, sys_id: str, *, correlation_id: str) -> str:
        """Analyze an artifact's script and suggest test scenarios.

        Detects patterns like operation checks, conditional branches, role checks,
        and abort actions to generate relevant test scenario suggestions.

        Args:
            artifact_type: The artifact type (e.g. business_rule, script_include).
            sys_id: The sys_id of the artifact.
        """
        table = ARTIFACT_TABLES.get(artifact_type)
        if not table:
            valid_types = ", ".join(sorted(ARTIFACT_TABLES.keys()))
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error=f"Unknown artifact type '{artifact_type}'. Valid: {valid_types}",
            )

        check_table_access(table)

        async with ServiceNowClient(settings, auth_provider) as client:
            record = await client.get_record(table, sys_id)

        record = mask_sensitive_fields(record)
        script = record.get("script", "")
        scenarios = _generate_test_scenarios(script, record)

        return format_response(
            data={
                "artifact": {
                    "name": record.get("name", ""),
                    "sys_id": sys_id,
                    "type": artifact_type,
                },
                "scenarios": scenarios,
                "scenario_count": len(scenarios),
            },
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def docs_review_notes(artifact_type: str, sys_id: str, *, correlation_id: str) -> str:
        """Scan an artifact's script for anti-patterns and generate review notes.

        Detects: GlideRecord in loop, hardcoded sys_ids, unbounded queries,
        and other common ServiceNow anti-patterns.

        Args:
            artifact_type: The artifact type (e.g. business_rule, script_include).
            sys_id: The sys_id of the artifact.
        """
        table = ARTIFACT_TABLES.get(artifact_type)
        if not table:
            valid_types = ", ".join(sorted(ARTIFACT_TABLES.keys()))
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error=f"Unknown artifact type '{artifact_type}'. Valid: {valid_types}",
            )

        check_table_access(table)

        async with ServiceNowClient(settings, auth_provider) as client:
            record = await client.get_record(table, sys_id)

        record = mask_sensitive_fields(record)
        script = record.get("script", "")
        findings = _scan_for_anti_patterns(script)

        return format_response(
            data={
                "artifact": {
                    "name": record.get("name", ""),
                    "sys_id": sys_id,
                    "type": artifact_type,
                },
                "findings": findings,
                "finding_count": len(findings),
            },
            correlation_id=correlation_id,
        )


# ── Helper functions ──────────────────────────────────────────────────────


def _extract_gliderecord_tables(script: str) -> list[str]:
    """Extract table names from GlideRecord('table') and GlideRecordSecure('table') calls."""
    pattern = r"""(?:GlideRecord|GlideRecordSecure)\s*\(\s*['"]([^'"]+)['"]\s*\)"""
    matches = re.findall(pattern, script)
    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result


def _generate_test_scenarios(script: str, record: dict[str, Any]) -> list[dict[str, str]]:
    """Analyze script and generate test scenario suggestions."""
    scenarios: list[dict[str, str]] = []

    if not script.strip():
        scenarios.append(
            {
                "scenario": "Basic execution test",
                "description": "Verify the artifact executes without errors when triggered.",
                "priority": "high",
            }
        )
        return scenarios

    # Detect operation() checks → insert/update/delete scenarios
    if re.search(r"current\.operation\(\)\s*==\s*['\"]insert['\"]", script):
        scenarios.append(
            {
                "scenario": "Test insert operation path",
                "description": "Create a new record and verify the insert-specific logic executes correctly.",
                "priority": "high",
            }
        )
    if re.search(r"current\.operation\(\)\s*==\s*['\"]update['\"]", script):
        scenarios.append(
            {
                "scenario": "Test update operation path",
                "description": "Update an existing record and verify the update-specific logic executes correctly.",
                "priority": "high",
            }
        )
    if re.search(r"current\.operation\(\)\s*==\s*['\"]delete['\"]", script):
        scenarios.append(
            {
                "scenario": "Test delete operation path",
                "description": "Delete a record and verify the delete-specific logic executes correctly.",
                "priority": "high",
            }
        )

    # Detect isNewRecord() checks
    if re.search(r"current\.isNewRecord\(\)", script):
        scenarios.append(
            {
                "scenario": "Test new record vs existing record",
                "description": "Test behavior for both new and existing records since isNewRecord() is used.",
                "priority": "high",
            }
        )

    # Detect if/else condition branches
    if re.search(r"\bif\s*\(", script):
        scenarios.append(
            {
                "scenario": "Test condition branches",
                "description": "Verify each conditional branch (if/else) is tested with appropriate input values.",
                "priority": "medium",
            }
        )

    # Detect role checks
    role_matches = re.findall(r"gs\.hasRole\(['\"]([^'\"]+)['\"]\)", script)
    scenarios.extend(
        {
            "scenario": f"Test with role '{role}'",
            "description": f"Test behavior when user has and does not have the '{role}' role.",
            "priority": "medium",
        }
        for role in role_matches
    )

    # Detect setAbortAction
    if re.search(r"setAbortAction\(true\)", script):
        scenarios.append(
            {
                "scenario": "Test abort action trigger",
                "description": "Verify conditions under which the operation is aborted and that the abort message is appropriate.",
                "priority": "high",
            }
        )

    # Detect GlideRecord queries (data dependency)
    tables = _extract_gliderecord_tables(script)
    if tables:
        scenarios.append(
            {
                "scenario": f"Test with dependent data ({', '.join(tables)})",
                "description": f"Ensure required records exist in {', '.join(tables)} before testing.",
                "priority": "medium",
            }
        )

    # Always add a generic scenario if nothing else detected
    if not scenarios:
        scenarios.append(
            {
                "scenario": "Basic execution test",
                "description": "Verify the artifact executes without errors when triggered.",
                "priority": "high",
            }
        )

    return scenarios


def _scan_for_anti_patterns(script: str) -> list[dict[str, str]]:
    """Scan script for common ServiceNow anti-patterns."""
    findings: list[dict[str, str]] = []

    if not script.strip():
        return findings

    # 1. GlideRecord inside a loop (while/for containing new GlideRecord)
    if re.search(r"while\s*\(.*?\)\s*\{.*?new\s+GlideRecord\s*\(", script, re.DOTALL):
        findings.append(
            {
                "category": "gliderecord_in_loop",
                "severity": "warning",
                "message": "GlideRecord instantiated inside a loop. This is a major performance "
                "concern — move the query outside the loop or use GlideAggregate.",
            }
        )

    # 2. Hardcoded sys_ids (32-char hex strings)
    sysid_matches = re.findall(r"['\"]([0-9a-f]{32})['\"]", script)
    if sysid_matches:
        findings.append(
            {
                "category": "hardcoded_sys_id",
                "severity": "warning",
                "message": f"Found {len(sysid_matches)} hardcoded sys_id(s). Use system properties "
                "or reference qualifiers instead for portability across instances.",
            }
        )

    # 3. Unbounded GlideRecord query (query() without addQuery/addEncodedQuery/get)
    gr_blocks = re.findall(r"new\s+GlideRecord\s*\([^)]+\)\s*;(.*?)\.query\(\)", script, re.DOTALL)
    for block in gr_blocks:
        if "addQuery" not in block and "addEncodedQuery" not in block and "get(" not in block:
            findings.append(
                {
                    "category": "unbounded_query",
                    "severity": "info",
                    "message": "GlideRecord.query() called without addQuery or addEncodedQuery. "
                    "This may return all records in the table. Add appropriate filters.",
                }
            )
            break  # Report once

    # 4. current.update() in a business rule
    if re.search(r"current\.update\(\)", script):
        findings.append(
            {
                "category": "current_update_in_br",
                "severity": "warning",
                "message": "current.update() called in script. In a business rule, this can cause "
                "recursive execution. Use workflow: false or autoSysFields: false if intentional.",
            }
        )

    return findings
