"""Tool package registry and loader for the ServiceNow MCP server.

The unified tool surface exposes 13 tool groups across 4 preset packages:

Groups (registered modules under ``servicenow_mcp.tools``):
    ``query``, ``describe``, ``record_write``, ``record_read``,
    ``attachment``, ``attachment_write``, ``investigate``, ``resolve_choice``,
    ``service_catalog``, ``analysis``, ``audit``, ``flow``, ``code_search``.

Note: the ``record_write`` group registers both ``record_write`` and
``record_apply`` tools. Attachment reads and writes use separate groups;
``attachment_write`` remains gated at runtime by ``write_gate``.

Presets:
    ``full``           - every group (full surface, including attachment writes).
    ``readonly``       - query + describe + record_read + attachment +
                       investigate + resolve_choice + analysis + audit + flow +
                       code_search.
    ``core_readonly``  - query + describe + attachment only.
    ``none``           - no tool groups loaded; only ``list_tool_packages``
                       is registered by the server bootstrap.

Custom comma-syntax packages (e.g. ``"query,describe"``) are also accepted
by ``get_package`` and validated against ``_TOOL_GROUP_MODULES``. Note
that the ``service_catalog`` group name shadows the legacy preset of the
same name; passing ``MCP_TOOL_PACKAGE=service_catalog`` now resolves to
the single-group custom package, which loads the unified service catalog
tool only.
"""

_TOOL_GROUP_MODULES: dict[str, str] = {
    "query": "servicenow_mcp.tools.query",
    "describe": "servicenow_mcp.tools.describe",
    "record_write": "servicenow_mcp.tools.record_write",
    "record_read": "servicenow_mcp.tools.record_read",
    "attachment": "servicenow_mcp.tools.attachment",
    "attachment_write": "servicenow_mcp.tools.attachment_write",
    "investigate": "servicenow_mcp.tools.investigate",
    "resolve_choice": "servicenow_mcp.tools.resolve_choice",
    "service_catalog": "servicenow_mcp.tools.service_catalog",
    "analysis": "servicenow_mcp.tools.analysis",
    "audit": "servicenow_mcp.tools.audit",
    "flow": "servicenow_mcp.tools.flow",
    "code_search": "servicenow_mcp.tools.code_search",
}

# Registry mapping package names to lists of tool group names.
# Tool groups correspond to modules in servicenow_mcp.tools.
PACKAGE_REGISTRY: dict[str, list[str]] = {
    "full": [
        "query",
        "describe",
        "record_write",
        "record_read",
        "attachment",
        "attachment_write",
        "investigate",
        "resolve_choice",
        "service_catalog",
        "analysis",
        "audit",
        "flow",
        "code_search",
    ],
    "readonly": [
        "query",
        "describe",
        "record_read",
        "attachment",
        "investigate",
        "resolve_choice",
        "analysis",
        "audit",
        "flow",
        "code_search",
    ],
    "core_readonly": [
        "query",
        "describe",
        "attachment",
    ],
    "none": [],
}


def get_package(name: str) -> list[str]:
    """Return the tool group names for a package or comma-separated groups.

    Args:
        name: Package name or comma-separated group names.

    Returns:
        List of tool group names.

    Raises:
        ValueError: If package/groups not found or invalid format.
    """
    if name in PACKAGE_REGISTRY:
        return list(PACKAGE_REGISTRY[name])

    raw_groups = name.split(",")
    stripped_groups = [g.strip() for g in raw_groups]

    if "" in stripped_groups:
        raise ValueError("No empty groups allowed")

    groups = [g for g in stripped_groups if g]

    if not groups:  # pragma: no cover - defensive guard; line above catches all empty strings first
        raise ValueError("No empty groups allowed")

    seen: set[str] = set()
    collisions: set[str] = set()
    unknown: set[str] = set()
    result: list[str] = []

    for group in groups:
        if group in collisions or group in unknown:
            continue
        if group in PACKAGE_REGISTRY:
            collisions.add(group)
        elif group not in _TOOL_GROUP_MODULES:
            unknown.add(group)
        elif group not in seen:
            seen.add(group)
            result.append(group)

    errors: list[str] = []
    if collisions:
        errors.append(
            (  # noqa: UP034
                f"Cannot use preset package names as group names: {', '.join(sorted(collisions))}. "
                f"Use them as a package name directly instead."
            )
        )
    if unknown:
        errors.append(
            (  # noqa: UP034
                f"Unknown group names: {', '.join(sorted(unknown))}. "
                f"Valid groups: {', '.join(sorted(_TOOL_GROUP_MODULES.keys()))}"
            )
        )
    if errors:
        raise ValueError(" ".join(errors))

    return result


def list_packages() -> dict[str, list[str]]:
    """Return all registered packages and their tool groups."""
    return {k: list(v) for k, v in PACKAGE_REGISTRY.items()}
