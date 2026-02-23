"""Developer action tools for toggling artifacts, managing properties, seeding data, and preview/apply workflows."""

import asyncio
import json
import uuid

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.policy import can_write
from servicenow_mcp.state import PreviewTokenStore, SeededRecordTracker
from servicenow_mcp.tools.metadata import ARTIFACT_TABLES
from servicenow_mcp.utils import format_response, generate_correlation_id, validate_identifier


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register developer action tools on the MCP server."""

    # In-memory state stores, shared across tools via closure
    preview_store = PreviewTokenStore()
    seed_tracker = SeededRecordTracker()

    @mcp.tool()
    async def dev_toggle(artifact_type: str, sys_id: str, active: bool) -> str:
        """Toggle the active field on a ServiceNow artifact (business rule, script include, etc.).

        Args:
            artifact_type: The type of artifact (e.g. 'business_rule', 'script_include').
            sys_id: The sys_id of the artifact record.
            active: Whether to set the artifact active (true) or inactive (false).
        """
        correlation_id = generate_correlation_id()
        try:
            # Resolve artifact type to table name
            table = ARTIFACT_TABLES.get(artifact_type)
            if table is None:
                return json.dumps(
                    format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error=f"Unknown artifact type: '{artifact_type}'. "
                        f"Valid types: {', '.join(sorted(ARTIFACT_TABLES.keys()))}",
                    )
                )

            # Write gate
            if not can_write(table, settings):
                return json.dumps(
                    format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error="Write operations are blocked in production environments",
                    )
                )

            async with ServiceNowClient(settings, auth_provider) as client:
                # Read current state
                current = await client.get_record(table, sys_id)
                old_active = current.get("active", "unknown")

                # Update active field
                updated = await client.update_record(table, sys_id, {"active": str(active).lower()})
                new_active = updated.get("active", "unknown")

            return json.dumps(
                format_response(
                    data={
                        "sys_id": sys_id,
                        "artifact_type": artifact_type,
                        "table": table,
                        "old_active": old_active,
                        "new_active": new_active,
                    },
                    correlation_id=correlation_id,
                )
            )
        except Exception as e:
            return json.dumps(
                format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=str(e),
                )
            )

    @mcp.tool()
    async def dev_set_property(name: str, value: str) -> str:
        """Set a ServiceNow system property value. Returns the old value.

        Args:
            name: The property name (e.g. 'glide.ui.session_timeout').
            value: The new value to set.
        """
        correlation_id = generate_correlation_id()
        try:
            # Write gate
            if not can_write("sys_properties", settings):
                return json.dumps(
                    format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error="Write operations are blocked in production environments",
                    )
                )

            async with ServiceNowClient(settings, auth_provider) as client:
                # Find the property by name
                result = await client.query_records(
                    "sys_properties",
                    f"name={name}",
                    limit=1,
                )
                records = result["records"]
                if not records:
                    return json.dumps(
                        format_response(
                            data=None,
                            correlation_id=correlation_id,
                            status="error",
                            error=f"Property '{name}' not found",
                        )
                    )

                prop = records[0]
                prop_sys_id = prop["sys_id"]
                old_value = prop.get("value", "")

                # Update the property value
                updated = await client.update_record("sys_properties", prop_sys_id, {"value": value})
                new_value = updated.get("value", value)

            return json.dumps(
                format_response(
                    data={
                        "name": name,
                        "sys_id": prop_sys_id,
                        "old_value": old_value,
                        "new_value": new_value,
                    },
                    correlation_id=correlation_id,
                )
            )
        except Exception as e:
            return json.dumps(
                format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=str(e),
                )
            )

    @mcp.tool()
    async def dev_seed_test_data(table: str, records: str, tag: str | None = None) -> str:
        """Create test data records in a ServiceNow table. Returns sys_ids and a cleanup tag.

        Args:
            table: The table to insert records into (e.g. 'incident').
            records: A JSON string containing an array of record objects to create.
            tag: Optional tag for grouping seeded records for cleanup. Auto-generated if omitted.
        """
        correlation_id = generate_correlation_id()
        try:
            validate_identifier(table)

            # Write gate
            if not can_write(table, settings):
                return json.dumps(
                    format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error="Write operations are blocked in production environments",
                    )
                )

            # Parse records JSON string
            record_list = json.loads(records)

            # Auto-generate tag if not provided
            seed_tag = tag if tag else f"seed-{uuid.uuid4().hex[:8]}"

            sys_ids: list[str] = []
            async with ServiceNowClient(settings, auth_provider) as client:
                created_records = await asyncio.gather(
                    *(client.create_record(table, record_data) for record_data in record_list)
                )
                sys_ids = [created["sys_id"] for created in created_records]

            # Track for cleanup
            seed_tracker.track(seed_tag, table, sys_ids)

            return json.dumps(
                format_response(
                    data={
                        "table": table,
                        "created_count": len(sys_ids),
                        "sys_ids": sys_ids,
                        "tag": seed_tag,
                    },
                    correlation_id=correlation_id,
                )
            )
        except Exception as e:
            return json.dumps(
                format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=str(e),
                )
            )

    @mcp.tool()
    async def dev_cleanup(tag: str) -> str:
        """Delete all records previously seeded with the given tag.

        Args:
            tag: The seed tag returned by dev_seed_test_data.
        """
        correlation_id = generate_correlation_id()
        try:
            # Look up tracked records
            entries = seed_tracker.get(tag)
            if entries is None:
                return json.dumps(
                    format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error=f"No records found for tag '{tag}'",
                    )
                )

            deleted_count = 0
            async with ServiceNowClient(settings, auth_provider) as client:
                delete_tasks = []
                for entry in entries:
                    table = entry["table"]
                    for sys_id in entry["sys_ids"]:
                        delete_tasks.append(client.delete_record(table, sys_id))
                await asyncio.gather(*delete_tasks)
                deleted_count = len(delete_tasks)

            # Remove from tracker
            seed_tracker.remove(tag)

            return json.dumps(
                format_response(
                    data={
                        "tag": tag,
                        "deleted_count": deleted_count,
                    },
                    correlation_id=correlation_id,
                )
            )
        except Exception as e:
            return json.dumps(
                format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=str(e),
                )
            )

    @mcp.tool()
    async def table_preview_update(table: str, sys_id: str, changes: str) -> str:
        """Preview an update to a record: shows field-level diff and returns a token to apply.

        Args:
            table: The table containing the record.
            sys_id: The sys_id of the record to update.
            changes: A JSON string of field-value pairs to change.
        """
        correlation_id = generate_correlation_id()
        try:
            validate_identifier(table)

            # Write gate
            if not can_write(table, settings):
                return json.dumps(
                    format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error="Write operations are blocked in production environments",
                    )
                )

            # Parse changes JSON string
            changes_dict = json.loads(changes)

            async with ServiceNowClient(settings, auth_provider) as client:
                current = await client.get_record(table, sys_id)

            # Build field-level diff (only for fields being changed)
            diff: dict[str, dict[str, str]] = {}
            for field, new_value in changes_dict.items():
                old_value = current.get(field, "")
                diff[field] = {"old": old_value, "new": new_value}

            # Store preview for later apply
            token = preview_store.create(
                {
                    "table": table,
                    "sys_id": sys_id,
                    "changes": changes_dict,
                }
            )

            return json.dumps(
                format_response(
                    data={
                        "token": token,
                        "table": table,
                        "sys_id": sys_id,
                        "diff": diff,
                    },
                    correlation_id=correlation_id,
                )
            )
        except Exception as e:
            return json.dumps(
                format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=str(e),
                )
            )

    @mcp.tool()
    async def table_apply_update(preview_token: str) -> str:
        """Apply a previously previewed update using the preview token.

        Args:
            preview_token: The token returned by table_preview_update.
        """
        correlation_id = generate_correlation_id()
        try:
            # Consume the token (single-use)
            payload = preview_store.consume(preview_token)
            if payload is None:
                return json.dumps(
                    format_response(
                        data=None,
                        correlation_id=correlation_id,
                        status="error",
                        error="Invalid or expired preview token",
                    )
                )

            table = payload["table"]
            sys_id = payload["sys_id"]
            changes = payload["changes"]

            async with ServiceNowClient(settings, auth_provider) as client:
                updated = await client.update_record(table, sys_id, changes)

            return json.dumps(
                format_response(
                    data={
                        "table": table,
                        "sys_id": sys_id,
                        "record": updated,
                    },
                    correlation_id=correlation_id,
                )
            )
        except Exception as e:
            return json.dumps(
                format_response(
                    data=None,
                    correlation_id=correlation_id,
                    status="error",
                    error=str(e),
                )
            )
