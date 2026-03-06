"""Tool package registry and loader for the ServiceNow MCP server."""

_TOOL_GROUP_MODULES: dict[str, str] = {
    "introspection": "servicenow_mcp.tools.introspection",
    "relationships": "servicenow_mcp.tools.relationships",
    "testing": "servicenow_mcp.tools.testing",
    "metadata": "servicenow_mcp.tools.metadata",
    "changes": "servicenow_mcp.tools.changes",
    "debug": "servicenow_mcp.tools.debug",
    "developer": "servicenow_mcp.tools.developer",
    "dev_utils": "servicenow_mcp.tools.dev_utils",
    "investigations": "servicenow_mcp.tools.investigations",
    "documentation": "servicenow_mcp.tools.documentation",
    "utility": "servicenow_mcp.tools.utility",
    "workflow": "servicenow_mcp.tools.workflow",
    "domain_incident": "servicenow_mcp.tools.domains.incident",
    "domain_change": "servicenow_mcp.tools.domains.change",
    "domain_cmdb": "servicenow_mcp.tools.domains.cmdb",
    "domain_problem": "servicenow_mcp.tools.domains.problem",
    "domain_request": "servicenow_mcp.tools.domains.request",
    "domain_knowledge": "servicenow_mcp.tools.domains.knowledge",
    "domain_service_catalog": "servicenow_mcp.tools.domains.service_catalog",
}

# Registry mapping package names to lists of tool group names.
# Tool groups correspond to modules in servicenow_mcp.tools.
PACKAGE_REGISTRY: dict[str, list[str]] = {
    "introspection_only": [
        "introspection",
        "relationships",
        "metadata",
        "utility",
    ],
    "full": [
        "introspection",
        "relationships",
        # "testing",  # ATF - disabled
        "metadata",
        "changes",
        "debug",
        "developer",
        "dev_utils",
        "investigations",
        "documentation",
        "utility",
        "workflow",
        "domain_incident",
        "domain_change",
        "domain_cmdb",
        "domain_problem",
        "domain_request",
        "domain_knowledge",
        "domain_service_catalog",
    ],
    "none": [],
    "itil": [
        "introspection",
        "relationships",
        "metadata",
        "changes",
        "debug",
        "documentation",
        "utility",
        "workflow",
        "domain_incident",
        "domain_change",
        "domain_problem",
        "domain_request",
    ],
    "developer": [
        "introspection",
        "relationships",
        "metadata",
        "changes",
        "debug",
        "developer",
        "dev_utils",
        "investigations",
        "documentation",
        "utility",
        "workflow",
    ],
    "readonly": [
        "introspection",
        "relationships",
        "metadata",
        "changes",
        "debug",
        "investigations",
        "documentation",
        "utility",
        "workflow",
    ],
    "analyst": [
        "introspection",
        "relationships",
        "metadata",
        "investigations",
        "documentation",
        "utility",
        "workflow",
    ],
    "incident_management": [
        "introspection",
        "utility",
        "domain_incident",
        "debug",
        "workflow",
    ],
    "change_management": [
        "introspection",
        "utility",
        "domain_change",
        "changes",
    ],
    "cmdb": [
        "introspection",
        "relationships",
        "utility",
        "domain_cmdb",
    ],
    "problem_management": [
        "introspection",
        "utility",
        "domain_problem",
        "debug",
        "workflow",
    ],
    "request_management": [
        "introspection",
        "utility",
        "domain_request",
        "workflow",
    ],
    "knowledge_management": [
        "introspection",
        "utility",
        "domain_knowledge",
    ],
    "service_catalog": [
        "introspection",
        "utility",
        "domain_service_catalog",
    ],
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

    if not groups:  # pragma: no cover — defensive guard; line 150 catches all empty strings first
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
