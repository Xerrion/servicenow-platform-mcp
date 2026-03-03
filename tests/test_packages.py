"""Tests for tool package system."""

import pytest


class TestPackageRegistry:
    """Test package registry and loading."""

    def test_registry_contains_full(self):
        """full package is defined in the registry."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "full" in PACKAGE_REGISTRY

    def test_registry_contains_none(self):
        """'none' package is defined in the registry."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "none" in PACKAGE_REGISTRY

    def test_registry_contains_introspection_only(self):
        """introspection_only package is defined."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "introspection_only" in PACKAGE_REGISTRY

    def test_full_includes_introspection(self):
        """full package includes introspection tools."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "introspection" in PACKAGE_REGISTRY["full"]

    def test_full_includes_metadata(self):
        """full package includes metadata tools."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "metadata" in PACKAGE_REGISTRY["full"]

    def test_full_includes_relationships(self):
        """full package includes relationship tools."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "relationships" in PACKAGE_REGISTRY["full"]

    def test_none_package_is_empty(self):
        """'none' package has no tool groups."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert PACKAGE_REGISTRY["none"] == []

    def test_get_package_valid(self):
        """get_package returns tool groups for a valid package."""
        from servicenow_mcp.packages import get_package

        groups = get_package("full")
        assert isinstance(groups, list)
        assert len(groups) > 0

    def test_get_package_invalid_raises(self):
        """get_package raises ValueError for unknown package."""
        from servicenow_mcp.packages import get_package

        with pytest.raises(ValueError, match="Unknown"):
            get_package("nonexistent_package")

    def test_full_includes_changes(self):
        """full package includes change intelligence tools."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "changes" in PACKAGE_REGISTRY["full"]

    def test_full_includes_debug(self):
        """full package includes debug/trace tools."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "debug" in PACKAGE_REGISTRY["full"]

    def test_list_packages_returns_all(self):
        """list_packages returns all registered packages."""
        from servicenow_mcp.packages import list_packages

        packages = list_packages()
        assert "none" in packages
        assert "full" in packages
        assert "introspection_only" in packages

    def test_dev_debug_not_in_registry(self):
        """dev_debug package has been removed from the registry."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "dev_debug" not in PACKAGE_REGISTRY

    def test_get_package_returns_copy(self):
        """get_package returns a copy — mutating it does not affect the registry."""
        from servicenow_mcp.packages import get_package

        groups = get_package("full")
        groups.append("should_not_persist")
        fresh = get_package("full")
        assert "should_not_persist" not in fresh

    def test_list_packages_returns_copies(self):
        """list_packages returns deep copies of value lists."""
        from servicenow_mcp.packages import list_packages

        packages = list_packages()
        packages["full"].append("should_not_persist")
        fresh = list_packages()
        assert "should_not_persist" not in fresh["full"]

    def test_get_package_itil(self):
        """get_package returns correct groups for itil preset."""
        from servicenow_mcp.packages import get_package

        groups = get_package("itil")
        expected = [
            "introspection",
            "relationships",
            "metadata",
            "changes",
            "debug",
            "documentation",
            "utility",
            "domain_incident",
            "domain_change",
            "domain_problem",
            "domain_request",
        ]
        assert groups == expected
        assert len(groups) == 11

    def test_get_package_developer(self):
        """get_package returns correct groups for developer preset."""
        from servicenow_mcp.packages import get_package

        groups = get_package("developer")
        expected = [
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
        ]
        assert groups == expected
        assert len(groups) == 10

    def test_get_package_readonly(self):
        """get_package returns correct groups for readonly preset."""
        from servicenow_mcp.packages import get_package

        groups = get_package("readonly")
        expected = [
            "introspection",
            "relationships",
            "metadata",
            "changes",
            "debug",
            "investigations",
            "documentation",
            "utility",
        ]
        assert groups == expected
        assert len(groups) == 8

    def test_get_package_analyst(self):
        """get_package returns correct groups for analyst preset."""
        from servicenow_mcp.packages import get_package

        groups = get_package("analyst")
        expected = ["introspection", "relationships", "metadata", "investigations", "documentation", "utility"]
        assert groups == expected
        assert len(groups) == 6

    def test_list_packages_includes_itil(self):
        """list_packages includes itil preset."""
        from servicenow_mcp.packages import list_packages

        packages = list_packages()
        assert "itil" in packages

    def test_list_packages_includes_developer(self):
        """list_packages includes developer preset."""
        from servicenow_mcp.packages import list_packages

        packages = list_packages()
        assert "developer" in packages

    def test_list_packages_includes_readonly(self):
        """list_packages includes readonly preset."""
        from servicenow_mcp.packages import list_packages

        packages = list_packages()
        assert "readonly" in packages

    def test_list_packages_includes_analyst(self):
        """list_packages includes analyst preset."""
        from servicenow_mcp.packages import list_packages

        packages = list_packages()
        assert "analyst" in packages

    def test_full_package_unchanged(self):
        """full package still returns all groups unchanged."""
        from servicenow_mcp.packages import get_package

        groups = get_package("full")
        assert "introspection" in groups
        assert "relationships" in groups
        assert "metadata" in groups
        assert "changes" in groups
        assert "debug" in groups
        assert "developer" in groups
        assert "dev_utils" in groups
        assert "investigations" in groups
        assert "documentation" in groups
        assert "utility" in groups
        assert len(groups) == 17


