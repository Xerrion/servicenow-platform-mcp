"""Tests for tool package system."""

import pytest


class TestPackageRegistry:
    """Test package registry and loading."""

    def test_registry_contains_full(self) -> None:
        """full package is defined in the registry."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "full" in PACKAGE_REGISTRY

    def test_registry_contains_none(self) -> None:
        """'none' package is defined in the registry."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "none" in PACKAGE_REGISTRY

    def test_registry_contains_core_readonly(self) -> None:
        """core_readonly package is defined."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "core_readonly" in PACKAGE_REGISTRY

    def test_full_includes_table(self) -> None:
        """full package includes table tools."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "table" in PACKAGE_REGISTRY["full"]

    def test_full_includes_metadata(self) -> None:
        """full package includes metadata tools."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "metadata" in PACKAGE_REGISTRY["full"]

    def test_full_includes_record(self) -> None:
        """full package includes record tools."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "record" in PACKAGE_REGISTRY["full"]

    def test_none_package_is_empty(self) -> None:
        """'none' package has no tool groups."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert PACKAGE_REGISTRY["none"] == []

    def test_get_package_valid(self) -> None:
        """get_package returns tool groups for a valid package."""
        from servicenow_mcp.packages import get_package

        groups = get_package("full")
        assert isinstance(groups, list)
        assert len(groups) > 0

    def test_get_package_invalid_raises(self) -> None:
        """get_package raises ValueError for unknown package."""
        from servicenow_mcp.packages import get_package

        with pytest.raises(ValueError, match="Unknown"):
            get_package("nonexistent_package")

    def test_full_includes_changes(self) -> None:
        """full package includes change intelligence tools."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "changes" in PACKAGE_REGISTRY["full"]

    def test_full_includes_debug(self) -> None:
        """full package includes debug/trace tools."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "debug" in PACKAGE_REGISTRY["full"]

    def test_list_packages_returns_all(self) -> None:
        """list_packages returns all registered packages."""
        from servicenow_mcp.packages import list_packages

        packages = list_packages()
        assert "none" in packages
        assert "full" in packages
        assert "core_readonly" in packages

    def test_dev_debug_not_in_registry(self) -> None:
        """dev_debug package has been removed from the registry."""
        from servicenow_mcp.packages import PACKAGE_REGISTRY

        assert "dev_debug" not in PACKAGE_REGISTRY

    def test_get_package_returns_copy(self) -> None:
        """get_package returns a copy — mutating it does not affect the registry."""
        from servicenow_mcp.packages import get_package

        groups = get_package("full")
        groups.append("should_not_persist")
        fresh = get_package("full")
        assert "should_not_persist" not in fresh

    def test_list_packages_returns_copies(self) -> None:
        """list_packages returns deep copies of value lists."""
        from servicenow_mcp.packages import list_packages

        packages = list_packages()
        packages["full"].append("should_not_persist")
        fresh = list_packages()
        assert "should_not_persist" not in fresh["full"]

    def test_get_package_itil(self) -> None:
        """get_package returns correct groups for itil preset."""
        from servicenow_mcp.packages import get_package

        groups = get_package("itil")
        expected = [
            "table",
            "record",
            "record_write",
            "metadata",
            "changes",
            "debug",
            "documentation",
            "workflow",
            "flow_designer",
            "domain_incident",
            "domain_change",
            "domain_problem",
            "domain_request",
        ]
        assert groups == expected
        assert len(groups) == 13

    def test_get_package_developer(self) -> None:
        """get_package returns correct groups for developer preset."""
        from servicenow_mcp.packages import get_package

        groups = get_package("developer")
        expected = [
            "table",
            "record",
            "record_write",
            "metadata",
            "changes",
            "debug",
            "investigations",
            "documentation",
            "workflow",
            "flow_designer",
        ]
        assert groups == expected
        assert len(groups) == 10

    def test_get_package_readonly(self) -> None:
        """get_package returns correct groups for readonly preset."""
        from servicenow_mcp.packages import get_package

        groups = get_package("readonly")
        expected = [
            "table",
            "record",
            "metadata",
            "changes",
            "debug",
            "investigations",
            "documentation",
            "workflow",
            "flow_designer",
        ]
        assert groups == expected
        assert len(groups) == 9

    def test_get_package_analyst(self) -> None:
        """get_package returns correct groups for analyst preset."""
        from servicenow_mcp.packages import get_package

        groups = get_package("analyst")
        expected = [
            "table",
            "record",
            "metadata",
            "investigations",
            "documentation",
            "workflow",
            "flow_designer",
        ]
        assert groups == expected
        assert len(groups) == 7

    def test_list_packages_includes_itil(self) -> None:
        """list_packages includes itil preset."""
        from servicenow_mcp.packages import list_packages

        packages = list_packages()
        assert "itil" in packages

    def test_list_packages_includes_developer(self) -> None:
        """list_packages includes developer preset."""
        from servicenow_mcp.packages import list_packages

        packages = list_packages()
        assert "developer" in packages

    def test_list_packages_includes_readonly(self) -> None:
        """list_packages includes readonly preset."""
        from servicenow_mcp.packages import list_packages

        packages = list_packages()
        assert "readonly" in packages

    def test_list_packages_includes_analyst(self) -> None:
        """list_packages includes analyst preset."""
        from servicenow_mcp.packages import list_packages

        packages = list_packages()
        assert "analyst" in packages

    def test_full_package_unchanged(self) -> None:
        """full package still returns all groups unchanged."""
        from servicenow_mcp.packages import get_package

        groups = get_package("full")
        assert "table" in groups
        assert "record" in groups
        assert "record_write" in groups
        assert "metadata" in groups
        assert "changes" in groups
        assert "debug" in groups
        assert "investigations" in groups
        assert "documentation" in groups
        assert "workflow" in groups
        assert "flow_designer" in groups
        assert "testing" not in groups
        assert len(groups) == 17


class TestCommaSeparatedGroups:
    """Test comma-separated group syntax for custom tool packages."""

    def test_comma_separated_valid_groups(self) -> None:
        """get_package accepts comma-separated group names and returns list."""
        from servicenow_mcp.packages import get_package

        groups = get_package("table,debug,record")
        assert groups == ["table", "debug", "record"]

    def test_comma_separated_with_spaces(self) -> None:
        """get_package strips whitespace from comma-separated groups."""
        from servicenow_mcp.packages import get_package

        groups = get_package("table, debug, record")
        assert groups == ["table", "debug", "record"]

    def test_comma_separated_deduplicates(self) -> None:
        """get_package deduplicates repeated group names."""
        from servicenow_mcp.packages import get_package

        groups = get_package("debug,debug,debug")
        assert groups == ["debug"]

    def test_comma_separated_mixed_duplicates(self) -> None:
        """get_package deduplicates mixed repeated groups."""
        from servicenow_mcp.packages import get_package

        groups = get_package("table,debug,table,record,debug")
        assert groups == ["table", "debug", "record"]

    def test_comma_separated_invalid_group_raises(self) -> None:
        """get_package raises ValueError for unknown group names."""
        from servicenow_mcp.packages import get_package

        with pytest.raises(ValueError, match="Unknown group"):
            get_package("table,invalid_group")

    def test_comma_separated_multiple_invalid_groups_raises(self) -> None:
        """get_package mentions all invalid group names in error."""
        from servicenow_mcp.packages import get_package

        with pytest.raises(ValueError, match="invalid_group"):
            get_package("table,invalid_group,debug,fake_group")

    def test_comma_separated_empty_groups_raises(self) -> None:
        """get_package raises ValueError for empty group names."""
        from servicenow_mcp.packages import get_package

        with pytest.raises(ValueError, match="empty"):
            get_package(",,,")

    def test_comma_separated_trailing_comma_raises(self) -> None:
        """get_package raises ValueError for trailing commas."""
        from servicenow_mcp.packages import get_package

        with pytest.raises(ValueError, match="empty"):
            get_package("debug,table,")

    def test_comma_separated_leading_comma_raises(self) -> None:
        """get_package raises ValueError for leading commas."""
        from servicenow_mcp.packages import get_package

        with pytest.raises(ValueError, match="empty"):
            get_package(",debug,table")

    def test_preset_name_still_works(self) -> None:
        """get_package still returns preset when name is in PACKAGE_REGISTRY."""
        from servicenow_mcp.packages import get_package

        groups = get_package("itil")
        assert isinstance(groups, list)
        assert "table" in groups

    def test_comma_separated_cannot_use_preset_names(self) -> None:
        """get_package rejects preset names in comma syntax."""
        from servicenow_mcp.packages import get_package

        with pytest.raises(ValueError, match="Cannot use preset package names"):
            get_package("table,itil,debug")

    def test_comma_separated_single_group(self) -> None:
        """get_package accepts single group name."""
        from servicenow_mcp.packages import get_package

        groups = get_package("debug")
        assert groups == ["debug"]

    def test_comma_separated_preserves_order(self) -> None:
        """get_package preserves order while deduplicating."""
        from servicenow_mcp.packages import get_package

        groups = get_package("record,debug,table,debug")
        assert groups == ["record", "debug", "table"]

    def test_comma_separated_duplicate_preset_skipped(self) -> None:
        """get_package skips repeated preset names already flagged as collisions."""
        from servicenow_mcp.packages import get_package

        with pytest.raises(ValueError, match="Cannot use preset package names"):
            get_package("table,itil,itil,debug")

    def test_comma_separated_duplicate_unknown_skipped(self) -> None:
        """get_package skips repeated unknown names already flagged."""
        from servicenow_mcp.packages import get_package

        with pytest.raises(ValueError, match="Unknown group"):
            get_package("table,bogus,bogus,debug")


class TestDomainPackages:
    """Test domain-specific packages."""

    def test_incident_management_package(self) -> None:
        """incident_management package includes correct groups."""
        from servicenow_mcp.packages import get_package

        groups = get_package("incident_management")
        assert "domain_incident" in groups
        assert "table" in groups
        assert "record" in groups
        assert "record_write" in groups
        assert "debug" in groups
        assert "workflow" in groups
        assert "flow_designer" in groups
        assert len(groups) == 7

    def test_change_management_package(self) -> None:
        """change_management package includes correct groups."""
        from servicenow_mcp.packages import get_package

        groups = get_package("change_management")
        assert "domain_change" in groups
        assert "table" in groups
        assert "record" in groups
        assert "record_write" in groups
        assert "changes" in groups
        assert "flow_designer" in groups
        assert len(groups) == 6

    def test_cmdb_package(self) -> None:
        """cmdb package includes correct groups."""
        from servicenow_mcp.packages import get_package

        groups = get_package("cmdb")
        assert "domain_cmdb" in groups
        assert "table" in groups
        assert "record" in groups
        assert "record_write" in groups
        assert len(groups) == 4

    def test_problem_management_package(self) -> None:
        """problem_management package includes correct groups."""
        from servicenow_mcp.packages import get_package

        groups = get_package("problem_management")
        assert "domain_problem" in groups
        assert "table" in groups
        assert "record" in groups
        assert "record_write" in groups
        assert "debug" in groups
        assert "workflow" in groups
        assert "flow_designer" in groups
        assert len(groups) == 7

    def test_request_management_package(self) -> None:
        """request_management package includes correct groups."""
        from servicenow_mcp.packages import get_package

        groups = get_package("request_management")
        assert "domain_request" in groups
        assert "table" in groups
        assert "record" in groups
        assert "record_write" in groups
        assert "workflow" in groups
        assert "flow_designer" in groups
        assert len(groups) == 6

    def test_knowledge_management_package(self) -> None:
        """knowledge_management package includes correct groups."""
        from servicenow_mcp.packages import get_package

        groups = get_package("knowledge_management")
        assert "domain_knowledge" in groups
        assert "table" in groups
        assert "record" in groups
        assert "record_write" in groups
        assert len(groups) == 4

    def test_full_package_includes_all_domain_groups(self) -> None:
        """full package includes exactly 7 domain groups."""
        from servicenow_mcp.packages import get_package

        groups = get_package("full")
        domain_groups = [g for g in groups if g.startswith("domain_")]
        assert len(domain_groups) == 7
        assert "domain_incident" in domain_groups
        assert "domain_change" in domain_groups
        assert "domain_cmdb" in domain_groups
        assert "domain_problem" in domain_groups
        assert "domain_request" in domain_groups
        assert "domain_knowledge" in domain_groups
        assert "domain_service_catalog" in domain_groups

    def test_itil_package_includes_four_domain_groups(self) -> None:
        """itil package includes 4 domain groups (incident, change, problem, request)."""
        from servicenow_mcp.packages import get_package

        groups = get_package("itil")
        domain_groups = [g for g in groups if g.startswith("domain_")]
        assert len(domain_groups) == 4
        assert "domain_incident" in domain_groups
        assert "domain_change" in domain_groups
        assert "domain_problem" in domain_groups
        assert "domain_request" in domain_groups

    def test_list_packages_includes_all_domain_packages(self) -> None:
        """list_packages includes all 7 domain-specific packages."""
        from servicenow_mcp.packages import list_packages

        packages = list_packages()
        assert "incident_management" in packages
        assert "change_management" in packages
        assert "cmdb" in packages
        assert "problem_management" in packages
        assert "request_management" in packages
        assert "knowledge_management" in packages
        assert "service_catalog" in packages

    def test_comma_syntax_with_domain_groups(self) -> None:
        """get_package accepts comma-separated syntax with domain groups."""
        from servicenow_mcp.packages import get_package

        groups = get_package("table,domain_incident,record")
        assert groups == ["table", "domain_incident", "record"]

    def test_comma_syntax_multiple_domain_groups(self) -> None:
        """get_package accepts multiple domain groups in comma syntax."""
        from servicenow_mcp.packages import get_package

        groups = get_package("domain_incident,domain_change,record")
        assert groups == ["domain_incident", "domain_change", "record"]

    def test_backward_compatibility_full_package_count(self) -> None:
        """full package has 17 total groups (10 core + 7 domain)."""
        from servicenow_mcp.packages import get_package

        groups = get_package("full")
        assert len(groups) == 17

    def test_backward_compatibility_itil_package_count(self) -> None:
        """itil package has 13 total groups (9 original + 4 domain)."""
        from servicenow_mcp.packages import get_package

        groups = get_package("itil")
        assert len(groups) == 13

    def test_developer_package_unchanged(self) -> None:
        """developer package has 10 groups (no domain groups)."""
        from servicenow_mcp.packages import get_package

        groups = get_package("developer")
        assert len(groups) == 10
        domain_groups = [g for g in groups if g.startswith("domain_")]
        assert len(domain_groups) == 0

    def test_readonly_package_unchanged(self) -> None:
        """readonly package has 9 groups (no domain groups)."""
        from servicenow_mcp.packages import get_package

        groups = get_package("readonly")
        assert len(groups) == 9
        domain_groups = [g for g in groups if g.startswith("domain_")]
        assert len(domain_groups) == 0

    def test_analyst_package_unchanged(self) -> None:
        """analyst package has 7 groups (no domain groups)."""
        from servicenow_mcp.packages import get_package

        groups = get_package("analyst")
        assert len(groups) == 7
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

    def test_full_package_tool_uniqueness(self) -> None:
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

    def test_itil_package_tool_uniqueness(self) -> None:
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

    def test_incident_management_package_tool_uniqueness(self) -> None:
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

    def test_change_management_package_tool_uniqueness(self) -> None:
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

    def test_cmdb_package_tool_uniqueness(self) -> None:
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

    def test_problem_management_package_tool_uniqueness(self) -> None:
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

    def test_request_management_package_tool_uniqueness(self) -> None:
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

    def test_knowledge_management_package_tool_uniqueness(self) -> None:
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
