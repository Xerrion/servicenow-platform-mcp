"""Tests for the unified ``attachment`` and ``attachment_write`` tools (Phase 3a)."""

from __future__ import annotations

import base64
import importlib
from typing import Any

import httpx
import pytest
import respx
from mcp.server import MCPServer

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.policy import DENIED_TABLES
from servicenow_mcp.tools.attachment import register_tools as register_read_tools
from servicenow_mcp.tools.attachment_write import register_tools as register_write_tools
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"
ATTACHMENT_SYS_ID = "a" * 32
TABLE_SYS_ID = "b" * 32

_attachment_common: Any = importlib.import_module("servicenow_mcp.tools._attachment_common")
MAX_ATTACHMENT_BYTES = _attachment_common.MAX_ATTACHMENT_BYTES


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """BasicAuthProvider for the unified attachment test scope."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict[str, Any]:
    """Register the unified attachment tools on a fresh MCP and return callables."""
    mcp = MCPServer("test")
    register_read_tools(mcp, settings, auth_provider)
    register_write_tools(mcp, settings, auth_provider)
    return get_tool_functions(mcp)


def _metadata(
    *, table_name: str = "incident", sys_id: str = ATTACHMENT_SYS_ID, size_bytes: str = "5"
) -> dict[str, str]:
    """Build a representative attachment metadata payload."""
    return {
        "sys_id": sys_id,
        "table_name": table_name,
        "table_sys_id": TABLE_SYS_ID,
        "file_name": "hello.txt",
        "content_type": "text/plain",
        "size_bytes": size_bytes,
    }


# ---------------------------------------------------------------------------
# attachment (read) — 9 tests
# ---------------------------------------------------------------------------


class TestUnifiedAttachmentRead:
    """Tests for the unified ``attachment`` action-dispatching tool."""

    @pytest.mark.asyncio()
    async def test_unknown_action_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """An unrecognized action yields an error envelope, not an exception."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["attachment"](action="bogus"))

        assert result["status"] == "error"
        assert "unknown action" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_action_lists_attachments(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """The list action returns attachment metadata for the parent record."""
        respx.get(f"{BASE_URL}/api/now/attachment").mock(
            return_value=httpx.Response(200, json={"result": [_metadata()]}, headers={"X-Total-Count": "1"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["attachment"](action="list", table="incident", table_sys_id=TABLE_SYS_ID))

        assert result["status"] == "success"
        assert result["data"][0]["sys_id"] == ATTACHMENT_SYS_ID
        assert result["pagination"]["total"] == 1

    @pytest.mark.asyncio()
    async def test_list_action_missing_table_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """The list action requires a table identifier."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["attachment"](action="list", table_sys_id=TABLE_SYS_ID))

        assert result["status"] == "error"
        assert "table is required" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_action_returns_metadata(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """The get action returns masked metadata for a single attachment."""
        respx.get(f"{BASE_URL}/api/now/attachment/{ATTACHMENT_SYS_ID}").mock(
            return_value=httpx.Response(200, json={"result": _metadata()})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["attachment"](action="get", sys_id=ATTACHMENT_SYS_ID))

        assert result["status"] == "success"
        assert result["data"]["table_name"] == "incident"
        assert result["data"]["sys_id"] == ATTACHMENT_SYS_ID

    @pytest.mark.asyncio()
    async def test_get_action_missing_sys_id_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """The get action requires a sys_id."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["attachment"](action="get"))

        assert result["status"] == "error"
        assert "sys_id is required" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_download_action_returns_content_base64(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """The download action returns metadata plus base64-encoded payload."""
        respx.get(f"{BASE_URL}/api/now/attachment/{ATTACHMENT_SYS_ID}").mock(
            return_value=httpx.Response(200, json={"result": _metadata()})
        )
        respx.get(f"{BASE_URL}/api/now/attachment/{ATTACHMENT_SYS_ID}/file").mock(
            return_value=httpx.Response(200, content=b"hello")
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["attachment"](action="download", sys_id=ATTACHMENT_SYS_ID))

        assert result["status"] == "success"
        assert result["data"]["content_base64"] == base64.b64encode(b"hello").decode("ascii")

    @pytest.mark.asyncio()
    @respx.mock
    async def test_download_by_name_action_returns_content_base64(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """The download_by_name action resolves metadata then downloads payload."""
        respx.get(f"{BASE_URL}/api/now/table/sys_attachment").mock(
            return_value=httpx.Response(200, json={"result": [_metadata()]}, headers={"X-Total-Count": "1"})
        )
        respx.get(f"{BASE_URL}/api/now/attachment/{ATTACHMENT_SYS_ID}/file").mock(
            return_value=httpx.Response(200, content=b"hello")
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(
            await tools["attachment"](
                action="download_by_name",
                table="incident",
                table_sys_id=TABLE_SYS_ID,
                file_name="hello.txt",
            )
        )

        assert result["status"] == "success"
        assert result["data"]["sys_id"] == ATTACHMENT_SYS_ID
        assert result["data"]["content_base64"] == base64.b64encode(b"hello").decode("ascii")

    @pytest.mark.asyncio()
    async def test_download_by_name_missing_args_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """The download_by_name action requires table, table_sys_id, and file_name."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(
            await tools["attachment"](
                action="download_by_name",
                table="incident",
                table_sys_id=TABLE_SYS_ID,
                # file_name missing
            )
        )

        assert result["status"] == "error"
        assert "file_name is required" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_denied_table_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Listing attachments on a denied table is rejected by policy."""
        denied_table = next(iter(DENIED_TABLES))
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(
            await tools["attachment"](action="list", table=denied_table, table_sys_id=TABLE_SYS_ID)
        )

        assert result["status"] == "error"
        assert "denied" in result["error"]["message"].lower()


# ---------------------------------------------------------------------------
# attachment_write — 6 tests
# ---------------------------------------------------------------------------


class TestUnifiedAttachmentWrite:
    """Tests for the unified ``attachment_write`` action-dispatching tool."""

    @pytest.mark.asyncio()
    async def test_unknown_action_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """An unrecognized write action yields an error envelope."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["attachment_write"](action="bogus"))

        assert result["status"] == "error"
        assert "unknown action" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_upload_action_remains_gated_in_production(
        self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider
    ) -> None:
        """The opt-in write group retains runtime production gating."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)
        result = decode_response(
            await tools["attachment_write"](
                action="upload",
                table="incident",
                table_sys_id=TABLE_SYS_ID,
                file_name="hello.txt",
                content_base64=base64.b64encode(b"hello").decode("ascii"),
            )
        )

        assert result["status"] == "error"
        assert "production" in result["error"]["message"].lower()
        assert not respx.calls.called

    @pytest.mark.asyncio()
    @respx.mock
    async def test_upload_action_creates_attachment(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """The upload action decodes content and posts via the attachment API."""
        route = respx.post(f"{BASE_URL}/api/now/attachment/file").mock(
            return_value=httpx.Response(201, json={"result": _metadata()})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(
            await tools["attachment_write"](
                action="upload",
                table="incident",
                table_sys_id=TABLE_SYS_ID,
                file_name="hello.txt",
                content_base64=base64.b64encode(b"hello").decode("ascii"),
                content_type="text/plain",
            )
        )

        assert result["status"] == "success"
        assert result["data"]["sys_id"] == ATTACHMENT_SYS_ID
        assert route.calls.last is not None
        assert route.calls.last.request.content == b"hello"

    @pytest.mark.asyncio()
    async def test_upload_action_missing_args_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """The upload action requires table, table_sys_id, file_name, content_base64."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(
            await tools["attachment_write"](
                action="upload",
                table="incident",
                table_sys_id=TABLE_SYS_ID,
                # file_name missing
                content_base64=base64.b64encode(b"hello").decode("ascii"),
            )
        )

        assert result["status"] == "error"
        assert "file_name is required" in result["error"]["message"]

    @pytest.mark.asyncio()
    async def test_upload_oversize_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Uploads whose base64 decodes above the cap are rejected before HTTP."""
        oversized_b64 = base64.b64encode(b"x" * (MAX_ATTACHMENT_BYTES + 1)).decode("ascii")

        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(
            await tools["attachment_write"](
                action="upload",
                table="incident",
                table_sys_id=TABLE_SYS_ID,
                file_name="hello.txt",
                content_base64=oversized_b64,
            )
        )

        assert result["status"] == "error"
        assert "exceeds the maximum supported size" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_delete_action_removes_attachment(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """The delete action looks up metadata then issues DELETE."""
        respx.get(f"{BASE_URL}/api/now/attachment/{ATTACHMENT_SYS_ID}").mock(
            return_value=httpx.Response(200, json={"result": _metadata()})
        )
        delete_route = respx.delete(f"{BASE_URL}/api/now/attachment/{ATTACHMENT_SYS_ID}").mock(
            return_value=httpx.Response(204)
        )

        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["attachment_write"](action="delete", sys_id=ATTACHMENT_SYS_ID))

        assert result["status"] == "success"
        assert result["data"]["deleted"] is True
        assert delete_route.called

    @pytest.mark.asyncio()
    async def test_delete_action_missing_sys_id_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """The delete action requires a sys_id."""
        tools = _register_and_get_tools(settings, auth_provider)
        result = decode_response(await tools["attachment_write"](action="delete"))

        assert result["status"] == "error"
        assert "sys_id is required" in result["error"]["message"]