class TestCommaSeparatedGroups:
    """Test comma-separated group syntax for custom tool packages."""

    def test_comma_separated_valid_groups(self):
        """get_package accepts comma-separated group names and returns list."""
        from servicenow_mcp.packages import get_package

        groups = get_package("introspection,debug,utility")
        assert groups == ["introspection", "debug", "utility"]

    def test_comma_separated_with_spaces(self):
        """get_package strips whitespace from comma-separated groups."""
        from servicenow_mcp.packages import get_package

        groups = get_package("introspection, debug, utility")
        assert groups == ["introspection", "debug", "utility"]

    def test_comma_separated_deduplicates(self):
        """get_package deduplicates repeated group names."""
        from servicenow_mcp.packages import get_package

        groups = get_package("debug,debug,debug")
        assert groups == ["debug"]

    def test_comma_separated_mixed_duplicates(self):
        """get_package deduplicates mixed repeated groups."""
        from servicenow_mcp.packages import get_package

        groups = get_package("introspection,debug,introspection,utility,debug")
        assert groups == ["introspection", "debug", "utility"]

    def test_comma_separated_invalid_group_raises(self):
        """get_package raises ValueError for unknown group names."""
        from servicenow_mcp.packages import get_package

        with pytest.raises(ValueError, match="Unknown group"):
            get_package("introspection,invalid_group")

    def test_comma_separated_multiple_invalid_groups_raises(self):
        """get_package mentions all invalid group names in error."""
        from servicenow_mcp.packages import get_package

        with pytest.raises(ValueError, match="invalid_group"):
            get_package("introspection,invalid_group,debug,fake_group")

    def test_comma_separated_empty_groups_raises(self):
        """get_package raises ValueError for empty group names."""
        from servicenow_mcp.packages import get_package

        with pytest.raises(ValueError, match="empty"):
            get_package(",,,")

    def test_comma_separated_trailing_comma_raises(self):
        """get_package raises ValueError for trailing commas."""
        from servicenow_mcp.packages import get_package

        with pytest.raises(ValueError, match="empty"):
            get_package("debug,introspection,")

    def test_comma_separated_leading_comma_raises(self):
        """get_package raises ValueError for leading commas."""
        from servicenow_mcp.packages import get_package

        with pytest.raises(ValueError, match="empty"):
            get_package(",debug,introspection")

    def test_preset_name_still_works(self):
        """get_package still returns preset when name is in PACKAGE_REGISTRY."""
        from servicenow_mcp.packages import get_package

        groups = get_package("itil")
        assert isinstance(groups, list)
        assert "introspection" in groups

    def test_comma_separated_cannot_use_preset_names(self):
        """get_package rejects preset names in comma syntax."""
        from servicenow_mcp.packages import get_package

        with pytest.raises(ValueError, match="Unknown group"):
            get_package("introspection,itil,debug")

    def test_comma_separated_single_group(self):
        """get_package accepts single group name."""
        from servicenow_mcp.packages import get_package

        groups = get_package("debug")
        assert groups == ["debug"]

    def test_comma_separated_preserves_order(self):
        """get_package preserves order while deduplicating."""
        from servicenow_mcp.packages import get_package

        groups = get_package("utility,debug,introspection,debug")
        assert groups == ["utility", "debug", "introspection"]


