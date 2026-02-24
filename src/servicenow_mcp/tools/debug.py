"""Debug and trace tools for investigating ServiceNow runtime behavior."""

import asyncio
import json
from collections import Counter

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.utils import ServiceNowQuery, format_response, generate_correlation_id


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register debug/trace tools on the MCP server."""

    @mcp.tool()
    async def debug_trace(
        record_sys_id: str,
        table: str,
        minutes: int = 60,
    ) -> str:
        """Build a merged timeline of events for a record from sys_audit, syslog, and sys_journal_field.

        Args:
            record_sys_id: The sys_id of the record to trace.
            table: The table the record belongs to.
            minutes: How many minutes of history to include (default 60).
        """
        correlation_id = generate_correlation_id()
        try:
            validate_identifier(table)
            check_table_access(table)

            timeline = []
            safe_record_sys_id = sanitize_query_value(record_sys_id)

            async with ServiceNowClient(settings, auth_provider) as client:
                # Fetch audit entries
                audit_query = (
                    ServiceNowQuery()
                    .equals("tablename", table)
                    .equals("documentkey", record_sys_id)
                    .minutes_ago("sys_created_on", minutes)
                    .build()
                )
                audit_result = await client.query_records(
                    "sys_audit",
                    audit_query,
                    fields=[
                        "sys_id",
                        "user",
                        "fieldname",
                        "oldvalue",
                        "newvalue",
                        "sys_created_on",
                    ],
                    limit=200,
                    order_by="sys_created_on",
                )

                for entry in audit_result["records"]:
                    masked_entry = mask_audit_entry(entry)
                    timeline.append(
                        {
                            "source": "sys_audit",
                            "timestamp": masked_entry.get("sys_created_on", ""),
                            "user": masked_entry.get("user", ""),
                            "detail": (
                                f"Field '{masked_entry.get('fieldname', '')}' changed "
                                f"from '{masked_entry.get('oldvalue', '')}' "
                                f"to '{masked_entry.get('newvalue', '')}'"
                            ),
                        }
                    )

                # Fetch syslog entries (keyed by source document)
                syslog_query_builder = ServiceNowQuery().equals("source", table)
                if record_sys_id:
                    syslog_query_builder = syslog_query_builder.equals("documentkey", record_sys_id)
                syslog_query = syslog_query_builder.minutes_ago("sys_created_on", minutes).build()
                syslog_result = await client.query_records(
                    "syslog",
                    syslog_query,
                    fields=[
                        "sys_id",
                        "message",
                        "source",
                        "level",
                        "sys_created_on",
                    ],
                    limit=100,
                    order_by="sys_created_on",
                )
                for entry in syslog_result["records"]:
                    masked_entry = mask_sensitive_fields(entry)
                    timeline.append(
                        {
                            "source": "syslog",
                            "timestamp": masked_entry.get("sys_created_on", ""),
                            "user": "",
                            "detail": masked_entry.get("message", ""),
                        }
                    )

                # Fetch journal entries (comments, work notes)
                journal_query = (
                    ServiceNowQuery().equals("element_id", record_sys_id).minutes_ago("sys_created_on", minutes).build()
                )
                journal_result = await client.query_records(
                    "sys_journal_field",
                    journal_query,
                    fields=[
                        "sys_id",
                        "element",
                        "value",
                        "sys_created_on",
                        "sys_created_by",
                    ],
                    limit=100,
                    order_by="sys_created_on",
                )
                for entry in journal_result["records"]:
                    masked_entry = mask_sensitive_fields(entry)
                    timeline.append(
                        {
                            "source": "sys_journal_field",
                            "timestamp": masked_entry.get("sys_created_on", ""),
                            "user": masked_entry.get("sys_created_by", ""),
                            "detail": (f"[{masked_entry.get('element', '')}] {masked_entry.get('value', '')[:200]}"),
                        }
                    )

            # Sort by timestamp
            timeline.sort(key=lambda e: e["timestamp"])

            return json.dumps(
                format_response(
                    data={
                        "record_sys_id": record_sys_id,
                        "table": table,
                        "event_count": len(timeline),
                        "timeline": timeline,
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
    async def debug_flow_execution(context_id: str) -> str:
        """Inspect a Flow Designer execution: context info and step-by-step log.

        Args:
            context_id: The sys_id of the flow context (sys_flow_context).
        """
        correlation_id = generate_correlation_id()
        try:
            async with ServiceNowClient(settings, auth_provider) as client:
                # Fetch flow context
                context = mask_sensitive_fields(await client.get_record("sys_flow_context", context_id))

                # Fetch flow log entries
                log_result = await client.query_records(
                    "sys_flow_log",
                    ServiceNowQuery().equals("context", context_id).build(),
                    fields=[
                        "sys_id",
                        "step_label",
                        "state",
                        "sys_created_on",
                        "output_data",
                        "error_message",
                    ],
                    limit=200,
                    order_by="sys_created_on",
                )

            steps = []
            for entry in log_result["records"]:
                masked_entry = mask_sensitive_fields(entry)
                steps.append(
                    {
                        "step_label": masked_entry.get("step_label", ""),
                        "state": masked_entry.get("state", ""),
                        "timestamp": masked_entry.get("sys_created_on", ""),
                        "output_data": masked_entry.get("output_data", ""),
                        "error_message": masked_entry.get("error_message", ""),
                    }
                )

            return json.dumps(
                format_response(
                    data={
                        "context": {
                            "sys_id": context.get("sys_id", ""),
                            "name": context.get("name", ""),
                            "state": context.get("state", ""),
                            "started": context.get("started", ""),
                            "ended": context.get("ended", ""),
                        },
                        "step_count": len(steps),
                        "steps": steps,
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
    async def debug_email_trace(record_sys_id: str) -> str:
        """Reconstruct the email chain for a record from sys_email.

        Args:
            record_sys_id: The sys_id of the record whose emails to trace.
        """
        correlation_id = generate_correlation_id()
        try:
            safe_record_sys_id = sanitize_query_value(record_sys_id)
            async with ServiceNowClient(settings, auth_provider) as client:
                email_result = await client.query_records(
                    "sys_email",
                    ServiceNowQuery().equals("instance", record_sys_id).build(),
                    fields=[
                        "sys_id",
                        "type",
                        "subject",
                        "recipients",
                        "sys_created_on",
                        "direct",
                        "body_text",
                    ],
                    limit=100,
                    order_by="sys_created_on",
                )

            emails = []
            for entry in email_result["records"]:
                masked_entry = mask_sensitive_fields(entry)
                emails.append(
                    {
                        "sys_id": masked_entry.get("sys_id", ""),
                        "type": masked_entry.get("type", ""),
                        "subject": masked_entry.get("subject", ""),
                        "recipients": masked_entry.get("recipients", ""),
                        "timestamp": masked_entry.get("sys_created_on", ""),
                        "body_preview": masked_entry.get("body_text", "")[:300],
                    }
                )

            return json.dumps(
                format_response(
                    data={
                        "record_sys_id": record_sys_id,
                        "email_count": len(emails),
                        "emails": emails,
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
    async def debug_integration_health(
        kind: str = "ecc_queue",
        hours: int = 24,
    ) -> str:
        """Summarize recent integration errors from ecc_queue or REST message transactions.

        Args:
            kind: The integration type to check — 'ecc_queue' or 'rest_message'.
            hours: How many hours of history to review (default 24).
        """
        correlation_id = generate_correlation_id()
        try:
            errors = []

            async with ServiceNowClient(settings, auth_provider) as client:
                if kind == "ecc_queue":
                    ecc_query = ServiceNowQuery().equals("state", "error").hours_ago("sys_created_on", hours).build()
                    result = await client.query_records(
                        "ecc_queue",
                        ecc_query,
                        fields=[
                            "sys_id",
                            "name",
                            "queue",
                            "state",
                            "error_string",
                            "sys_created_on",
                        ],
                        limit=100,
                        order_by="sys_created_on",
                    )
                    for entry in result["records"]:
                        masked_entry = mask_sensitive_fields(entry)
                        errors.append(
                            {
                                "sys_id": masked_entry.get("sys_id", ""),
                                "name": masked_entry.get("name", ""),
                                "queue": masked_entry.get("queue", ""),
                                "error": masked_entry.get("error_string", ""),
                                "timestamp": masked_entry.get("sys_created_on", ""),
                            }
                        )
                elif kind == "rest_message":
                    rest_query = (
                        ServiceNowQuery()
                        .greater_or_equal("http_status", "400")
                        .hours_ago("sys_created_on", hours)
                        .build()
                    )
                    result = await client.query_records(
                        "sys_rest_transaction",
                        rest_query,
                        fields=[
                            "sys_id",
                            "rest_message",
                            "http_method",
                            "http_status",
                            "endpoint",
                            "sys_created_on",
                        ],
                        limit=100,
                        order_by="sys_created_on",
                    )
                    for entry in result["records"]:
                        masked_entry = mask_sensitive_fields(entry)
                        errors.append(
                            {
                                "sys_id": masked_entry.get("sys_id", ""),
                                "rest_message": masked_entry.get("rest_message", ""),
                                "http_method": masked_entry.get("http_method", ""),
                                "http_status": masked_entry.get("http_status", ""),
                                "endpoint": masked_entry.get("endpoint", ""),
                                "timestamp": masked_entry.get("sys_created_on", ""),
                            }
                        )
                else:
                    return json.dumps(
                        format_response(
                            data=None,
                            correlation_id=correlation_id,
                            status="error",
                            error=f"Unknown kind '{kind}'. Use 'ecc_queue' or 'rest_message'.",
                        ),
                        indent=2,
                    )

            return json.dumps(
                format_response(
                    data={
                        "kind": kind,
                        "error_count": len(errors),
                        "errors": errors,
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
    async def debug_importset_run(import_set_sys_id: str) -> str:
        """Inspect an import set run: header info, row-level results, and error summary.

        Args:
            import_set_sys_id: The sys_id of the import set (sys_import_set).
        """
        correlation_id = generate_correlation_id()
        try:
            async with ServiceNowClient(settings, auth_provider) as client:
                # Fetch import set header
                import_set = mask_sensitive_fields(await client.get_record("sys_import_set", import_set_sys_id))

                # Fetch import set rows
                rows_result = await client.query_records(
                    "sys_import_set_row",
                    ServiceNowQuery().equals("sys_import_set", import_set_sys_id).build(),
                    fields=[
                        "sys_id",
                        "sys_import_state",
                        "sys_target_sys_id",
                        "sys_import_state_comment",
                    ],
                    limit=500,
                    order_by="sys_created_on",
                )

            rows = [mask_sensitive_fields(row) for row in rows_result["records"]]

            # Build summary by state
            state_counts: Counter[str] = Counter()
            error_details = []
            for row in rows:
                state = row.get("sys_import_state", "unknown")
                state_counts[state] += 1
                if state == "error":
                    error_details.append(
                        {
                            "sys_id": row.get("sys_id", ""),
                            "comment": row.get("sys_import_state_comment", ""),
                        }
                    )

            summary = {
                "total": len(rows),
                **dict(state_counts),
            }

            return json.dumps(
                format_response(
                    data={
                        "import_set": {
                            "sys_id": import_set.get("sys_id", ""),
                            "table_name": import_set.get("table_name", ""),
                            "state": import_set.get("state", ""),
                        },
                        "summary": summary,
                        "errors": error_details,
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
    async def debug_field_mutation_story(
        table: str,
        sys_id: str,
        field: str,
        limit: int = 50,
    ) -> str:
        """Show the chronological mutation history of a single field on a record.

        Args:
            table: The table name.
            sys_id: The sys_id of the record.
            field: The field name to trace mutations for.
            limit: Maximum number of audit entries to return (default 50).
        """
        correlation_id = generate_correlation_id()
        try:
            validate_identifier(table)
            validate_identifier(field)
            check_table_access(table)

            async with ServiceNowClient(settings, auth_provider) as client:
                audit_query = (
                    ServiceNowQuery()
                    .equals("tablename", table)
                    .equals("documentkey", sys_id)
                    .equals("fieldname", field)
                    .build()
                )
                audit_result = await client.query_records(
                    "sys_audit",
                    audit_query,
                    fields=[
                        "sys_id",
                        "user",
                        "fieldname",
                        "oldvalue",
                        "newvalue",
                        "sys_created_on",
                    ],
                    limit=limit,
                    order_by="sys_created_on",
                )

            mutations = []
            for entry in audit_result["records"]:
                masked_entry = mask_audit_entry(entry)
                mutations.append(
                    {
                        "user": masked_entry.get("user", ""),
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
                        "field": field,
                        "mutation_count": len(mutations),
                        "mutations": mutations,
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
