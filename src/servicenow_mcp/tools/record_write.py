"""Unified ``record_write`` and ``record_apply`` tools.

Folds together six legacy CRUD tools (``record_create`` / ``record_update`` /
``record_delete`` plus their preview variants) and the two artifact tools
(``artifact_create`` / ``artifact_update``) into a single action-dispatching
surface:

* ``record_write(action, table, ...)`` - dispatches on ``action``
  (create/update/delete). ``preview=True`` (default) returns a single-use
  token; ``preview=False`` commits immediately. All field values are supplied
  inline in ``data``. Dictionary metadata identifies XML fields for validation.
* ``record_apply(preview_token)`` - commits a previously previewed write.
"""

from __future__ import annotations

from typing import Any, Final

from mcp.server import MCPServer

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.policy import (
    check_table_access,
    gate_write,
    mask_sensitive_fields,
    write_gate,
)
from servicenow_mcp.state import PreviewTokenStore
from servicenow_mcp.tools._artifact import validate_ui_macro_xml
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.tools._payload import parse_payload_json
from servicenow_mcp.tools._record_helpers import _build_update_diff, _check_mandatory_or_error
from servicenow_mcp.utils import format_response, validate_identifier, validate_sys_id


TOOL_NAMES: list[str] = ["record_write", "record_apply"]

_VALID_ACTIONS: Final[frozenset[str]] = frozenset({"create", "update", "delete"})

# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------


def _err(message: str) -> str:
    """Return a serialized error envelope with the given message."""
    return format_response(data=None, status="error", error=message)


# ---------------------------------------------------------------------------
# Argument validation (parse-don't-validate: turn raw args into trusted shape)
# ---------------------------------------------------------------------------


def _validate_create_args(sys_id: str, data: str) -> str | None:
    """Validate per-action constraints for ``action='create'``."""
    if not data:
        return _err("data is required for action='create'.")
    if sys_id:
        return _err("sys_id must be empty for action='create'.")
    return None


def _validate_update_args(sys_id: str, data: str) -> str | None:
    """Validate per-action constraints for ``action='update'``."""
    if not sys_id:
        return _err("sys_id is required for action='update'.")
    if not data:
        return _err("data is required for action='update'.")
    return None


def _validate_delete_args(sys_id: str, data: str) -> str | None:
    """Validate per-action constraints for ``action='delete'``."""
    if not sys_id:
        return _err("sys_id is required for action='delete'.")
    if data:
        return _err("data must be empty for action='delete'.")
    return None


def _validate_action_args(
    action: str,
    table: str,
    sys_id: str,
    data: str,
) -> str | None:
    """Validate the cross-argument constraints. Returns error envelope or None."""
    if action not in _VALID_ACTIONS:
        return _err(
            f"Unknown action {action!r}. Valid actions: {sorted(_VALID_ACTIONS)}.",
        )

    if not table:
        return _err("table is required.")

    # Per-action argument checks delegated to focused validators.
    if action == "create":
        return _validate_create_args(sys_id, data)
    if action == "update":
        return _validate_update_args(sys_id, data)
    # delete (membership in _VALID_ACTIONS narrows the action enum).
    return _validate_delete_args(sys_id, data)


# ---------------------------------------------------------------------------
# Preview/direct dispatch bodies
# ---------------------------------------------------------------------------


async def _run_create(
    client: ServiceNowClient,
    table: str,
    parsed_data: dict[str, Any],
    preview: bool,
    preview_store: PreviewTokenStore,
    extra_data: dict[str, Any],
    dictionary: DictionaryRegistry,
) -> str:
    """Run a create action in either preview or direct mode."""
    err = await _check_mandatory_or_error(client, table, parsed_data, dictionary)
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
    )


async def _run_update(
    client: ServiceNowClient,
    table: str,
    sys_id: str,
    parsed_data: dict[str, Any],
    preview: bool,
    preview_store: PreviewTokenStore,
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
    )


async def _run_delete(
    client: ServiceNowClient,
    table: str,
    sys_id: str,
    preview: bool,
    preview_store: PreviewTokenStore,
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
        )

    await client.delete_record(table, sys_id)
    return format_response(
        data={"action": "delete", "table": table, "sys_id": sys_id, "deleted": True, **extra_data},
    )


# ---------------------------------------------------------------------------
# Payload preparation + dispatch (used by record_write)
# ---------------------------------------------------------------------------


async def _prepare_payload(
    action: str,
    data: str,
    table: str,
    dictionary: DictionaryRegistry,
) -> dict[str, Any] | str:
    """Parse the bounded field map and reject invalid XML before staging a write."""
    if action == "delete":
        return {}

    parsed = parse_payload_json(data, field_name="data")
    if isinstance(parsed, str):
        return parsed
    fields = await dictionary.get_fields(table, list(parsed))
    for field in fields:
        if field.internal_type != "xml":
            continue
        content = parsed[field.name]
        if not isinstance(content, str):
            return _err(f"XML field {field.name!r} must be a string.")
        xml_error = validate_ui_macro_xml(content)
        if xml_error:
            return _err(f"Field {field.name!r}: {xml_error}")
    return parsed


