"""Tests for the unified tool package registry.

Phase 3b collapsed 14 legacy presets into 4 (``full``, ``readonly``,
``core_readonly``, ``none``) and 21 legacy tool groups into 7 unified
groups under ``servicenow_mcp.tools.*``.
"""

import importlib

import pytest

from servicenow_mcp.packages import (
    _TOOL_GROUP_MODULES,
    PACKAGE_REGISTRY,
    get_package,
    list_packages,
)


EXPECTED_PRESETS = {"full", "readonly", "core_readonly", "none"}
EXPECTED_GROUPS = {
    "code_search",
    "query",
    "describe",
    "record_write",
    "record_read",
    "attachment",
    "investigate",
    "resolve_choice",
    "service_catalog",
    "build_query",
    "flow",
    "audit",
}


class TestPackageRegistry:
    """Shape of the preset registry."""

    def test_registry_has_exactly_four_presets(self) -> None:
        assert set(PACKAGE_REGISTRY.keys()) == EXPECTED_PRESETS

    def test_full_contains_twelve_unified_groups(self) -> None:
        assert set(PACKAGE_REGISTRY["full"]) == EXPECTED_GROUPS
        assert len(PACKAGE_REGISTRY["full"]) == 12

    def test_readonly_is_strict_subset_of_full(self) -> None:
        readonly = set(PACKAGE_REGISTRY["readonly"])
        full = set(PACKAGE_REGISTRY["full"])
        assert readonly < full
        # readonly excludes mutating groups and the build_query helper
        assert "record_write" not in readonly
        assert "service_catalog" not in readonly
        assert "build_query" not in readonly
        # record_read is read-only and included
        assert "record_read" in readonly

    def test_core_readonly_is_strict_subset_of_readonly(self) -> None:
        core = set(PACKAGE_REGISTRY["core_readonly"])
        readonly = set(PACKAGE_REGISTRY["readonly"])
        assert core < readonly
        assert core == {"query", "describe", "attachment"}
        assert "build_query" not in core

    def test_none_is_empty(self) -> None:
        assert PACKAGE_REGISTRY["none"] == []

    def test_legacy_presets_removed(self) -> None:
        for legacy in (
            "itil",
            "developer",
            "analyst",
            "incident_management",
            "change_management",
            "cmdb",
            "problem_management",
            "request_management",
            "knowledge_management",
            "service_catalog",  # was a preset; now a tool group name
        ):
            assert legacy not in PACKAGE_REGISTRY


class TestToolGroupModules:
    """Shape and importability of ``_TOOL_GROUP_MODULES``."""

    def test_groups_match_expected_set(self) -> None:
        assert set(_TOOL_GROUP_MODULES.keys()) == EXPECTED_GROUPS

    def test_all_paths_under_unified_namespace(self) -> None:
        for group, path in _TOOL_GROUP_MODULES.items():
            assert path == f"servicenow_mcp.tools.{group}"

    @pytest.mark.parametrize("group", sorted(EXPECTED_GROUPS))
    def test_each_group_module_is_importable(self, group: str) -> None:
        module = importlib.import_module(_TOOL_GROUP_MODULES[group])
        assert hasattr(module, "register_tools"), f"Module {_TOOL_GROUP_MODULES[group]} must export register_tools()"


class TestGetPackage:
    """``get_package`` resolves preset names and validates inputs."""

    @pytest.mark.parametrize("preset", sorted(EXPECTED_PRESETS))
    def test_known_preset_round_trips(self, preset: str) -> None:
        assert get_package(preset) == PACKAGE_REGISTRY[preset]

    def test_returns_a_copy(self) -> None:
        groups = get_package("full")
        groups.append("not_a_real_group")
        fresh = get_package("full")
        assert "not_a_real_group" not in fresh

    def test_unknown_package_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown"):
            get_package("nonexistent_package")


class TestListPackages:
    """``list_packages`` exposes the registry safely."""

    def test_returns_all_presets(self) -> None:
        assert set(list_packages().keys()) == EXPECTED_PRESETS

    def test_returns_deep_copy(self) -> None:
        packages = list_packages()
        packages["full"].append("not_a_real_group")
        fresh = list_packages()
        assert "not_a_real_group" not in fresh["full"]