class TestDomainPackages:
    """Test domain-specific packages."""

    def test_incident_management_package(self):
        """incident_management package includes correct groups."""
        from servicenow_mcp.packages import get_package

        groups = get_package("incident_management")
        assert "domain_incident" in groups
        assert "introspection" in groups
        assert "utility" in groups
        assert "debug" in groups
        assert len(groups) == 4

    def test_change_management_package(self):
        """change_management package includes correct groups."""
        from servicenow_mcp.packages import get_package

        groups = get_package("change_management")
        assert "domain_change" in groups
        assert "introspection" in groups
        assert "utility" in groups
        assert "changes" in groups
        assert len(groups) == 4

    def test_cmdb_package(self):
        """cmdb package includes correct groups."""
        from servicenow_mcp.packages import get_package

        groups = get_package("cmdb")
        assert "domain_cmdb" in groups
        assert "introspection" in groups
        assert "relationships" in groups
        assert "utility" in groups
        assert len(groups) == 4

    def test_problem_management_package(self):
        """problem_management package includes correct groups."""
        from servicenow_mcp.packages import get_package

        groups = get_package("problem_management")
        assert "domain_problem" in groups
        assert "introspection" in groups
        assert "utility" in groups
        assert "debug" in groups
        assert len(groups) == 4

    def test_request_management_package(self):
        """request_management package includes correct groups."""
        from servicenow_mcp.packages import get_package

        groups = get_package("request_management")
        assert "domain_request" in groups
        assert "introspection" in groups
        assert "utility" in groups
        assert len(groups) == 3

    def test_knowledge_management_package(self):
        """knowledge_management package includes correct groups."""
        from servicenow_mcp.packages import get_package

        groups = get_package("knowledge_management")
        assert "domain_knowledge" in groups
        assert "introspection" in groups
        assert "utility" in groups
        assert len(groups) == 3

    def test_full_package_includes_all_domain_groups(self):
        """full package includes exactly 6 domain groups."""
        from servicenow_mcp.packages import get_package

        groups = get_package("full")
        domain_groups = [g for g in groups if g.startswith("domain_")]
        assert len(domain_groups) == 6
        assert "domain_incident" in domain_groups
        assert "domain_change" in domain_groups
        assert "domain_cmdb" in domain_groups
        assert "domain_problem" in domain_groups
        assert "domain_request" in domain_groups
        assert "domain_knowledge" in domain_groups

    def test_itil_package_includes_four_domain_groups(self):
        """itil package includes 4 domain groups (incident, change, problem, request)."""
        from servicenow_mcp.packages import get_package

        groups = get_package("itil")
        domain_groups = [g for g in groups if g.startswith("domain_")]
        assert len(domain_groups) == 4
        assert "domain_incident" in domain_groups
        assert "domain_change" in domain_groups
        assert "domain_problem" in domain_groups
        assert "domain_request" in domain_groups

    def test_list_packages_includes_all_domain_packages(self):
        """list_packages includes all 6 domain-specific packages."""
        from servicenow_mcp.packages import list_packages

        packages = list_packages()
        assert "incident_management" in packages
        assert "change_management" in packages
        assert "cmdb" in packages
        assert "problem_management" in packages
        assert "request_management" in packages
        assert "knowledge_management" in packages

    def test_comma_syntax_with_domain_groups(self):
        """get_package accepts comma-separated syntax with domain groups."""
        from servicenow_mcp.packages import get_package

        groups = get_package("introspection,domain_incident,utility")
        assert groups == ["introspection", "domain_incident", "utility"]

    def test_comma_syntax_multiple_domain_groups(self):
        """get_package accepts multiple domain groups in comma syntax."""
        from servicenow_mcp.packages import get_package

        groups = get_package("domain_incident,domain_change,utility")
        assert groups == ["domain_incident", "domain_change", "utility"]

    def test_backward_compatibility_full_package_count(self):
        """full package has 17 total groups (11 original + 6 domain)."""
        from servicenow_mcp.packages import get_package

        groups = get_package("full")
        assert len(groups) == 17

    def test_backward_compatibility_itil_package_count(self):
        """itil package has 11 total groups (7 original + 4 domain)."""
        from servicenow_mcp.packages import get_package

        groups = get_package("itil")
        assert len(groups) == 11

    def test_developer_package_unchanged(self):
        """developer package still has 10 groups (no domain groups)."""
        from servicenow_mcp.packages import get_package

        groups = get_package("developer")
        assert len(groups) == 10
        domain_groups = [g for g in groups if g.startswith("domain_")]
        assert len(domain_groups) == 0

    def test_readonly_package_unchanged(self):
        """readonly package still has 8 groups (no domain groups)."""
        from servicenow_mcp.packages import get_package

        groups = get_package("readonly")
        assert len(groups) == 8
        domain_groups = [g for g in groups if g.startswith("domain_")]
        assert len(domain_groups) == 0

    def test_analyst_package_unchanged(self):
        """analyst package still has 6 groups (no domain groups)."""
        from servicenow_mcp.packages import get_package

        groups = get_package("analyst")
        assert len(groups) == 6
        domain_groups = [g for g in groups if g.startswith("domain_")]
        assert len(domain_groups) == 0


