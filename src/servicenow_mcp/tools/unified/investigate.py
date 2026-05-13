"""Unified ``investigate`` tool: run investigations or explain findings.

This module collapses ``investigate_run`` and ``investigate_explain`` into one
action-dispatching tool. Two mutually exclusive actions:

1. ``run``     -> dispatches to ``module.run(client, params_dict)`` for a named investigation.
2. ``explain`` -> dispatches to a registered module's ``explain(client, element_id)``.

Old tools stay registered alongside this one until Phase 3b retires them.
"""

from __future__ import annotations

from typing import Any, Final

from mcp.server.fastmcp import FastMCP

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.choices import ChoiceRegistry
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.decorators import tool_handler
from servicenow_mcp.investigation_helpers import parse_element_id
from servicenow_mcp.investigations import INVESTIGATION_REGISTRY
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.tools._dictionary import DictionaryRegistry
from servicenow_mcp.tools._payload import parse_payload_json
from servicenow_mcp.utils import format_response, validate_identifier


TOOL_NAMES: list[str] = ["investigate"]

_VALID_ACTIONS: Final[frozenset[str]] = frozenset({"run", "explain"})


def _error(correlation_id: str, message: str) -> str:
    """Serialize a standard error envelope."""
    return format_response(data=None, correlation_id=correlation_id, status="error", error=message)


def _unknown_investigation_error(correlation_id: str, name: str) -> str:
    available = ", ".join(sorted(INVESTIGATION_REGISTRY.keys()))
    return _error(correlation_id, f"Unknown investigation '{name}'. Available: {available}")


async def _run_action(
    name: str,
    params: str,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    correlation_id: str,
) -> str:
    if not name:
        return _error(correlation_id, "'name' is required when action='run'.")

    module = INVESTIGATION_REGISTRY.get(name)
    if module is None:
        return _unknown_investigation_error(correlation_id, name)

    if params:
        parsed = parse_payload_json(params, field_name="params", correlation_id=correlation_id, validate_keys=False)
        if isinstance(parsed, str):
            return parsed
        params_dict: dict[str, Any] = parsed
    else:
        params_dict = {}

    table = params_dict.get("table")
    if table:
        validate_identifier(table)
        check_table_access(table)

    async with ServiceNowClient(settings, auth_provider) as client:
        result = await module.run(client, params_dict)

    return format_response(data=result, correlation_id=correlation_id)


async def _explain_action(
    element_id: str,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    correlation_id: str,
) -> str:
    if not element_id:
        return _error(correlation_id, "'element_id' is required when action='explain'.")

    # Format guard before any I/O. ``parse_element_id`` enforces the 'table:sys_id'
    # shape; the table-allowlist check happens inside each module's explain.
    try:
        table, sys_id = parse_element_id(element_id)
    except ValueError as exc:
        return _error(correlation_id, str(exc))
    validate_identifier(table)
    validate_identifier(sys_id)

    # Without a caller-supplied ``name``, dispatch by trial: each module's explain
    # returns ``{"error": ...}`` (single-key) when ``element_id``'s table is outside
    # its allow-set. The first module that produces a real explanation wins; if all
    # decline, we surface the first decline so the caller sees a real message.
    first_decline: dict[str, Any] | None = None
    async with ServiceNowClient(settings, auth_provider) as client:
        for module in INVESTIGATION_REGISTRY.values():
            result = await module.explain(client, element_id)
            if isinstance(result, dict) and set(result.keys()) == {"error"}:
                if first_decline is None:
                    first_decline = result
                continue
            return format_response(data=result, correlation_id=correlation_id)

    fallback: dict[str, Any] = first_decline or {
        "error": f"No registered investigation can explain element_id '{element_id}'.",
    }
    return format_response(data=fallback, correlation_id=correlation_id)


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
) -> None:
    """Register the unified ``investigate`` tool on the MCP server.

    Mirrors the unified register signature so ``server.py`` can pass ``choices``
    uniformly. ``investigate`` does not consume the registry; the parameter is
    accepted only to keep the loader contract consistent.
    """
    del choices, dictionary  # unused; signature retained for loader parity

    @mcp.tool()
    @tool_handler
    async def investigate(
        action: str,
        name: str = "",
        params: str = "{}",
        element_id: str = "",
        *,
        correlation_id: str = "",
    ) -> str:
        """Run an investigation or explain a finding.

        Args:
            action: 'run' | 'explain'.
            name: Investigation name (required for 'run').
                Available: stale_automations, deprecated_apis, table_health,
                acl_conflicts, error_analysis, slow_transactions, performance_bottlenecks.
            params: JSON string of run parameters (run only).
            element_id: 'table:sys_id' identifier of a finding (explain only).
        """
        if action not in _VALID_ACTIONS:
            return _error(
                correlation_id,
                f"Unknown action {action!r}. Expected one of: {sorted(_VALID_ACTIONS)}.",
            )

        if action == "run":
            return await _run_action(
                name=name,
                params=params,
                settings=settings,
                auth_provider=auth_provider,
                correlation_id=correlation_id,
            )

        return await _explain_action(
            element_id=element_id,
            settings=settings,
            auth_provider=auth_provider,
            correlation_id=correlation_id,
        )
