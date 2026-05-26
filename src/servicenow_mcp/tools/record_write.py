"""Unified ``record_write`` and ``record_apply`` tools.

Folds together six legacy CRUD tools (``record_create`` / ``record_update`` /
``record_delete`` plus their preview variants) and the two artifact tools
(``artifact_create`` / ``artifact_update``) into a single action-dispatching
surface:

* ``record_write(action, table, ...)`` - dispatches on ``action``
  (create/update/delete). ``preview=True`` (default) returns a single-use
  token; ``preview=False`` commits immediately. Script-bearing fields are
  discovered dynamically via ``DictionaryRegistry``; ``script_path`` reads a
  local file into the resolved field and validates XML when the field's
  ``internal_type`` is ``xml``.
* ``record_apply(preview_token)`` - commits a previously previewed write.
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
from servicenow_mcp.tools._artifact import _read_script_file, validate_ui_macro_xml
from servicenow_mcp.tools._dictionary import DictionaryRegistry, ScriptField
from servicenow_mcp.tools._payload import parse_payload_json
from servicenow_mcp.tools._record_helpers import _build_update_diff, _check_mandatory_or_error
from servicenow_mcp.utils import format_response, validate_identifier, validate_sys_id


TOOL_NAMES: list[str] = ["record_write", "record_apply"]

_VALID_ACTIONS: Final[frozenset[str]] = frozenset({"create", "update", "delete"})


# ---------------------------------------------------------------------------
# Error helpers
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

    if not table:
        return _err(correlation_id, "table is required.")

    if script_field and not script_path:
        return _err(correlation_id, "script_field requires script_path to be set.")

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
# Script-path injection (dictionary-driven)
# ---------------------------------------------------------------------------


def _pick_target_field(
    detected: list[ScriptField],
    requested: str,
    table: str,
    correlation_id: str,
) -> ScriptField | str:
    """Resolve which detected script field receives the ``script_path`` content.

    Returns the chosen ``ScriptField`` on success or a serialized error envelope.
    The default (empty ``requested``) is the first detected field; the registry
    already orders child-first and sys_dictionary row order within a table.
    """
    if not detected:
        return _err(
            correlation_id,
            f"Table {table!r} has no script-bearing fields detectable from sys_dictionary.",
        )

    if not requested:
        return detected[0]

    for field in detected:
        if field.name == requested:
            return field

    allowed = ", ".join(f.name for f in detected)
    return _err(
        correlation_id,
        f"Invalid script_field {requested!r} for table {table!r}. Detected script fields: [{allowed}].",
    )


async def _inject_script_path(
    parsed: dict[str, Any],
    table: str,
    script_path: str,
    script_field: str,
    allowed_root: str,
    dictionary: DictionaryRegistry,
    correlation_id: str,
) -> tuple[dict[str, Any], list[str]] | str:
    """Read ``script_path`` and inject content into the resolved script field.

    Discovers script fields via ``DictionaryRegistry``. When ``script_field``
    is empty, writes to the first detected field. When the resolved field has
    ``internal_type == 'xml'``, the content is validated as well-formed XML
    before any platform call. Returns ``(updated_payload, warnings)`` on
    success, or a serialized error envelope.
    """
    detected = await dictionary.get_script_fields(table)
    pick = _pick_target_field(detected, script_field, table, correlation_id)
    if isinstance(pick, str):
        return pick
    target = pick

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

    if target.internal_type == "xml":
        xml_error = validate_ui_macro_xml(content)
        if xml_error:
            return _err(correlation_id, xml_error)

    warnings: list[str] = []
    if target.name in parsed:
        warnings.append(f"'{target.name}' field in data was overridden by script_path content.")
    parsed[target.name] = content
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
# Apply dispatch
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
    dictionary: DictionaryRegistry | None = None,
) -> None:
    """Register the unified ``record_write`` and ``record_apply`` tools."""
    del choices  # unused; signature retained for loader parity

    if dictionary is None:
        dictionary = DictionaryRegistry(settings, auth_provider)
    dict_registry = dictionary

    # Closure-scoped preview store.
    preview_store = PreviewTokenStore()

    @mcp.tool()
    @tool_handler
    async def record_write(
        action: str,
        table: str = "",
        sys_id: str = "",
        data: str = "",
        script_path: str = "",
        script_field: str = "",
        preview: bool = True,
        *,
        correlation_id: str = "",
    ) -> str:
        """Create, update, or delete a record. Defaults to preview mode.

        Script-bearing tables (Business Rules, Script Includes, widgets, etc.)
        are not special-cased - script-field discovery happens at runtime via
        ``sys_dictionary`` and the table's super_class chain. ``script_path``
        loads a local file into the resolved target field; ``script_field``
        names the destination column when more than one script-bearing field
        is detected.

        Args:
            action: 'create' | 'update' | 'delete'.
            table: Target table. Required.
            sys_id: Required for 'update' and 'delete'.
            data: JSON string of field values. Required for 'create' and
                'update'.
            script_path: Optional absolute path to a local script file. Content
                is read (UTF-8, max 1 MB) and stored under the resolved
                target script field. Path resolved with strict=True;
                constrained to ``settings.script_allowed_root``. When the
                resolved field has ``internal_type == 'xml'``, the content is
                validated as well-formed XML before any platform call.
            script_field: Optional override for the destination script field.
                When empty (default), the first detected script field is used
                (child-first, ``sys_dictionary`` row order within a table).
                When set, must match one of the script-bearing fields detected
                from ``sys_dictionary``; otherwise the call returns a
                structured error listing the detected fields. Use
                ``describe(action='list_script_fields', table=...)`` to
                discover script fields for a table.
            preview: When True (default) returns a preview_token; caller
                invokes record_apply to commit. When False, write commits
                immediately.
        """
        # --- 1. Cross-argument validation (early exit) -------------------
        err = _validate_action_args(action, table, sys_id, data, script_path, script_field, correlation_id)
        if err:
            return err

        validate_identifier(table)

        # --- 2. Policy gate ----------------------------------------------
        blocked = gate_write(table, settings, correlation_id)
        if blocked:
            return blocked

        # --- 3. sys_id validation ----------------------------------------
        if sys_id:
            validate_sys_id(sys_id)

        # --- 4. Parse JSON payload (create/update only) ------------------
        parsed_data: dict[str, Any] = {}
        warnings: list[str] = []
        if action != "delete":
            parsed = parse_payload_json(data, field_name="data", correlation_id=correlation_id)
            if isinstance(parsed, str):
                return parsed
            parsed_data = parsed

            # --- 5. Script-path injection (dictionary-driven) ------------
            if script_path:
                injected = await _inject_script_path(
                    parsed_data,
                    table,
                    script_path,
                    script_field,
                    settings.script_allowed_root,
                    dict_registry,
                    correlation_id,
                )
                if isinstance(injected, str):
                    return injected
                parsed_data, warnings = injected

        # --- 6. Dispatch -------------------------------------------------
        extra_data: dict[str, Any] = {}
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
