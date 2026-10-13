"""Data-only package and capability-group selection."""

TOOL_GROUPS = frozenset(
    {
        "query",
        "describe",
        "record_write",
        "record_read",
        "attachment",
        "attachment_write",
        "investigate",
        "resolve_choice",
        "service_catalog",
        "service_catalog_write",
        "analysis",
        "audit",
        "flow",
        "code_search",
        "cmdb",
    }
)
MUTATION_GROUPS = frozenset({"record_write", "attachment_write", "service_catalog_write"})
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
        "service_catalog_write",
        "analysis",
        "audit",
        "flow",
        "code_search",
        "cmdb",
    ],
    "readonly": [
        "query",
        "describe",
        "record_read",
        "attachment",
        "investigate",
        "resolve_choice",
        "service_catalog",
        "analysis",
        "audit",
        "flow",
        "code_search",
        "cmdb",
    ],
    "core_readonly": ["query", "describe", "attachment"],
    "none": [],
}


def get_package(name: str) -> list[str]:
    """Resolve a named preset or a comma-separated group selection."""
    if name in PACKAGE_REGISTRY:
        return list(PACKAGE_REGISTRY[name])
    groups = [group.strip() for group in name.split(",")]
    if "" in groups:
        raise ValueError("No empty groups allowed")
    presets = sorted(set(groups) & PACKAGE_REGISTRY.keys())
    unknown = sorted(set(groups) - TOOL_GROUPS - PACKAGE_REGISTRY.keys())
    errors = []
    if presets:
        errors.append(
            f"Cannot use preset package names as group names: {', '.join(presets)}. Use them as a package name directly instead."
        )
    if unknown:
        errors.append(f"Unknown group names: {', '.join(unknown)}. Valid groups: {', '.join(sorted(TOOL_GROUPS))}")
    if errors:
        raise ValueError(" ".join(errors))
    return list(dict.fromkeys(groups))


def list_packages() -> dict[str, list[str]]:
    return {name: list(groups) for name, groups in PACKAGE_REGISTRY.items()}
