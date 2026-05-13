"""Tool package registry and loader for the ServiceNow MCP server.

The unified tool surface exposes 8 tool groups across 4 preset packages:

Groups (registered modules under ``servicenow_mcp.tools.unified``):
    ``query``, ``describe``, ``record_write``, ``attachment``,
    ``investigate``, ``resolve_choice``, ``service_catalog``,
    ``build_query``. The ``build_query`` group is included only in the
    ``full`` preset - it is a stateless query-string builder that complements
    the ``query`` tool and is not needed for read-only surfaces.

Note: the ``record_write`` group registers both ``record_write`` and
``record_apply`` tools; the ``attachment`` group registers both
``attachment`` (read) and ``attachment_write`` tools. There is no
separate ``attachment_write`` group - read and write live in one
module and write paths are gated by ``write_gate``/``can_write``.

Presets:
    ``full``           - every group (full surface).
    ``readonly``       - read + investigate + resolve_choice (still loads
                       attachment which carries write tools; those are
                       blocked at runtime in production by ``write_gate``).
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
    "query": "servicenow_mcp.tools.unified.query",
    "describe": "servicenow_mcp.tools.unified.describe",
    "record_write": "servicenow_mcp.tools.unified.record_write",
    "attachment": "servicenow_mcp.tools.unified.attachment",
    "investigate": "servicenow_mcp.tools.unified.investigate",
    "resolve_choice": "servicenow_mcp.tools.unified.resolve_choice",
    "service_catalog": "servicenow_mcp.tools.unified.service_catalog",
    "build_query": "servicenow_mcp.tools.unified.build_query",
}

# Registry mapping package names to lists of tool group names.
# Tool groups correspond to modules in servicenow_mcp.tools.unified.
#
# Caveat: ``readonly`` and ``core_readonly`` both include the ``attachment``
# group, which registers both read AND write attachment tools. The write
# tools are blocked at runtime in production by ``write_gate``. To get a
# truly read-only attachment surface, the ``attachment`` module would need
# to be split into separate read / write groups.
PACKAGE_REGISTRY: dict[str, list[str]] = {
    "full": [
        "query",
        "describe",
        "record_write",
        "attachment",
        "investigate",
        "resolve_choice",
        "service_catalog",
        "build_query",
    ],
    "readonly": [
        "query",
        "describe",
        "attachment",
        "investigate",
        "resolve_choice",
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
