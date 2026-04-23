"""Write operations for ServiceNow platform artifacts.

All mutations flow through a preview/apply pair so callers must explicitly
confirm a destructive change before it reaches ServiceNow. The preview step
validates, classifies and describes the operation; the apply step consumes
the preview token (single-use) and performs the write.

The preview envelope never echoes script bodies verbatim and never echoes
values for fields matched by :func:`policy.is_sensitive_field`. Script
content is summarised by size and a short head snippet only.
"""

import json
import logging
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import (
    MASK_VALUE,
    TABLE_SCRIPT_FIELDS,
    check_table_access,
    is_sensitive_field,
    mask_sensitive_fields,
    write_gate,
)
from servicenow_mcp.state import PreviewTokenStore
from servicenow_mcp.utils import format_response, validate_identifier, validate_sys_id


logger = logging.getLogger(__name__)

# Mapping from human-friendly artifact type names to ServiceNow tables.
# Superset of metadata.py's 7-type ARTIFACT_TABLES.
WRITABLE_ARTIFACT_TABLES: dict[str, str] = {
    "business_rule": "sys_script",
    "script_include": "sys_script_include",
    "ui_policy": "sys_ui_policy",
    "ui_action": "sys_ui_action",
    "client_script": "sys_script_client",
    "scheduled_job": "sysauto_script",
    "fix_script": "sys_script_fix",
    "scripted_rest_resource": "sys_ws_operation",
    "ui_script": "sys_ui_script",
    "processor": "sys_processor",
    "widget": "sp_widget",
    "ui_page": "sys_ui_page",
    "ui_macro": "sys_ui_macro",
    "script_action": "sysevent_script_action",
    "mid_script_include": "ecc_agent_script_include",
    "scripted_rest_api": "sys_web_service",
    "notification_script": "sysevent_email_action",
}

DEFAULT_SCRIPT_FIELD: str = "script"
MAX_SCRIPT_FILE_BYTES: int = 1_048_576  # 1 MB

# Per-artifact override for the script field name.
# Types not listed here default to DEFAULT_SCRIPT_FIELD ("script").
SCRIPT_FIELD_MAP: dict[str, str] = {
    "ui_policy": "script_true",
    "scripted_rest_resource": "operation_script",
    "widget": "client_script",
    "ui_page": "html",
    "ui_macro": "xml",
    "notification_script": "advanced_condition",
}

# TTL for stored preview payloads; also echoed in the preview envelope so the
# caller knows how long they have to apply.
_PREVIEW_TTL_SECONDS: int = 300

# Number of characters of the script body echoed back in the preview. A short
# snippet helps a human verify they are applying the intended file; the full
# body is deliberately withheld so leak-by-echo on denied applies cannot
# reveal production script content.
_SCRIPT_HEAD_CHARS: int = 80


def _resolve_writable_artifact_table(artifact_type: str) -> str:
    """Resolve artifact_type to its ServiceNow table name.

    Raises:
        ValueError: If artifact_type is not in WRITABLE_ARTIFACT_TABLES.
    """
    table = WRITABLE_ARTIFACT_TABLES.get(artifact_type)
    if table is None:
        valid_types = ", ".join(sorted(WRITABLE_ARTIFACT_TABLES.keys()))
        raise ValueError(f"Unknown artifact_type '{artifact_type}'. Valid types: {valid_types}")
    return table


