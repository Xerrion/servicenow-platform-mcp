"""Package presets, custom selection, and mutation-group exposure."""

import pytest

from servicenow_mcp.bootstrap.stdio import create_mcp_server
from servicenow_mcp.config import Settings
from servicenow_mcp.packages import MUTATION_GROUPS, PACKAGE_REGISTRY, TOOL_GROUPS, get_package, list_packages


def test_presets_have_complete_disjoint_write_selection() -> None:
    assert set(PACKAGE_REGISTRY) == {"full", "readonly", "core_readonly", "none"}
    assert set(PACKAGE_REGISTRY["full"]) == TOOL_GROUPS
    assert set(PACKAGE_REGISTRY["readonly"]) == TOOL_GROUPS - MUTATION_GROUPS
    assert set(PACKAGE_REGISTRY["core_readonly"]) == {"query", "describe", "attachment"}
    assert PACKAGE_REGISTRY["none"] == []
    assert "service_catalog" in PACKAGE_REGISTRY["readonly"]


@pytest.mark.parametrize("name", list(PACKAGE_REGISTRY))
def test_package_returns_owned_copy(name: str) -> None:
    values = get_package(name)
    values.append("extra")
    assert "extra" not in get_package(name)
    packages = list_packages()
    packages[name].append("extra")
    assert "extra" not in list_packages()[name]


def test_custom_groups_preserve_order_and_deduplicate() -> None:
    assert get_package("query, describe, query") == ["query", "describe"]


@pytest.mark.parametrize("name", ["", "query,", ",query", "query,,describe", "unknown", "full,query", "readonly,query"])
def test_invalid_package_is_explicit(name: str) -> None:
    with pytest.raises(ValueError, match=r"empty|Unknown|preset"):
        get_package(name)


@pytest.mark.parametrize("group", sorted(TOOL_GROUPS))
async def test_selected_group_is_constructible(settings: Settings, group: str) -> None:
    settings.mcp_tool_package = group
    server = create_mcp_server(settings)
    async with server._lowlevel_server.lifespan(server._lowlevel_server):
        names = {tool.name for tool in await server.list_tools()}
    expected = {
        "record_write": "record_preview",
        "attachment_write": "attachment_preview",
        "service_catalog_write": "service_catalog_preview",
    }.get(group, group)
    assert names == {"list_tool_packages", expected} | ({"mutation_apply"} if group in MUTATION_GROUPS else set())


async def test_apply_registered_once_for_multiple_groups(settings: Settings) -> None:
    settings.mcp_tool_package = "record_write,attachment_write,service_catalog_write"
    server = create_mcp_server(settings)
    async with server._lowlevel_server.lifespan(server._lowlevel_server):
        names = [tool.name for tool in await server.list_tools()]
    assert names.count("mutation_apply") == 1
    assert set(names) == {
        "list_tool_packages",
        "record_preview",
        "attachment_preview",
        "service_catalog_preview",
        "mutation_apply",
    }
