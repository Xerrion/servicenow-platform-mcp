"""Local application construction, safe defaults, and command startup."""

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from servicenow_mcp.bootstrap.stdio import create_mcp_server
from servicenow_mcp.config import Settings
from tests.helpers import decode_response, get_tool_functions, get_tool_names

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("package", "count"),
    [("none", 1), ("core_readonly", 4), ("readonly", 13), ("full", 18)],
)
async def test_presets_register_expected_surface(
    settings: Settings, package: str, count: int
) -> None:
    settings.mcp_tool_package = package
    server = create_mcp_server(settings)
    async with server._lowlevel_server.lifespan(server._lowlevel_server):
        assert server.name == "servicenow-platform-mcp"
        assert len(await server.list_tools()) == count


async def test_safe_default_server_exposes_no_mutations() -> None:
    with patch.dict(
        "os.environ",
        {
            "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
            "SERVICENOW_OAUTH_CLIENT_ID": "test-client",
        },
        clear=True,
    ):
        settings = Settings(_env_file=None)
    assert settings.mcp_tool_package == "readonly"
    assert settings.is_production
    server = create_mcp_server(settings)
    async with server._lowlevel_server.lifespan(server._lowlevel_server):
        names = await get_tool_names(server)
    assert "service_catalog" in names
    assert not {
        "record_preview",
        "attachment_preview",
        "service_catalog_preview",
        "mutation_apply",
    } & set(names)


async def test_discovery_is_native_and_does_not_authorize(settings: Settings) -> None:
    settings.mcp_tool_package = "none"
    server = create_mcp_server(settings)
    async with server._lowlevel_server.lifespan(server._lowlevel_server):
        value = await get_tool_functions(server)["list_tool_packages"]()
    assert not value.is_error
    packages = decode_response(value)["data"]
    assert packages["none"] == []
    assert packages["core_readonly"] == ["query", "describe", "attachment"]


def test_missing_configuration_is_secret_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from servicenow_mcp.server import main

    monkeypatch.chdir(tmp_path)
    marker = "private-dsn-marker"
    with (
        patch.dict("os.environ", {"SENTRY_DSN": marker}, clear=True),
        pytest.raises(SystemExit) as failure,
    ):
        main()
    assert "SERVICENOW_INSTANCE_URL: Field required" in str(failure.value)
    assert ".env/.env.local in the working directory" in str(failure.value)
    assert marker not in str(failure.value)


@pytest.mark.parametrize("value", ["", "deev", "staging", "private-sensitive-value"])
def test_unknown_environment_cannot_enable_mutations(value: str) -> None:
    from pydantic import ValidationError

    with (
        patch.dict("os.environ", {}, clear=True),
        pytest.raises(ValidationError, match="Environment must"),
    ):
        Settings(
            servicenow_instance_url="https://test.service-now.com",
            servicenow_oauth_client_id="test-client",
            servicenow_env=value,
            _env_file=None,
        )