def _read_script_file(script_path: str, allowed_root: str) -> str:
    """Read a local script file and return its contents as a string.

    Args:
        script_path: Path to the script file.
        allowed_root: The resolved script path must be under this root directory.
            Must be non-empty - callers must configure ``script_allowed_root`` before using ``script_path``.

    Raises:
        ValueError: If the path is not absolute, allowed_root is empty or inaccessible,
            or the file exceeds MAX_SCRIPT_FILE_BYTES.
        PermissionError: If the resolved path is outside the allowed root.
        FileNotFoundError: If the file does not exist or is not a regular file.
        UnicodeDecodeError: If the file is not valid UTF-8.
    """
    if not Path(script_path).is_absolute():
        raise ValueError(f"script_path must be an absolute path, got: {script_path!r}")

    if not allowed_root:
        raise ValueError("script_allowed_root must be configured when using script_path")

    try:
        resolved = Path(script_path).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise FileNotFoundError(f"Script file not found or not accessible: {script_path!r}") from exc

    try:
        root = Path(allowed_root).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Configured script_allowed_root is not accessible: {allowed_root!r}") from exc
    if not resolved.is_relative_to(root):
        raise PermissionError(f"Script path {str(resolved)!r} is outside the allowed root {str(root)!r}")

    if not resolved.is_file():
        raise FileNotFoundError(f"Script path is not a regular file: {script_path!r}")

    file_size = resolved.stat().st_size
    if file_size > MAX_SCRIPT_FILE_BYTES:
        raise ValueError(
            f"Script file too large ({file_size} bytes). Maximum allowed size is {MAX_SCRIPT_FILE_BYTES} bytes (1 MB)."
        )

    return resolved.read_text(encoding="utf-8")


TOOL_NAMES: list[str] = [
    "artifact_create_preview",
    "artifact_update_preview",
    "artifact_apply",
]


def _parse_and_validate_payload(
    raw_json: str,
    param_name: str,
    artifact_type: str,
    script_path: str,
    allowed_root: str,
    correlation_id: str,
) -> tuple[dict[str, Any], list[str], str | None] | str:
    """Parse, validate, and enrich a JSON payload for artifact write operations.

    Returns a ``(data_dict, warnings, script_field)`` tuple on success, or a
    formatted error response string when validation fails. ``script_field``
    is the name of the field that received script-file content, or ``None``
    when ``script_path`` was empty.

    Args:
        raw_json: The raw JSON string from the caller.
        param_name: Human-readable parameter name for error messages (e.g. 'data', 'changes').
        artifact_type: The artifact type key used for SCRIPT_FIELD_MAP lookup.
        script_path: Optional path to a local script file.
        allowed_root: When non-empty, constrains script_path resolution.
        correlation_id: Correlation ID for error envelopes.
    """
    parsed = json.loads(raw_json)
    if not isinstance(parsed, dict):
        return format_response(
            data=None,
            correlation_id=correlation_id,
            status="error",
            error=f"'{param_name}' must be a JSON object, not " + type(parsed).__name__,
        )
    payload: dict[str, Any] = parsed

    for key in payload:
        validate_identifier(key)

    warnings: list[str] = []
    script_field: str | None = None

    if script_path:
        content = _read_script_file(script_path, allowed_root)
        script_field = SCRIPT_FIELD_MAP.get(artifact_type, DEFAULT_SCRIPT_FIELD)
        if script_field in payload:
            warnings.append(f"'{script_field}' field in {param_name} was overridden by script_path content.")
        payload[script_field] = content

    return payload, warnings, script_field


def _script_fields_for(table: str, explicit_script_field: str | None) -> frozenset[str]:
    """Return the set of fields that should be treated as script bodies.

    Combines the per-table registry from ``TABLE_SCRIPT_FIELDS`` with the
    explicit field that received content from ``script_path`` (if any). The
    explicit field matters for artifacts whose table is not in the registry.
    """
    registered = TABLE_SCRIPT_FIELDS.get(table.lower(), frozenset())
    if explicit_script_field is None:
        return registered
    return registered | {explicit_script_field}


def _summarise_field(
    field_name: str,
    value: Any,
    *,
    script_fields: frozenset[str],
) -> Any:
    """Return a preview-safe summary of a single field value.

    Masks sensitive-named fields with ``MASK_VALUE``. Script-body fields are
    replaced with a ``{"size_bytes", "head"}`` summary so the caller can
    verify the script without the full body leaking through the envelope.
    """
    if is_sensitive_field(field_name):
        return MASK_VALUE

    if field_name in script_fields:
        text = value if isinstance(value, str) else "" if value is None else str(value)
        return {
            "size_bytes": len(text.encode("utf-8")),
            "head": text[:_SCRIPT_HEAD_CHARS],
        }

    return value


