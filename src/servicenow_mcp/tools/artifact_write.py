"""Write operations for ServiceNow platform artifacts."""

import json
import logging
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import check_table_access, mask_sensitive_fields, write_gate
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


TOOL_NAMES: list[str] = ["artifact_create", "artifact_update"]


def _parse_and_validate_payload(
    raw_json: str,
    param_name: str,
    artifact_type: str,
    script_path: str,
    allowed_root: str,
    correlation_id: str,
) -> tuple[dict[str, Any], list[str]] | str:
    """Parse, validate, and enrich a JSON payload for artifact write operations.

    Returns a ``(data_dict, warnings)`` tuple on success, or a formatted error
    response string when validation fails.

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

    if script_path:
        content = _read_script_file(script_path, allowed_root)
        script_field = SCRIPT_FIELD_MAP.get(artifact_type, DEFAULT_SCRIPT_FIELD)
        if script_field in payload:
            warnings.append(f"'{script_field}' field in {param_name} was overridden by script_path content.")
        payload[script_field] = content

    return payload, warnings


def register_tools(mcp: FastMCP, settings: Settings, auth_provider: BasicAuthProvider) -> None:
    """Register artifact write tools on the MCP server."""

    @mcp.tool()
    @tool_handler
    async def artifact_create(
        artifact_type: str,
        data: str,
        script_path: str = "",
        *,
        correlation_id: str = "",
    ) -> str:
        """Create a new platform artifact in ServiceNow.

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
        data_dict, warnings = result

        async with ServiceNowClient(settings, auth_provider) as client:
            created = await client.create_record(table, data_dict)

        return format_response(
            data={
                "table": table,
                "artifact_type": artifact_type,
                "sys_id": created["sys_id"],
                "record": mask_sensitive_fields(created),
            },
            correlation_id=correlation_id,
            warnings=warnings or None,
        )

    @mcp.tool()
    @tool_handler
    async def artifact_update(
        artifact_type: str,
        sys_id: str,
        changes: str,
        script_path: str = "",
        *,
        correlation_id: str = "",
    ) -> str:
        """Update an existing platform artifact in ServiceNow.

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
        changes_dict, warnings = result

        async with ServiceNowClient(settings, auth_provider) as client:
            updated = await client.update_record(table, sys_id, changes_dict)

        return format_response(
            data={
                "table": table,
                "artifact_type": artifact_type,
                "sys_id": sys_id,
                "record": mask_sensitive_fields(updated),
            },
            correlation_id=correlation_id,
            warnings=warnings or None,
        )
