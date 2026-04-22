"""Change intelligence tools for inspecting update sets, diffs, and audit trails."""

import difflib
import re
from collections import defaultdict
from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import (
    INTERNAL_QUERY_LIMIT,
    SCRIPT_BODY_MASK,
    check_table_access,
    mask_audit_entry,
    mask_sensitive_fields,
)
from servicenow_mcp.utils import (
    ServiceNowQuery,
    format_response,
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

RELEASE_NOTES_MARKDOWN_ALIASES = {"", "markdown", "md"}


def _normalize_release_notes_format(_format_value: str) -> str:
    """Normalize release note format inputs to the supported markdown output."""
    return "markdown"


TOOL_NAMES: list[str] = [
    "changes_updateset_inspect",
    "changes_diff_artifact",
    "changes_last_touched",
    "changes_release_notes",
]


# ---------------------------------------------------------------------------
# Module-level helpers (extracted from nested tool bodies to reduce complexity)
# ---------------------------------------------------------------------------


def _group_updateset_members(
    members: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Group update set members by type and flag risky artifact types.

    Returns a tuple of (group_summary, risk_flags).
    """
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
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

    group_summary = [
        {"type": type_name, "count": len(items), "items": items} for type_name, items in sorted(groups.items())
    ]

    risky_found = set(groups.keys()) & RISKY_TYPES
    risk_flags = [
        f"Contains {len(groups[risky_type])} '{risky_type}' artifact(s) - review carefully"
        for risky_type in sorted(risky_found)
    ]

    return group_summary, risk_flags


def _build_audit_changes(audit_records: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Transform audit records into change summaries."""
    changes: list[dict[str, str]] = []
    for entry in audit_records:
        masked = mask_audit_entry(entry)
        changes.append(
            {
                "user": masked.get("user", ""),
                "field": masked.get("fieldname", ""),
                "old_value": masked.get("oldvalue", ""),
                "new_value": masked.get("newvalue", ""),
                "timestamp": masked.get("sys_created_on", ""),
            }
        )
    return changes


def _build_release_notes_markdown(
    update_set: dict[str, Any],
    members: list[dict[str, Any]],
) -> tuple[str, str]:
    """Build release notes markdown from update set metadata and members."""
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

    lines = [f"# Release Notes: {us_name}", ""]
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
            lines.extend(f"- {item}" for item in items)
            lines.append("")
    else:
        lines.append("_No changes in this update set._")
        lines.append("")

    return us_name, "\n".join(lines)


# Regex that matches a <script>...</script> block in an update-set payload
# XML. Used to strip script bodies before diffing when the caller has not
# opted in to seeing raw script contents.
_PAYLOAD_SCRIPT_RE: re.Pattern[str] = re.compile(
    r"(<script[^>]*>)(.*?)(</script>)",
    re.DOTALL,
)


def _strip_payload_script_bodies(payload: str) -> str:
    """Replace ``<script>...</script>`` bodies with the script-mask sentinel."""
    return _PAYLOAD_SCRIPT_RE.sub(rf"\1{SCRIPT_BODY_MASK}\3", payload)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register change intelligence tools on the MCP server."""

    @mcp.tool()
    @tool_handler
    async def changes_updateset_inspect(update_set_id: str, *, correlation_id: str) -> str:
        """Inspect an update set: list members grouped by type, flag risks, and show summary.

        Args:
            update_set_id: The sys_id of the update set to inspect.
        """
        validate_identifier(update_set_id)

        async with ServiceNowClient(settings, auth_provider) as client:
            update_set = await client.get_record("sys_update_set", update_set_id)

            members_result = await client.query_records(
                "sys_update_xml",
                ServiceNowQuery().equals("update_set", update_set_id).build(),
                fields=["sys_id", "name", "type", "action", "target_name"],
                limit=INTERNAL_QUERY_LIMIT,
            )

        members = [mask_sensitive_fields(m) for m in members_result["records"]]
        group_summary, risk_flags = _group_updateset_members(members)

        return format_response(
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
        )

    @mcp.tool()
    @tool_handler
    async def changes_diff_artifact(
        table: str,
        sys_id: str,
        include_script_body: bool = False,
        *,
        correlation_id: str,
    ) -> str:
        """Show a text diff between the two most recent versions of an artifact.

        Queries sys_update_version for the artifact's version history and produces
        a unified diff of the XML payloads.

        Args:
            table: The artifact table name (e.g., 'sys_script_include').
            sys_id: The sys_id of the artifact record.
            include_script_body: If True, diff the raw payloads including
                script/markup bodies. Script/markup bodies are masked by
                default (both sides of the diff have their ``<script>`` bodies
                replaced with a sentinel before diffing). Set True only when
                you need to inspect the code itself; script bodies may
                contain hardcoded secrets.
        """
        validate_identifier(table)
        check_table_access(table)

        update_name = f"{table}_{sys_id}"

        async with ServiceNowClient(settings, auth_provider) as client:
            versions_result = await client.query_records(
                "sys_update_version",
                ServiceNowQuery().equals("name", update_name).order_by("sys_recorded_at", descending=True).build(),
                fields=["sys_id", "name", "payload", "sys_recorded_at"],
                limit=2,
            )

        versions = [mask_sensitive_fields(v) for v in versions_result["records"]]

        if len(versions) < 2:
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error=f"Need at least 2 versions to diff, found {len(versions)}",
            )

        old_version = versions[1]
        new_version = versions[0]
        old_payload = old_version.get("payload", "")
        new_payload = new_version.get("payload", "")
        old_date = old_version.get("sys_recorded_at", "unknown")
        new_date = new_version.get("sys_recorded_at", "unknown")

        if not include_script_body:
            old_payload = _strip_payload_script_bodies(old_payload)
            new_payload = _strip_payload_script_bodies(new_payload)

        diff_lines = list(
            difflib.unified_diff(
                old_payload.splitlines(keepends=True),
                new_payload.splitlines(keepends=True),
                fromfile=f"{update_name} ({old_date})",
                tofile=f"{update_name} ({new_date})",
            )
        )

        return format_response(
            data={
                "artifact": update_name,
                "old_version": old_date,
                "new_version": new_date,
                "diff": "".join(diff_lines),
            },
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def changes_last_touched(
        table: str,
        sys_id: str,
        limit: int = 20,
        *,
        correlation_id: str,
    ) -> str:
        """Show who last touched a record and what they changed, using sys_audit.

        Args:
            table: The table name of the record.
            sys_id: The sys_id of the record.
            limit: Maximum number of audit entries to return (default 20).
        """
        validate_identifier(table)
        check_table_access(table)

        async with ServiceNowClient(settings, auth_provider) as client:
            audit_result = await client.query_records(
                "sys_audit",
                ServiceNowQuery().equals("tablename", table).equals("documentkey", sys_id).build(),
                fields=["sys_id", "user", "fieldname", "oldvalue", "newvalue", "sys_created_on", "documentkey"],
                limit=limit,
                order_by="sys_created_on",
            )

        changes = _build_audit_changes(audit_result["records"])

        return format_response(
            data={
                "table": table,
                "sys_id": sys_id,
                "total": len(changes),
                "changes": changes,
            },
            correlation_id=correlation_id,
        )

    @mcp.tool()
    @tool_handler
    async def changes_release_notes(
        update_set_id: str,
        format: str = "markdown",
        *,
        correlation_id: str,
    ) -> str:
        """Generate release notes from an update set in Markdown format.

        Args:
            update_set_id: The sys_id of the update set.
            format: Output format. Markdown aliases are normalized and other values fall back to markdown.
        """
        validate_identifier(update_set_id)
        release_notes_format = _normalize_release_notes_format(format)

        async with ServiceNowClient(settings, auth_provider) as client:
            update_set = await client.get_record("sys_update_set", update_set_id)

            members_result = await client.query_records(
                "sys_update_xml",
                ServiceNowQuery().equals("update_set", update_set_id).build(),
                fields=["sys_id", "type", "action", "target_name"],
                limit=INTERNAL_QUERY_LIMIT,
            )

        members = [mask_sensitive_fields(m) for m in members_result["records"]]
        us_name, release_notes = _build_release_notes_markdown(update_set, members)

        return format_response(
            data={
                "format": release_notes_format,
                "update_set_name": us_name,
                "release_notes": release_notes,
            },
            correlation_id=correlation_id,
        )
