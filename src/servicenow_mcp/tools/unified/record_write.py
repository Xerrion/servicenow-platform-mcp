"""Unified ``record_write`` and ``record_apply`` tools.

Folds together six legacy CRUD tools (``record_create`` / ``record_update`` /
``record_delete`` plus their preview variants) and the two artifact tools
(``artifact_create`` / ``artifact_update``) into a single action-dispatching
surface:

* ``record_write(action, ...)`` - dispatches on ``action`` (create/update/delete).
  When ``artifact_type`` is set, it routes to the artifact's table and applies
  the SCRIPT_FIELD_MAP rules; otherwise it is a plain record write.
  ``preview=True`` (default) returns a single-use token; ``preview=False``
  commits immediately.
* ``record_apply(preview_token)`` - commits a previously previewed write.

Old tools remain registered alongside this one until Phase 3b retires them.
"""

from __future__ import annotations

from typing import Any, Final

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import (
    check_table_access,
    gate_write,
    mask_sensitive_fields,
    write_gate,
)
from servicenow_mcp.state import PreviewTokenStore
from servicenow_mcp.tools._artifact import (
    SCRIPT_FIELD_MAP,
    _read_script_file,
    _resolve_writable_artifact_table,
    _validate_xml_content,
    primary_script_field,
)
from servicenow_mcp.tools._payload import parse_payload_json
from servicenow_mcp.tools._record_helpers import _build_update_diff, _check_mandatory_or_error
from servicenow_mcp.utils import format_response, validate_sys_id


TOOL_NAMES: list[str] = ["record_write", "record_apply"]

_VALID_ACTIONS: Final[frozenset[str]] = frozenset({"create", "update", "delete"})


# ---------------------------------------------------------------------------
# Error helpers (small, stateless - keep tool body readable)
# ---------------------------------------------------------------------------


def _err(correlation_id: str, message: str) -> str:
    """Return a serialized error envelope with the given message."""
    return format_response(data=None, correlation_id=correlation_id, status="error", error=message)


# ---------------------------------------------------------------------------
# Argument validation (parse-don't-validate: turn raw args into trusted shape)
# ---------------------------------------------------------------------------


def _validate_action_args(
    action: str,
    table: str,
    sys_id: str,
    data: str,
    artifact_type: str,
    script_path: str,
    script_field: str,
    correlation_id: str,
) -> str | None:
    """Validate the cross-argument constraints. Returns error envelope or None."""
    if action not in _VALID_ACTIONS:
        return _err(
            correlation_id,
            f"Unknown action {action!r}. Valid actions: {sorted(_VALID_ACTIONS)}.",
        )

    if not artifact_type and not table:
        return _err(correlation_id, "table is required when artifact_type is not set.")

    if script_path and not artifact_type:
        return _err(correlation_id, "script_path requires artifact_type to be set.")

    if script_field and not artifact_type:
        return _err(correlation_id, "script_field requires artifact_type to be set.")

    # Per-action argument checks.
    if action == "create":
        if not data:
            return _err(correlation_id, "data is required for action='create'.")
        if sys_id:
            return _err(correlation_id, "sys_id must be empty for action='create'.")
    elif action == "update":
        if not sys_id:
            return _err(correlation_id, "sys_id is required for action='update'.")
        if not data:
            return _err(correlation_id, "data is required for action='update'.")
    else:  # delete
        if not sys_id:
            return _err(correlation_id, "sys_id is required for action='delete'.")
        if data:
            return _err(correlation_id, "data must be empty for action='delete'.")
    return None


# ---------------------------------------------------------------------------
# Artifact script-path injection
# ---------------------------------------------------------------------------


def _inject_script_path(
    parsed: dict[str, Any],
    artifact_type: str,
    script_path: str,
    script_field: str,
    allowed_root: str,
    correlation_id: str,
) -> tuple[dict[str, Any], list[str]] | str:
    """Read script_path and inject content into the artifact's target script field.

    The destination field is ``script_field`` when supplied (must be one of
    ``SCRIPT_FIELD_MAP[artifact_type]``), otherwise the primary field.

    Returns ``(updated_payload, warnings)`` on success, or a serialized error
    envelope on file-read failures or invalid ``script_field``.
    """
    allowed_fields = SCRIPT_FIELD_MAP[artifact_type]
    if script_field:
        if script_field not in allowed_fields:
            return _err(
                correlation_id,
                f"Invalid script_field {script_field!r} for artifact_type "
                f"{artifact_type!r}. Allowed: {allowed_fields}.",
            )
        target_field = script_field
    else:
        target_field = primary_script_field(artifact_type)

    try:
        content = _read_script_file(script_path, allowed_root)
    except ValueError as exc:
        return _err(correlation_id, f"Invalid script_path: {exc}")
    except FileNotFoundError as exc:
        return _err(correlation_id, str(exc))
    except PermissionError as exc:
        return _err(correlation_id, f"script_path security violation: {exc}")
    except UnicodeDecodeError as exc:
        return _err(correlation_id, f"script_path is not valid UTF-8: {exc}")

    if artifact_type == "ui_macro":
        xml_error = _validate_xml_content(content)
        if xml_error:
            return _err(correlation_id, xml_error)

    warnings: list[str] = []
    if target_field in parsed:
        warnings.append(f"'{target_field}' field in data was overridden by script_path content.")
    parsed[target_field] = content
    return parsed, warnings


