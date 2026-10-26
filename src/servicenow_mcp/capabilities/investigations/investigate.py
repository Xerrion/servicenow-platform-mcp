"""Unified ``investigate`` tool: run investigations or explain findings.

Actions:

1. ``run``     -> dispatches to ``module.run(client, params_dict)`` for a named investigation.
2. ``explain`` -> dispatches to a registered module's ``explain(client, element_id)``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, NoReturn

from servicenow_mcp.capabilities.contracts import (
    CapabilityError,
    CapabilityResult,
    validate_payload,
)
from servicenow_mcp.capabilities.contracts import result as operation_result
from servicenow_mcp.capabilities.investigations.checks import INVESTIGATION_REGISTRY
from servicenow_mcp.capabilities.investigations.helpers import (
    parse_element_id,
    validate_params,
)
from servicenow_mcp.platform.client import ServiceNowClient, ServiceNowClientProvider
from servicenow_mcp.policy import check_table_access
from servicenow_mcp.validation import validate_identifier

if TYPE_CHECKING:
    from servicenow_mcp.auth import OAuthPKCEProvider
    from servicenow_mcp.config import Settings


_VALID_ACTIONS: Final[frozenset[str]] = frozenset({"run", "explain", "describe"})

_ACTION_REGISTRY: Final[dict[str, dict[str, Any]]] = {
    "run": {
        "description": "Run one registered investigation.",
        "params": {
            "name": "registered investigation name (required)",
            "params": "native JSON object",
        },
    },
    "explain": {
        "description": "Explain a finding directly when name is supplied, or use trial dispatch otherwise.",
        "params": {
            "element_id": (
                "table:sys_id; with name, table_health and heavy automation accept a table name, acl_conflicts "
                "also accepts an ACL sys_id"
            ),
            "name": "registered investigation name (optional direct-dispatch selector)",
        },
    },
    "describe": {
        "description": "List investigations and action contracts, or describe one investigation.",
        "params": {"name": "registered investigation name (optional)"},
    },
}


def _error(message: str) -> NoReturn:
    """Reject an invalid operation."""
    raise CapabilityError(message)


def _unknown_investigation_error(name: str) -> NoReturn:
    available = ", ".join(sorted(INVESTIGATION_REGISTRY.keys()))
    return _error(f"Unknown investigation '{name}'. Available: {available}")


async def _run_action(
    name: str,
    params: dict[str, Any],
    client_factory: ServiceNowClientProvider,
) -> CapabilityResult:
    if not name:
        return _error("'name' is required when action='run'.")

    module = INVESTIGATION_REGISTRY.get(name)
    if module is None:
        return _unknown_investigation_error(name)

    if params:
        parsed = validate_payload(params, field_name="params", validate_keys=False)
        params_dict: dict[str, Any] = parsed
    else:
        params_dict = {}
    validate_params(params_dict, module.PARAMS)

    table = params_dict.get("table")
    if table:
        validate_identifier(table)
        check_table_access(table)

    async with client_factory() as client:
        result = await module.run(client, params_dict)

    result["investigation"] = name
    if "error" in result:
        raise CapabilityError(str(result["error"]))
    warnings = result.pop("warnings", None)
    return operation_result(data=result, warnings=warnings)


def _describe_action(name: str) -> CapabilityResult:
    if not name:
        return operation_result(
            data={
                "investigations": sorted(INVESTIGATION_REGISTRY.keys()),
                "actions": _ACTION_REGISTRY,
            },
        )

    module = INVESTIGATION_REGISTRY.get(name)
    if module is None:
        return _unknown_investigation_error(name)

    params: dict[str, dict[str, Any]] = getattr(module, "PARAMS", {})
    doc = (module.__doc__ or "").strip()
    description = doc.splitlines()[0] if doc else name
    return operation_result(
        data={
            "name": name,
            "description": description,
            "params": params,
            "actions": _ACTION_REGISTRY,
        },
    )


async def _explain_action(
    element_id: str,
    name: str,
    client_factory: ServiceNowClientProvider,
) -> CapabilityResult:
    if not element_id:
        return _error("'element_id' is required when action='explain'.")
    selected_module = INVESTIGATION_REGISTRY.get(name) if name else None
    if name and selected_module is None:
        return _unknown_investigation_error(name)

    # Direct dispatch supports module-specific identifiers. Validate their shape
    # before I/O; each module applies its table restrictions.
    try:
        if selected_module is not None and ":" not in element_id:
            validate_identifier(element_id)
        else:
            table, sys_id = parse_element_id(element_id)
            validate_identifier(table)
            validate_identifier(sys_id)
    except ValueError as exc:
        return _error(str(exc))

    # Without a caller-supplied ``name``, dispatch by trial: each module's explain
    # returns ``{"error": ...}`` (single-key) when ``element_id``'s table is outside
    # its allow-set. The first module that produces a real explanation wins; if all
    # decline, we surface the first decline so the caller sees a real message.
    if selected_module is not None:
        async with client_factory() as client:
            result = await selected_module.explain(client, element_id)
        if "error" in result:
            raise CapabilityError(str(result["error"]))
        return operation_result(data=result)

    first_decline: dict[str, Any] | None = None
    async with client_factory() as client:
        for module in INVESTIGATION_REGISTRY.values():
            result = await module.explain(client, element_id)
            if isinstance(result, dict) and set(result.keys()) == {"error"}:
                if first_decline is None:
                    first_decline = result
                continue
            return operation_result(data=result)

    fallback: dict[str, Any] = first_decline or {
        "error": f"No registered investigation can explain element_id '{element_id}'.",
    }
    raise CapabilityError(str(fallback["error"]))


class InvestigateCapability:
    def __init__(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        client_factory: ServiceNowClientProvider | None = None,
    ) -> None:
        """Bind the operation's platform and metadata dependencies."""
        client_factory = client_factory or (
            lambda: ServiceNowClient(settings, auth_provider)
        )

        self.auth_provider = auth_provider
        self.client_factory = client_factory
        self.settings = settings

    async def investigate(
        self,
        action: str,
        name: str | None = None,
        params: dict[str, Any] | None = None,
        element_id: str | None = None,
    ) -> CapabilityResult:
        """Execute the investigate capability with native typed inputs."""
        name = "" if name is None else name
        params = {} if params is None else params
        element_id = "" if element_id is None else element_id

        if action not in _VALID_ACTIONS:
            return _error(
                f"Unknown action {action!r}. Expected one of: {sorted(_VALID_ACTIONS)}.",
            )

        if action == "run":
            return await _run_action(
                name=name,
                params=params,
                client_factory=self.client_factory,
            )

        if action == "describe":
            return _describe_action(name)

        return await _explain_action(
            element_id=element_id,
            name=name,
            client_factory=self.client_factory,
        )
