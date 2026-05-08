"""Test that all domain modules can be imported."""


def test_all_domain_modules_importable() -> None:
    """Verify all 6 domain modules can be imported without error."""
    from servicenow_mcp.tools.domains import (
        change,
        cmdb,
        incident,
        knowledge,
        problem,
        request,
    )

    # Verify each has register_tools function
    assert hasattr(incident, "register_tools")
    assert hasattr(change, "register_tools")
    assert hasattr(cmdb, "register_tools")
    assert hasattr(problem, "register_tools")
    assert hasattr(request, "register_tools")
    assert hasattr(knowledge, "register_tools")


def test_domain_groups_not_in_unified_registry() -> None:
    """Phase 3b: legacy ``domain_*`` groups were removed from ``_TOOL_GROUP_MODULES``.

    The domain modules themselves remain importable on disk (Phase 4 deletes
    them); only the package-loader registry was collapsed to the 7 unified
    groups. This test pins the new shape so we notice if domain entries
    accidentally come back.
    """
    from servicenow_mcp.packages import _TOOL_GROUP_MODULES

    domain_groups = [g for g in _TOOL_GROUP_MODULES if g.startswith("domain_")]
    assert domain_groups == [], (
        f"Legacy domain_* groups should not appear in the unified registry, found: {domain_groups}"
    )
