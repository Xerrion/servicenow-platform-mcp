"""Write operations for ServiceNow platform artifacts."""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import gate_write, mask_sensitive_fields
from servicenow_mcp.tools._artifact import (
    DEFAULT_SCRIPT_FIELD,
    MAX_SCRIPT_FILE_BYTES,
    SCRIPT_FIELD_MAP,
    WRITABLE_ARTIFACT_TABLES,
    _read_script_file,
    _resolve_writable_artifact_table,
)
from servicenow_mcp.tools._payload import parse_payload_json
from servicenow_mcp.utils import format_response, validate_sys_id


logger = logging.getLogger(__name__)


# Re-exported for backward compatibility with existing imports/tests.
__all__ = [
    "DEFAULT_SCRIPT_FIELD",
    "MAX_SCRIPT_FILE_BYTES",
    "SCRIPT_FIELD_MAP",
    "TOOL_NAMES",
    "WRITABLE_ARTIFACT_TABLES",
    "_parse_and_validate_payload",
    "_read_script_file",
    "_resolve_writable_artifact_table",
    "register_tools",
]


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

    Returns a ``(payload, warnings)`` tuple on success, or a formatted error
    response string when validation fails. Delegates JSON parsing, size/depth
    caps, and identifier validation of top-level keys to ``parse_payload_json``;
    adds artifact-specific behavior on top:

      * Maps ``script_path`` content into the per-artifact script field via
        ``SCRIPT_FIELD_MAP`` (defaulting to ``"script"``).
      * Emits a warning when the script_path content overrides an existing
        field key in the supplied payload.

    Args:
        raw_json: The raw JSON string from the caller.
        param_name: Human-readable parameter name for error messages (e.g. 'data', 'changes').
        artifact_type: The artifact type key used for SCRIPT_FIELD_MAP lookup.
        script_path: Optional path to a local script file.
        allowed_root: When non-empty, constrains script_path resolution.
        correlation_id: Correlation ID for error envelopes.
    """
    parsed = parse_payload_json(raw_json, field_name=param_name, correlation_id=correlation_id)
    if isinstance(parsed, str):
        return parsed
    payload: dict[str, Any] = parsed

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

        blocked = gate_write(table, settings, correlation_id)
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

        blocked = gate_write(table, settings, correlation_id)
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