def _summarise_payload(
    payload: dict[str, Any],
    *,
    script_fields: frozenset[str],
) -> dict[str, Any]:
    """Produce a preview-safe view of a full payload dict."""
    return {key: _summarise_field(key, value, script_fields=script_fields) for key, value in payload.items()}


def _build_preview_diff(
    changes: dict[str, Any],
    current: dict[str, Any],
    *,
    script_fields: frozenset[str],
) -> dict[str, dict[str, Any]]:
    """Build an update preview diff that masks credentials and summarises script bodies."""
    diff: dict[str, dict[str, Any]] = {}
    for field, new_value in changes.items():
        old_value = current.get(field, "")
        if is_sensitive_field(field):
            diff[field] = {"old": MASK_VALUE, "new": MASK_VALUE}
            continue
        if field in script_fields:
            diff[field] = {
                "old": _script_summary(old_value) if old_value != "" else None,
                "new": _script_summary(new_value),
            }
            continue
        diff[field] = {"old": old_value, "new": new_value}
    return diff


def _script_summary(value: Any) -> dict[str, Any]:
    """Return a ``{"size_bytes", "head"}`` summary for a script-body value."""
    text = value if isinstance(value, str) else "" if value is None else str(value)
    return {"size_bytes": len(text.encode("utf-8")), "head": text[:_SCRIPT_HEAD_CHARS]}


def _mask_current_for_diff(current: dict[str, Any], table: str) -> dict[str, Any]:
    """Mask the full current record for the ``before`` side of the diff envelope."""
    return mask_sensitive_fields(current, table=table)