# ---------------------------------------------------------------------------
# Preview/direct dispatch bodies
# ---------------------------------------------------------------------------


async def _run_create(
    client: ServiceNowClient,
    table: str,
    parsed_data: dict[str, Any],
    preview: bool,
    preview_store: PreviewTokenStore,
    correlation_id: str,
    warnings: list[str],
    extra_data: dict[str, Any],
) -> str:
    """Run a create action in either preview or direct mode."""
    err = await _check_mandatory_or_error(client, table, parsed_data, correlation_id)
    if err:
        return err

    if preview:
        token = await preview_store.create(
            {"action": "create", "table": table, "data": parsed_data},
        )
        return format_response(
            data={
                "action": "create",
                "table": table,
                "preview_token": token,
                "preview": {"data": mask_sensitive_fields(parsed_data), **extra_data},
            },
            correlation_id=correlation_id,
            warnings=warnings or None,
        )

    created = await client.create_record(table, parsed_data)
    return format_response(
        data={
            "action": "create",
            "table": table,
            "sys_id": created["sys_id"],
            "record": mask_sensitive_fields(created),
            **extra_data,
        },
        correlation_id=correlation_id,
        warnings=warnings or None,
    )


async def _run_update(
    client: ServiceNowClient,
    table: str,
    sys_id: str,
    parsed_data: dict[str, Any],
    preview: bool,
    preview_store: PreviewTokenStore,
    correlation_id: str,
    warnings: list[str],
    extra_data: dict[str, Any],
) -> str:
    """Run an update action in either preview or direct mode."""
    if preview:
        current = await client.get_record(table, sys_id)
        diff = _build_update_diff(parsed_data, current)
        token = await preview_store.create(
            {"action": "update", "table": table, "sys_id": sys_id, "changes": parsed_data},
        )
        return format_response(
            data={
                "action": "update",
                "table": table,
                "sys_id": sys_id,
                "preview_token": token,
                "preview": {"diff": diff, **extra_data},
            },
            correlation_id=correlation_id,
            warnings=warnings or None,
        )

    updated = await client.update_record(table, sys_id, parsed_data)
    return format_response(
        data={
            "action": "update",
            "table": table,
            "sys_id": sys_id,
            "record": mask_sensitive_fields(updated),
            **extra_data,
        },
        correlation_id=correlation_id,
        warnings=warnings or None,
    )


async def _run_delete(
    client: ServiceNowClient,
    table: str,
    sys_id: str,
    preview: bool,
    preview_store: PreviewTokenStore,
    correlation_id: str,
    extra_data: dict[str, Any],
) -> str:
    """Run a delete action in either preview or direct mode."""
    if preview:
        snapshot = await client.get_record(table, sys_id)
        token = await preview_store.create(
            {
                "action": "delete",
                "table": table,
                "sys_id": sys_id,
                "record_snapshot": snapshot,
            },
        )
        return format_response(
            data={
                "action": "delete",
                "table": table,
                "sys_id": sys_id,
                "preview_token": token,
                "preview": {"record_snapshot": mask_sensitive_fields(snapshot), **extra_data},
            },
            correlation_id=correlation_id,
        )

    await client.delete_record(table, sys_id)
    return format_response(
        data={"action": "delete", "table": table, "sys_id": sys_id, "deleted": True, **extra_data},
        correlation_id=correlation_id,
    )


# ---------------------------------------------------------------------------
# Apply dispatch (mirrors legacy record_apply._execute_apply_action)
# ---------------------------------------------------------------------------


