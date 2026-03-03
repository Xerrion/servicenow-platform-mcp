"""Test that all domain modules can be imported."""



def test_all_domain_modules_importable() -> None:
    """Verify all 6 domain modules can be imported without error."""
    from servicenow_mcp.tools.domains import change, cmdb, incident, knowledge, problem, request

    # Verify each has register_tools function
    assert hasattr(incident, "register_tools")
    assert hasattr(change, "register_tools")
    assert hasattr(cmdb, "register_tools")
    assert hasattr(problem, "register_tools")
    assert hasattr(request, "register_tools")
    assert hasattr(knowledge, "register_tools")


def test_domain_groups_in_registry() -> None:
    """Verify all domain groups are registered in _TOOL_GROUP_MODULES."""
    from servicenow_mcp.packages import _TOOL_GROUP_MODULES

    expected_domains = ["incident", "change", "cmdb", "problem", "request", "knowledge"]
    for domain in expected_domains:
        group_key = f"domain_{domain}"
        assert group_key in _TOOL_GROUP_MODULES, f"Missing {group_key} in _TOOL_GROUP_MODULES"
        assert _TOOL_GROUP_MODULES[group_key] == f"servicenow_mcp.tools.domains.{domain}", (
            f"Incorrect module path for {group_key}"
        )