async def _dispatch_record_write(
    client: ServiceNowClient,
    action: str,
    table: str,
    sys_id: str,
    parsed_data: dict[str, Any],
    preview: bool,
    preview_store: PreviewTokenStore,
    extra_data: dict[str, Any],
    dictionary: DictionaryRegistry,
) -> str:
    """Route a validated ``record_write`` request to its ``_run_*`` helper."""
    if action == "create":
        return await _run_create(client, table, parsed_data, preview, preview_store, extra_data, dictionary)
    if action == "update":
        return await _run_update(client, table, sys_id, parsed_data, preview, preview_store, extra_data)
    # delete - membership in _VALID_ACTIONS narrows the action enum.
    return await _run_delete(client, table, sys_id, preview, preview_store, extra_data)


# ---------------------------------------------------------------------------
# Apply dispatch
# ---------------------------------------------------------------------------


async def _apply_payload(
    client: ServiceNowClient,
    payload: dict[str, Any],
    table: str,
    dictionary: DictionaryRegistry,
) -> str:
    """Execute a previously previewed action."""
    action = payload["action"]

    if action == "create":
        err = await _check_mandatory_or_error(client, table, payload["data"], dictionary)
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
        )

    if action == "delete":
        sys_id = payload["sys_id"]
        await client.delete_record(table, sys_id)
        return format_response(
            data={"action": "delete", "table": table, "sys_id": sys_id, "deleted": True},
        )

    return _err(f"Unknown preview action: {action!r}")


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_tools(
    mcp: MCPServer,
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
    client_factory: ServiceNowClientProvider | None = None,
) -> None:
    """Register the unified ``record_write`` and ``record_apply`` tools."""
    del choices  # unused; signature retained for loader parity
    client_factory = client_factory or (lambda: ServiceNowClient(settings, auth_provider))

    if dictionary is None:
        dictionary = DictionaryRegistry(settings, auth_provider, client_factory)
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
        preview: bool = True,
    ) -> str:
        """Create, update, or delete a record. Defaults to preview mode.

        Supply all field values, including complete script or markup strings,
        in ``data``. Omitted fields stay unchanged on update. Dictionary
        metadata identifies supplied XML fields, including inherited fields;
        malformed XML is rejected before preview creation or mutation.
        Creates also check inherited mandatory fields, with child declarations
        taking precedence. Metadata request errors block writes.

        Args:
            action: 'create' | 'update' | 'delete'.
            table: Target table. Required.
            sys_id: Required for 'update' and 'delete'.
            data: JSON string mapping field names to values, including any
                script fields. Required for 'create' and 'update'. Maximum
                256 KiB of UTF-8 JSON, including escaping and field names.
            preview: When True (default) returns a preview_token; caller
                invokes record_apply to commit. When False, write commits
                immediately.
        """
        # --- 1. Cross-argument validation (early exit) -------------------
        err = _validate_action_args(action, table, sys_id, data)
        if err:
            return err

        validate_identifier(table)

        # --- 2. Policy gate ----------------------------------------------
        blocked = gate_write(table, settings)
        if blocked:
            return blocked

        # --- 3. sys_id validation ----------------------------------------
        if sys_id:
            validate_sys_id(sys_id)

        # --- 4. Payload prep ---------------------------------------------
        prepared = await _prepare_payload(
            action,
            data,
            table,
            dict_registry,
        )
        if isinstance(prepared, str):
            return prepared
        parsed_data = prepared

        # --- 5. Dispatch -------------------------------------------------
        extra_data: dict[str, Any] = {}
        async with client_factory() as client:
            return await _dispatch_record_write(
                client,
                action,
                table,
                sys_id,
                parsed_data,
                preview,
                preview_store,
                extra_data,
                dict_registry,
            )

    @mcp.tool()
    @tool_handler
    async def record_apply(
        preview_token: str,
    ) -> str:
        """Commit a previously previewed write. Single-use token.

        Args:
            preview_token: The token returned by ``record_write`` in preview
                mode. Single-use - consumed on success or failure.
        """
        payload = await preview_store.consume(preview_token)
        if payload is None:
            return _err("Invalid or expired preview token")

        table = payload["table"]

        # Defense in depth - re-check policy gates before committing.
        check_table_access(table)
        blocked = write_gate(table, settings)
        if blocked:
            return blocked

        async with client_factory() as client:
            return await _apply_payload(client, payload, table, dict_registry)
