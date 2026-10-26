"""Package presets, custom selection, and change-group exposure."""

from typing import TYPE_CHECKING

import pytest

from servicenow_mcp.bootstrap.stdio import create_mcp_server
from servicenow_mcp.packages import (
    CHANGE_GROUPS,
    PACKAGE_REGISTRY,
    TOOL_GROUPS,
    get_package,
    list_packages,
)

if TYPE_CHECKING:
    from servicenow_mcp.config import Settings


def test_presets_have_complete_disjoint_write_selection() -> None:
    assert set(PACKAGE_REGISTRY) == {"full", "readonly", "core_readonly", "none"}
    assert set(PACKAGE_REGISTRY["full"]) == TOOL_GROUPS
    assert set(PACKAGE_REGISTRY["readonly"]) == TOOL_GROUPS - CHANGE_GROUPS
    assert set(PACKAGE_REGISTRY["core_readonly"]) == {"query", "describe", "attachment"}
    assert PACKAGE_REGISTRY["none"] == []
    assert "service_catalog" in PACKAGE_REGISTRY["readonly"]
    assert "flow" in PACKAGE_REGISTRY["readonly"]
    assert "flow_write" not in TOOL_GROUPS


def test_package_returns_owned_copy() -> None:
    for name in PACKAGE_REGISTRY:
        values = get_package(name)
        values.append("extra")
        assert "extra" not in get_package(name), name
        packages = list_packages()
        packages[name].append("extra")
        assert "extra" not in list_packages()[name], name


def test_custom_groups_preserve_order_and_deduplicate() -> None:
    assert get_package("query, describe, query") == ["query", "describe"]


@pytest.mark.parametrize(
    "name", [pytest.param("", id=""), pytest.param("full,query", id="full,query")]
)
def test_invalid_package_is_explicit(name: str) -> None:
    with pytest.raises(ValueError, match=r"empty|Unknown|preset"):
        get_package(name)


async def test_each_group_exposes_only_its_own_tools(settings: Settings) -> None:
    for group in sorted(TOOL_GROUPS):
        settings.mcp_tool_package = group
        server = create_mcp_server(settings)
        async with server._lowlevel_server.lifespan(server._lowlevel_server):
            assert server.name == "servicenow-platform-mcp"
            names = {tool.name for tool in await server.list_tools()}
        expected = {
            "record_write": "record_preview",
            "attachment_write": "attachment_preview",
            "service_catalog_write": "service_catalog_preview",
        }.get(group, group)
        assert names == {"list_tool_packages", expected} | (
            {"apply_change"} if group in CHANGE_GROUPS else set()
        ), group


async def test_apply_registered_once_for_multiple_groups(settings: Settings) -> None:
    settings.mcp_tool_package = "record_write,attachment_write,service_catalog_write"
    server = create_mcp_server(settings)
    async with server._lowlevel_server.lifespan(server._lowlevel_server):
        names = [tool.name for tool in await server.list_tools()]
    assert names.count("apply_change") == 1
    assert set(names) == {
        "list_tool_packages",
        "record_preview",
        "attachment_preview",
        "service_catalog_preview",
        "apply_change",
    }