class TestCommaSyntax:
    """Custom comma-separated tool packages."""

    def test_single_group(self) -> None:
        assert get_package("query") == ["query"]

    def test_multiple_groups_preserve_order(self) -> None:
        assert get_package("query,describe,attachment") == [
            "query",
            "describe",
            "attachment",
        ]

    def test_strips_whitespace(self) -> None:
        assert get_package("query, describe ,attachment") == [
            "query",
            "describe",
            "attachment",
        ]

    def test_deduplicates(self) -> None:
        assert get_package("query,describe,query,describe") == ["query", "describe"]

    def test_unknown_group_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown group"):
            get_package("query,not_a_real_group")

    def test_unknown_group_lists_valid_groups(self) -> None:
        with pytest.raises(ValueError, match="Unknown") as exc_info:
            get_package("nope_not_real_group")
        message = str(exc_info.value)
        # Validates against the unified group catalog.
        assert "query" in message
        assert "describe" in message

    def test_empty_segment_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            get_package(",,,")

    def test_trailing_comma_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            get_package("query,describe,")

    def test_leading_comma_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            get_package(",query,describe")

    @pytest.mark.parametrize("preset", sorted(EXPECTED_PRESETS))
    def test_preset_name_in_comma_list_is_rejected(self, preset: str) -> None:
        with pytest.raises(ValueError, match="Cannot use preset package names"):
            get_package(f"query,{preset},describe")

    def test_service_catalog_resolves_as_group_now_that_preset_is_gone(self) -> None:
        """The legacy ``service_catalog`` preset is gone, so the bare name now
        resolves via the comma-syntax path to the single unified tool group of
        the same name. This is a documented semantics change."""
        assert get_package("service_catalog") == ["service_catalog"]


def test_domain_groups_not_in_unified_registry() -> None:
    """Phase 3b: legacy ``domain_*`` groups were removed from ``_TOOL_GROUP_MODULES``.

    Phase 4 then deleted the domain modules from disk. This test pins the
    new shape so we notice if domain entries accidentally come back.
    """
    domain_groups = [g for g in _TOOL_GROUP_MODULES if g.startswith("domain_")]
    assert domain_groups == [], (
        f"Legacy domain_* groups should not appear in the unified registry, found: {domain_groups}"
    )


def test_build_query_only_in_full() -> None:
    """``build_query`` is a ``full``-only helper.

    It assembles encoded query strings client-side. Read-only presets pass
    raw encoded queries straight to ``query`` and have no need for the
    builder; gating it to ``full`` keeps the readonly surface minimal.
    """
    assert "build_query" in PACKAGE_REGISTRY["full"]
    for preset in ("readonly", "core_readonly", "none"):
        assert "build_query" not in PACKAGE_REGISTRY[preset], f"build_query should not appear in the '{preset}' preset"


def test_flow_in_full_and_readonly_only() -> None:
    """``flow`` is a Flow Designer inspection group.

    It is read-only (no write tools) so it appears in ``full`` and
    ``readonly``. It is excluded from ``core_readonly`` (which is the
    minimum useful surface) and from ``none`` (empty by definition).
    """
    assert "flow" in PACKAGE_REGISTRY["full"]
    assert "flow" in PACKAGE_REGISTRY["readonly"]
    for preset in ("core_readonly", "none"):
        assert "flow" not in PACKAGE_REGISTRY[preset], f"flow should not appear in the '{preset}' preset"


def test_audit_in_full_and_readonly_only() -> None:
    """``audit`` is a read-only audit-posture / change-trail inspection group.

    Membership mirrors ``flow``: present in ``full`` and ``readonly``,
    absent from ``core_readonly`` and ``none``.
    """
    assert "audit" in PACKAGE_REGISTRY["full"]
    assert "audit" in PACKAGE_REGISTRY["readonly"]
    for preset in ("core_readonly", "none"):
        assert "audit" not in PACKAGE_REGISTRY[preset], f"audit should not appear in the '{preset}' preset"
