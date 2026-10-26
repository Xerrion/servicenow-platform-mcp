"""Local application construction, safe defaults, and command startup."""

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from servicenow_mcp.bootstrap.stdio import create_mcp_server
from servicenow_mcp.config import Settings
from tests.helpers import decode_response, get_tool_functions, get_tool_names

if TYPE_CHECKING:
    from pathlib import Path


async def test_safe_default_server_exposes_no_changes() -> None:
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
        discovery = decode_response(
            await get_tool_functions(server)["list_tool_packages"]()
        )
        assert set(discovery["data"]) == {"full", "readonly", "core_readonly", "none"}
    assert "service_catalog" in names
    assert not {
        "record_preview",
        "attachment_preview",
        "service_catalog_preview",
        "apply_change",
    } & set(names)


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


@pytest.mark.parametrize("value", [pytest.param("", id="")])
def test_unknown_environment_cannot_enable_changes(value: str) -> None:
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