async def _execute_apply(
    client: ServiceNowClient,
    payload: dict[str, Any],
    correlation_id: str,
) -> str:
    """Execute the write stored in a preview payload and return the tool response."""
    action = payload["action"]
    table = payload["table"]
    artifact_type = payload["artifact_type"]

    if action == "create":
        created = await client.create_record(table, payload["data"])
        return format_response(
            data={
                "action": "create",
                "table": table,
                "artifact_type": artifact_type,
                "sys_id": created["sys_id"],
                "record": mask_sensitive_fields(created, table=table),
            },
            correlation_id=correlation_id,
        )

    if action == "update":
        sys_id = payload["sys_id"]
        updated = await client.update_record(table, sys_id, payload["changes"])
        return format_response(
            data={
                "action": "update",
                "table": table,
                "artifact_type": artifact_type,
                "sys_id": sys_id,
                "record": mask_sensitive_fields(updated, table=table),
            },
            correlation_id=correlation_id,
        )

    return format_response(
        data=None,
        correlation_id=correlation_id,
        status="error",
        error=f"Unknown preview action: '{action}'",
    )


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register artifact write tools on the MCP server."""

    # Closure-local preview store so token scope is bounded by the MCP
    # instance lifetime. Matches record_write's shape.
    preview_store = PreviewTokenStore(ttl_seconds=_PREVIEW_TTL_SECONDS)

    @mcp.tool()
    @tool_handler
    async def artifact_create_preview(
        artifact_type: str,
        data: str,
        script_path: str = "",
        *,
        correlation_id: str = "",
    ) -> str:
        """Preview creating a platform artifact. Returns a token for artifact_apply.

        The preview never echoes script bodies or values for sensitive fields.
        Script content is summarised as ``{size_bytes, head}`` (first 80 chars).

        Args:
            artifact_type: The artifact type (e.g. 'business_rule', 'script_include', 'client_script').
            data: A JSON string of field-value pairs for the new artifact.
            script_path: Optional absolute path to a local script file. When provided, the file content is read and set as the artifact's script field.
        """
        table = _resolve_writable_artifact_table(artifact_type)
        check_table_access(table)

        blocked = write_gate(table, settings, correlation_id)
        if blocked:
            return blocked

        result = _parse_and_validate_payload(
            data, "data", artifact_type, script_path, settings.script_allowed_root, correlation_id
        )
        if isinstance(result, str):
            return result
        data_dict, warnings, script_field = result

        token = preview_store.create(
            {
                "action": "create",
                "table": table,
                "artifact_type": artifact_type,
                "data": data_dict,
            }
        )

        script_fields = _script_fields_for(table, script_field)
        summary = _summarise_payload(data_dict, script_fields=script_fields)
        return format_response(
            data={
                "token": token,
                "action": "create",
                "table": table,
                "artifact_type": artifact_type,
                "fields": sorted(data_dict.keys()),
                "summary": summary,
                "script_field": script_field,
                "ttl_seconds": _PREVIEW_TTL_SECONDS,
            },
            correlation_id=correlation_id,
            warnings=warnings or None,
        )

    @mcp.tool()
    @tool_handler
    async def artifact_update_preview(
        artifact_type: str,
        sys_id: str,
        changes: str,
        script_path: str = "",
        *,
        correlation_id: str = "",
    ) -> str:
        """Preview updating a platform artifact. Returns a token for artifact_apply.

        Fetches the current record and returns a field-level diff that masks
        sensitive fields on both sides and summarises script-body fields as
        ``{size_bytes, head}`` rather than echoing their content.

        Args:
            artifact_type: The artifact type (e.g. 'business_rule', 'script_include', 'client_script').
            sys_id: The sys_id of the artifact to update.
            changes: A JSON string of field-value pairs to update.
            script_path: Optional absolute path to a local script file. When provided, the file content is read and set as the artifact's script field.
        """
        table = _resolve_writable_artifact_table(artifact_type)
        check_table_access(table)

        blocked = write_gate(table, settings, correlation_id)
        if blocked:
            return blocked

        validate_sys_id(sys_id)

        result = _parse_and_validate_payload(
            changes, "changes", artifact_type, script_path, settings.script_allowed_root, correlation_id
        )
        if isinstance(result, str):
            return result
        changes_dict, warnings, script_field = result

        async with ServiceNowClient(settings, auth_provider) as client:
            current = await client.get_record(table, sys_id)

        script_fields = _script_fields_for(table, script_field)
        diff = _build_preview_diff(changes_dict, current, script_fields=script_fields)

        token = preview_store.create(
            {
                "action": "update",
                "table": table,
                "artifact_type": artifact_type,
                "sys_id": sys_id,
                "changes": changes_dict,
            }
        )

        return format_response(
            data={
                "token": token,
                "action": "update",
                "table": table,
                "artifact_type": artifact_type,
                "sys_id": sys_id,
                "fields": sorted(changes_dict.keys()),
                "diff": diff,
                "before": _mask_current_for_diff(current, table),
                "script_field": script_field,
                "ttl_seconds": _PREVIEW_TTL_SECONDS,
            },
            correlation_id=correlation_id,
            warnings=warnings or None,
        )

    @mcp.tool()
    @tool_handler
    async def artifact_apply(preview_token: str, *, correlation_id: str = "") -> str:
        """Apply a previously previewed artifact create/update using its token.

        Re-evaluates :func:`check_table_access` and :func:`write_gate` at
        apply time so a policy change between preview and apply cannot be
        ratified by a previously-issued token. Tokens are single-use and
        expire after the preview TTL.

        Args:
            preview_token: The single-use token returned by artifact_create_preview or artifact_update_preview.
        """
        payload = preview_store.consume(preview_token)
        if payload is None:
            return format_response(
                data=None,
                correlation_id=correlation_id,
                status="error",
                error="Invalid or expired preview token",
            )

        table = payload["table"]
        check_table_access(table)

        blocked = write_gate(table, settings, correlation_id)
        if blocked:
            return blocked

        async with ServiceNowClient(settings, auth_provider) as client:
            return await _execute_apply(client, payload, correlation_id)
