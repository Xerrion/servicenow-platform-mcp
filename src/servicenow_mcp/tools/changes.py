"""Change intelligence tools for inspecting update sets, diffs, and audit trails."""

import difflib
import json
from collections import defaultdict

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.policy import check_table_access, mask_audit_entry, mask_sensitive_fields
from servicenow_mcp.utils import (
    ServiceNowQuery,
    format_response,
    generate_correlation_id,
    sanitize_query_value,
    validate_identifier,
)

# Artifact types that are considered risky when modified
RISKY_TYPES = {
    "sys_security_acl",
    "sys_properties",
    "sys_db_object",
    "sys_dictionary",
    "sys_script",
    "sys_script_include",
    "sys_ws_operation",
    "sys_rest_message_fn",
}


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register change intelligence tools on the MCP server."""

    @mcp.tool()
    async def changes_updateset_inspect(update_set_id: str) -> str:
        """Inspect an update set: list members grouped by type, flag risks, and show summary.

        Args:
            update_set_id: The sys_id of the update set to inspect.
        """
        correlation_id = generate_correlation_id()
        try:
            async with ServiceNowClient(settings, auth_provider) as client:
                # Fetch update set metadata
                update_set = await client.get_record(
                    "sys_update_set",
                    update_set_id,
                )

                # Fetch all members of the update set
                members_result = await client.query_records(
                    "sys_update_xml",
                    ServiceNowQuery().equals("update_set", update_set_id).build(),
                    fields=[
                        "sys_id",
                        "name",
                        "type",
                        "action",
                        "target_name",
                    ],
                    limit=500,
                )

            members = [mask_sensitive_fields(m) for m in members_result["records"]]

            # Group members by type
            groups: dict[str, list[dict]] = defaultdict(list)
            for member in members:
                artifact_type = member.get("type", "unknown")
                groups[artifact_type].append(
                    {
                        "sys_id": member.get("sys_id", ""),
                        "name": member.get("name", ""),
                        "target_name": member.get("target_name", ""),
                        "action": member.get("action", ""),
                    }
                )

            # Build grouped summary
            group_summary = []
            for type_name, items in sorted(groups.items()):
                group_summary.append(
                    {
                        "type": type_name,
                        "count": len(items),
                        "items": items,
                    }
                )

            # Flag risks
            risk_flags = []
            member_types = set(groups.keys())
            risky_found = member_types & RISKY_TYPES
            for risky_type in sorted(risky_found):
                risk_flags.append(
                    f"Contains {groups[risky_type].__len__()} '{risky_type}' artifact(s) — review carefully"
                )

            return json.dumps(
                format_response(
                    data={
                        "update_set": {
                            "sys_id": update_set.get("sys_id", ""),
                            "name": update_set.get("name", ""),
                            "state": update_set.get("state", ""),
                        },
                        "total_members": len(members),
                        "groups": group_summary,
                        "risk_flags": risk_flags,
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
    async def changes_diff_artifact(table: str, sys_id: str) -> str:
        """Show a text diff between the two most recent versions of an artifact.

        Queries sys_update_version for the artifact's version history and produces
        a unified diff of the XML payloads.

        Args:
            table: The artifact table name (e.g., 'sys_script_include').
            sys_id: The sys_id of the artifact record.
        """
        correlation_id = generate_correlation_id()
        try:
            validate_identifier(table)
            check_table_access(table)

            # Build the update name pattern: {table}_{sys_id}
            update_name = f"{table}_{sanitize_query_value(sys_id)}"

            async with ServiceNowClient(settings, auth_provider) as client:
                versions_result = await client.query_records(
                    "sys_update_version",
                    ServiceNowQuery().equals("name", update_name).order_by("sys_recorded_at", descending=True).build(),
                    fields=["sys_id", "name", "payload", "sys_recorded_at"],
                    limit=2,
                )

            versions = [mask_sensitive_fields(v) for v in versions_result["records"]]

            if len(versions) < 2:
                return json.dumps(
                    format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error=f"Need at least 2 versions to diff, found {len(versions)}",
                    ),
                    indent=2,
                )

            # versions[0] is newest (DESC order), versions[1] is second-newest
            old_version = versions[1]
            new_version = versions[0]
            old_payload = old_version.get("payload", "")
            new_payload = new_version.get("payload", "")
            old_date = old_version.get("sys_recorded_at", "unknown")
            new_date = new_version.get("sys_recorded_at", "unknown")

            diff_lines = list(
                difflib.unified_diff(
                    old_payload.splitlines(keepends=True),
                    new_payload.splitlines(keepends=True),
                    fromfile=f"{update_name} ({old_date})",
                    tofile=f"{update_name} ({new_date})",
                )
            )

            return json.dumps(
                format_response(
                    data={
                        "artifact": update_name,
                        "old_version": old_date,
                        "new_version": new_date,
                        "diff": "".join(diff_lines),
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
    async def changes_last_touched(
        table: str,
        sys_id: str,
        limit: int = 20,
    ) -> str:
        """Show who last touched a record and what they changed, using sys_audit.

        Args:
            table: The table name of the record.
            sys_id: The sys_id of the record.
            limit: Maximum number of audit entries to return (default 20).
        """
        correlation_id = generate_correlation_id()
        try:
            validate_identifier(table)
            check_table_access(table)

            async with ServiceNowClient(settings, auth_provider) as client:
                audit_result = await client.query_records(
                    "sys_audit",
                    ServiceNowQuery().equals("tablename", table).equals("documentkey", sys_id).build(),
                    fields=[
                        "sys_id",
                        "user",
                        "fieldname",
                        "oldvalue",
                        "newvalue",
                        "sys_created_on",
                        "documentkey",
                    ],
                    limit=limit,
                    order_by="sys_created_on",
                )

            changes = []
            for entry in audit_result["records"]:
                masked_entry = mask_audit_entry(entry)
                changes.append(
                    {
                        "user": masked_entry.get("user", ""),
                        "field": masked_entry.get("fieldname", ""),
                        "old_value": masked_entry.get("oldvalue", ""),
                        "new_value": masked_entry.get("newvalue", ""),
                        "timestamp": masked_entry.get("sys_created_on", ""),
                    }
                )

            return json.dumps(
                format_response(
                    data={
                        "table": table,
                        "sys_id": sys_id,
                        "total": len(changes),
                        "changes": changes,
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
    async def changes_release_notes(
        update_set_id: str,
        format: str = "markdown",
    ) -> str:
        """Generate release notes from an update set in Markdown format.

        Args:
            update_set_id: The sys_id of the update set.
            format: Output format (currently only 'markdown' supported).
        """
        correlation_id = generate_correlation_id()
        try:
            async with ServiceNowClient(settings, auth_provider) as client:
                # Fetch update set metadata
                update_set = await client.get_record(
                    "sys_update_set",
                    update_set_id,
                )

                # Fetch members
                members_result = await client.query_records(
                    "sys_update_xml",
                    ServiceNowQuery().equals("update_set", update_set_id).build(),
                    fields=["sys_id", "type", "action", "target_name"],
                    limit=500,
                )

            members = [mask_sensitive_fields(m) for m in members_result["records"]]
            us_name = update_set.get("name", "Unnamed Update Set")
            us_description = update_set.get("description", "")
            us_state = update_set.get("state", "")
            us_updated = update_set.get("sys_updated_on", "")
            us_author = update_set.get("sys_created_by", "")

            # Group by type for the notes
            groups: dict[str, list[str]] = defaultdict(list)
            for member in members:
                artifact_type = member.get("type", "unknown")
                target = member.get("target_name", member.get("sys_id", "?"))
                action = member.get("action", "")
                groups[artifact_type].append(f"{target} ({action})")

            # Build Markdown
            lines = [
                f"# Release Notes: {us_name}",
                "",
            ]
            if us_description:
                lines.append(f"**Description:** {us_description}")
                lines.append("")
            if us_state:
                lines.append(f"**State:** {us_state}")
            if us_author:
                lines.append(f"**Author:** {us_author}")
            if us_updated:
                lines.append(f"**Last Updated:** {us_updated}")
            lines.append(f"**Total Changes:** {len(members)}")
            lines.append("")

            if groups:
                lines.append("## Changes by Type")
                lines.append("")
                for type_name, items in sorted(groups.items()):
                    lines.append(f"### {type_name} ({len(items)})")
                    for item in items:
                        lines.append(f"- {item}")
                    lines.append("")
            else:
                lines.append("_No changes in this update set._")
                lines.append("")

            release_notes = "\n".join(lines)

            return json.dumps(
                format_response(
                    data={
                        "update_set_name": us_name,
                        "release_notes": release_notes,
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
