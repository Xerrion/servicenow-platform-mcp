"""Tool package registry and loader for the ServiceNow MCP server."""

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
        "metadata",
        "changes",
        "debug",
        "developer",
        "investigations",
        "documentation",
        "utility",
    ],
    "none": [],
}


def get_package(name: str) -> list[str]:
    """Return the tool group names for a package.

    Raises ValueError if the package is not found.
    """
    if name not in PACKAGE_REGISTRY:
        raise ValueError(f"Unknown tool package '{name}'. Available packages: {', '.join(PACKAGE_REGISTRY.keys())}")
    return PACKAGE_REGISTRY[name]


def list_packages() -> dict[str, list[str]]:
    """Return all registered packages and their tool groups."""
    return dict(PACKAGE_REGISTRY)