async def _apply_payload(
    client: ServiceNowClient,
    payload: dict[str, Any],
    table: str,
    correlation_id: str,
) -> str:
    """Execute a previously previewed action."""
    action = payload["action"]

    if action == "create":
        err = await _check_mandatory_or_error(client, table, payload["data"], correlation_id)
        if err:
            return err
        result = await client.create_record(table, payload["data"])
        return format_response(
            data={
                "action": "create",
                "table": table,
                "sys_id": result["sys_id"],
                "record": mask_sensitive_fields(result),
            },
            correlation_id=correlation_id,
        )

    if action == "update":
        sys_id = payload["sys_id"]
        result = await client.update_record(table, sys_id, payload["changes"])
        return format_response(
            data={
                "action": "update",
                "table": table,
                "sys_id": sys_id,
                "record": mask_sensitive_fields(result),
            },
            correlation_id=correlation_id,
        )

    if action == "delete":
        sys_id = payload["sys_id"]
        await client.delete_record(table, sys_id)
        return format_response(
            data={"action": "delete", "table": table, "sys_id": sys_id, "deleted": True},
            correlation_id=correlation_id,
        )

    return _err(correlation_id, f"Unknown preview action: {action!r}")


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
) -> None:
    """Register the unified ``record_write`` and ``record_apply`` tools."""
    del choices  # unused; signature retained for loader parity

    # Closure-scoped preview store (mirrors legacy record_write).
    preview_store = PreviewTokenStore()

    @mcp.tool()
    @tool_handler
    async def record_write(
        action: str,
        table: str = "",
        sys_id: str = "",
        data: str = "",
        artifact_type: str = "",
        script_path: str = "",
        script_field: str = "",
        preview: bool = True,
        *,
        correlation_id: str = "",
    ) -> str:
        """Create, update, or delete a record. Defaults to preview mode.

        Args:
            action: 'create' | 'update' | 'delete'.
            table: Target table. Required when artifact_type is empty. Ignored
                when artifact_type is set (the artifact type's table is used
                instead).
            sys_id: Required for 'update' and 'delete'.
            data: JSON string of field values. Required for 'create' and
                'update'.
            artifact_type: When set, treat as a platform artifact write.
                Resolves to the matching table; data keys validated;
                SCRIPT_FIELD_MAP applied. Use ``describe(action='list_artifact_types')``
                to discover all valid types and their script fields.
            script_path: Optional absolute path to a local script file. Content
                is read (UTF-8, max 1 MB) and stored under the artifact's
                target script field. Path resolved with strict=True; constrained
                to settings.script_allowed_root. Only valid when artifact_type
                is set.
            script_field: Optional override for the destination script field.
                When empty (default), the primary field (index 0 of
                ``SCRIPT_FIELD_MAP[artifact_type]``) is used. When set, must be
                one of the allowed fields for the artifact_type (e.g.
                ``script_false`` for ``ui_policy``, ``template`` or ``css`` for
                ``widget``). Only meaningful when ``script_path`` is also set.
            preview: When True (default) returns a preview_token; caller
                invokes record_apply to commit. When False, write commits
                immediately.
        """
        # --- 1. Cross-argument validation (early exit) -------------------
        err = _validate_action_args(
            action, table, sys_id, data, artifact_type, script_path, script_field, correlation_id
        )
        if err:
            return err

        # --- 2. Mode resolution ------------------------------------------
        extra_data: dict[str, Any] = {}
        if artifact_type:
            try:
                table = _resolve_writable_artifact_table(artifact_type)
            except ValueError as exc:
                return _err(correlation_id, str(exc))
            extra_data["artifact_type"] = artifact_type

        # --- 3. Policy gate ----------------------------------------------
        blocked = gate_write(table, settings, correlation_id)
        if blocked:
            return blocked

        # --- 4. sys_id validation (update/delete only) -------------------
        if sys_id:
            validate_sys_id(sys_id)

        # --- 5. Parse JSON payload (create/update only) ------------------
        parsed_data: dict[str, Any] = {}
        warnings: list[str] = []
        if action != "delete":
            parsed = parse_payload_json(data, field_name="data", correlation_id=correlation_id)
            if isinstance(parsed, str):
                return parsed
            parsed_data = parsed

            # --- 6. Artifact script_path injection -----------------------
            if artifact_type and script_path:
                injected = _inject_script_path(
                    parsed_data,
                    artifact_type,
                    script_path,
                    script_field,
                    settings.script_allowed_root,
                    correlation_id,
                )
                if isinstance(injected, str):
                    return injected
                parsed_data, warnings = injected

        # --- 7. Dispatch -------------------------------------------------
        async with ServiceNowClient(settings, auth_provider) as client:
            if action == "create":
                return await _run_create(
                    client, table, parsed_data, preview, preview_store, correlation_id, warnings, extra_data
                )
            if action == "update":
                return await _run_update(
                    client, table, sys_id, parsed_data, preview, preview_store, correlation_id, warnings, extra_data
                )
            return await _run_delete(client, table, sys_id, preview, preview_store, correlation_id, extra_data)

    @mcp.tool()
    @tool_handler
    async def record_apply(
        preview_token: str,
        *,
        correlation_id: str = "",
    ) -> str:
        """Commit a previously previewed write. Single-use token.

        Args:
            preview_token: The token returned by ``record_write`` in preview
                mode. Single-use - consumed on success or failure.
        """
        payload = await preview_store.consume(preview_token)
        if payload is None:
            return _err(correlation_id, "Invalid or expired preview token")

        table = payload["table"]

        # Defense in depth - re-check policy gates before committing.
        check_table_access(table)
        blocked = write_gate(table, settings, correlation_id)
        if blocked:
            return blocked

        async with ServiceNowClient(settings, auth_provider) as client:
            return await _apply_payload(client, payload, table, correlation_id)
