"""Tool package registry and loader for the ServiceNow MCP server."""

_TOOL_GROUP_MODULES: dict[str, str] = {
    "table": "servicenow_mcp.tools.table",
    "record": "servicenow_mcp.tools.record",
    "attachment": "servicenow_mcp.tools.attachment",
    "record_write": "servicenow_mcp.tools.record_write",
    "attachment_write": "servicenow_mcp.tools.attachment_write",
    "testing": "servicenow_mcp.tools.testing",
    "metadata": "servicenow_mcp.tools.metadata",
    "artifact_write": "servicenow_mcp.tools.artifact_write",
    "changes": "servicenow_mcp.tools.changes",
    "debug": "servicenow_mcp.tools.debug",
    "investigations": "servicenow_mcp.tools.investigations",
    "documentation": "servicenow_mcp.tools.documentation",
    "workflow": "servicenow_mcp.tools.workflow",
    "flow_designer": "servicenow_mcp.tools.flow_designer",
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
    "core_readonly": [
        "table",
        "record",
        "attachment",
        "metadata",
    ],
    "full": [
        "table",
        "record",
        "attachment",
        "record_write",
        "attachment_write",
        # "testing",  # ATF - disabled
        "metadata",
        "artifact_write",
        "changes",
        "debug",
        "investigations",
        "documentation",
        "workflow",
        "flow_designer",
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
        "table",
        "record",
        "attachment",
        "record_write",
        "attachment_write",
        "metadata",
        "artifact_write",
        "changes",
        "debug",
        "documentation",
        "workflow",
        "flow_designer",
        "domain_incident",
        "domain_change",
        "domain_problem",
        "domain_request",
    ],
    "developer": [
        "table",
        "record",
        "attachment",
        "record_write",
        "attachment_write",
        "metadata",
        "artifact_write",
        "changes",
        "debug",
        "investigations",
        "documentation",
        "workflow",
        "flow_designer",
    ],
    "readonly": [
        "table",
        "record",
        "attachment",
        "metadata",
        "changes",
        "debug",
        "investigations",
        "documentation",
        "workflow",
        "flow_designer",
    ],
    "analyst": [
        "table",
        "record",
        "attachment",
        "metadata",
        "investigations",
        "documentation",
        "workflow",
        "flow_designer",
    ],
    "incident_management": [
        "table",
        "record",
        "attachment",
        "record_write",
        "attachment_write",
        "domain_incident",
        "debug",
        "workflow",
        "flow_designer",
    ],
    "change_management": [
        "table",
        "record",
        "attachment",
        "record_write",
        "attachment_write",
        "domain_change",
        "changes",
        "flow_designer",
    ],
    "cmdb": [
        "table",
        "record",
        "attachment",
        "record_write",
        "attachment_write",
        "domain_cmdb",
    ],
    "problem_management": [
        "table",
        "record",
        "attachment",
        "record_write",
        "attachment_write",
        "domain_problem",
        "debug",
        "workflow",
        "flow_designer",
    ],
    "request_management": [
        "table",
        "record",
        "attachment",
        "record_write",
        "attachment_write",
        "domain_request",
        "workflow",
        "flow_designer",
    ],
    "knowledge_management": [
        "table",
        "record",
        "attachment",
        "record_write",
        "attachment_write",
        "domain_knowledge",
    ],
    "service_catalog": [
        "table",
        "record",
        "attachment",
        "record_write",
        "attachment_write",
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