class TestToolNameUniqueness:
    """Integration tests verifying tool name uniqueness across packages."""

    def _load_tools_for_package(self, package_name: str) -> dict[str, list[str]]:
        """Load all tools for a package and return tool names grouped by module.

        Returns:
            Dict mapping module names to list of tool names.
        """
        import importlib

        from servicenow_mcp.packages import _TOOL_GROUP_MODULES, get_package

        tool_groups = get_package(package_name)
        tools_by_module: dict[str, list[str]] = {}

        for group_name in tool_groups:
            module_path = _TOOL_GROUP_MODULES.get(group_name)
            if module_path:
                try:
                    module = importlib.import_module(module_path)
                    if hasattr(module, "TOOL_NAMES"):
                        tools_by_module[group_name] = module.TOOL_NAMES
                except ImportError:
                    pass

        return tools_by_module

    def _get_all_tool_names(self, tools_by_module: dict[str, list[str]]) -> list[str]:
        """Flatten tool names from all modules."""
        all_tools = []
        for tools in tools_by_module.values():
            all_tools.extend(tools)
        return all_tools

    def test_full_package_tool_uniqueness(self):
        """full package has no duplicate tool names."""
        tools_by_module = self._load_tools_for_package("full")
        all_tools = self._get_all_tool_names(tools_by_module)

        seen = set()
        duplicates = set()
        for tool in all_tools:
            if tool in seen:
                duplicates.add(tool)
            seen.add(tool)

        assert len(duplicates) == 0, f"Duplicate tools in 'full' package: {duplicates}"

    def test_itil_package_tool_uniqueness(self):
        """itil package has no duplicate tool names."""
        tools_by_module = self._load_tools_for_package("itil")
        all_tools = self._get_all_tool_names(tools_by_module)

        seen = set()
        duplicates = set()
        for tool in all_tools:
            if tool in seen:
                duplicates.add(tool)
            seen.add(tool)

        assert len(duplicates) == 0, f"Duplicate tools in 'itil' package: {duplicates}"

    def test_incident_management_package_tool_uniqueness(self):
        """incident_management package has no duplicate tool names."""
        tools_by_module = self._load_tools_for_package("incident_management")
        all_tools = self._get_all_tool_names(tools_by_module)

        seen = set()
        duplicates = set()
        for tool in all_tools:
            if tool in seen:
                duplicates.add(tool)
            seen.add(tool)

        assert len(duplicates) == 0, f"Duplicate tools in 'incident_management' package: {duplicates}"

    def test_change_management_package_tool_uniqueness(self):
        """change_management package has no duplicate tool names."""
        tools_by_module = self._load_tools_for_package("change_management")
        all_tools = self._get_all_tool_names(tools_by_module)

        seen = set()
        duplicates = set()
        for tool in all_tools:
            if tool in seen:
                duplicates.add(tool)
            seen.add(tool)

        assert len(duplicates) == 0, f"Duplicate tools in 'change_management' package: {duplicates}"

    def test_cmdb_package_tool_uniqueness(self):
        """cmdb package has no duplicate tool names."""
        tools_by_module = self._load_tools_for_package("cmdb")
        all_tools = self._get_all_tool_names(tools_by_module)

        seen = set()
        duplicates = set()
        for tool in all_tools:
            if tool in seen:
                duplicates.add(tool)
            seen.add(tool)

        assert len(duplicates) == 0, f"Duplicate tools in 'cmdb' package: {duplicates}"

    def test_problem_management_package_tool_uniqueness(self):
        """problem_management package has no duplicate tool names."""
        tools_by_module = self._load_tools_for_package("problem_management")
        all_tools = self._get_all_tool_names(tools_by_module)

        seen = set()
        duplicates = set()
        for tool in all_tools:
            if tool in seen:
                duplicates.add(tool)
            seen.add(tool)

        assert len(duplicates) == 0, f"Duplicate tools in 'problem_management' package: {duplicates}"

    def test_request_management_package_tool_uniqueness(self):
        """request_management package has no duplicate tool names."""
        tools_by_module = self._load_tools_for_package("request_management")
        all_tools = self._get_all_tool_names(tools_by_module)

        seen = set()
        duplicates = set()
        for tool in all_tools:
            if tool in seen:
                duplicates.add(tool)
            seen.add(tool)

        assert len(duplicates) == 0, f"Duplicate tools in 'request_management' package: {duplicates}"

    def test_knowledge_management_package_tool_uniqueness(self):
        """knowledge_management package has no duplicate tool names."""
        tools_by_module = self._load_tools_for_package("knowledge_management")
        all_tools = self._get_all_tool_names(tools_by_module)

        seen = set()
        duplicates = set()
        for tool in all_tools:
            if tool in seen:
                duplicates.add(tool)
            seen.add(tool)

        assert len(duplicates) == 0, f"Duplicate tools in 'knowledge_management' package: {duplicates}"
