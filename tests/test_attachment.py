"""Tests for attachment MCP tools."""

import base64
import importlib
from typing import Any

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.policy import DENIED_TABLES
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"
ATTACHMENT_SYS_ID = "a" * 32
TABLE_SYS_ID = "b" * 32
_attachment_common: Any = importlib.import_module("servicenow_mcp.tools._attachment_common")
attachment: Any = importlib.import_module("servicenow_mcp.tools.attachment")
MAX_ATTACHMENT_BYTES = _attachment_common.MAX_ATTACHMENT_BYTES


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


def _register_read_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict[str, Any]:
    """Register attachment read tools on a fresh MCP server."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.attachment import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return get_tool_functions(mcp)


def _register_write_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict[str, Any]:
    """Register attachment write tools on a fresh MCP server."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.attachment_write import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
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


class TestAttachmentReadTools:
    """Tests for attachment read tools."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_attachment_list_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Lists attachment metadata via the attachment API."""
        route = respx.get(f"{BASE_URL}/api/now/attachment").mock(
            return_value=httpx.Response(
                200,
                json={"result": [_metadata()]},
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_read_tools(settings, auth_provider)
        raw = await tools["attachment_list"](table_name="incident", table_sys_id=TABLE_SYS_ID, file_name="hello.txt")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"][0]["sys_id"] == ATTACHMENT_SYS_ID
        assert result["pagination"]["total"] == 1
        assert route.called

    @pytest.mark.asyncio()
    @respx.mock
    async def test_attachment_list_filters_denied_tables_without_table_filter(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Omits denied-table attachments instead of failing the full response."""
        denied_table = next(iter(DENIED_TABLES))
        respx.get(f"{BASE_URL}/api/now/attachment").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [_metadata(table_name="incident"), _metadata(table_name=denied_table, sys_id="c" * 32)]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_read_tools(settings, auth_provider)
        raw = await tools["attachment_list"]()
        result = decode_response(raw)

        assert result["status"] == "success"
        assert [record["table_name"] for record in result["data"]] == ["incident"]
        assert result["pagination"]["total"] == 1
        assert "Some attachments were omitted due to table access policy" in result["warnings"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_attachment_get_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns attachment metadata after metadata-first policy checks."""
        respx.get(f"{BASE_URL}/api/now/attachment/{ATTACHMENT_SYS_ID}").mock(
            return_value=httpx.Response(200, json={"result": _metadata()})
        )

        tools = _register_read_tools(settings, auth_provider)
        raw = await tools["attachment_get"](sys_id=ATTACHMENT_SYS_ID)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["table_name"] == "incident"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_attachment_download_success_with_base64(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Downloads binary content and returns a base64 payload."""
        respx.get(f"{BASE_URL}/api/now/attachment/{ATTACHMENT_SYS_ID}").mock(
            return_value=httpx.Response(200, json={"result": _metadata()})
        )
        respx.get(f"{BASE_URL}/api/now/attachment/{ATTACHMENT_SYS_ID}/file").mock(
            return_value=httpx.Response(200, content=b"hello")
        )

        tools = _register_read_tools(settings, auth_provider)
        raw = await tools["attachment_download"](sys_id=ATTACHMENT_SYS_ID)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["sys_id"] == ATTACHMENT_SYS_ID
        assert result["data"]["content_base64"] == base64.b64encode(b"hello").decode("ascii")

    @pytest.mark.asyncio()
    @respx.mock
    async def test_attachment_download_by_name_success_uses_metadata_sys_id(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Downloads by file name through metadata resolution, not caller path trust."""
        query_route = respx.get(f"{BASE_URL}/api/now/table/sys_attachment").mock(
            return_value=httpx.Response(200, json={"result": [_metadata()]}, headers={"X-Total-Count": "1"})
        )
        download_route = respx.get(f"{BASE_URL}/api/now/attachment/{ATTACHMENT_SYS_ID}/file").mock(
            return_value=httpx.Response(200, content=b"hello")
        )
        by_name_route = respx.get(f"{BASE_URL}/api/now/attachment/{TABLE_SYS_ID}/hello.txt/file").mock(
            return_value=httpx.Response(500)
        )

        tools = _register_read_tools(settings, auth_provider)
        raw = await tools["attachment_download_by_name"](
            table_name="incident",
            table_sys_id=TABLE_SYS_ID,
            file_name="hello.txt",
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["sys_id"] == ATTACHMENT_SYS_ID
        assert query_route.called
        assert query_route.calls.last is not None
        assert "ORDERBYsys_created_on" in query_route.calls.last.request.url.params["sysparm_query"]
        assert download_route.called
        assert not by_name_route.called

    @pytest.mark.asyncio()
    @respx.mock
    async def test_attachment_download_by_name_blocks_denied_table_via_metadata_lookup(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Denied tables are blocked after metadata resolution before download occurs."""
        denied_table = next(iter(DENIED_TABLES))
        respx.get(f"{BASE_URL}/api/now/table/sys_attachment").mock(
            return_value=httpx.Response(
                200, json={"result": [_metadata(table_name=denied_table)]}, headers={"X-Total-Count": "1"}
            )
        )
        download_route = respx.get(f"{BASE_URL}/api/now/attachment/{ATTACHMENT_SYS_ID}/file").mock(
            return_value=httpx.Response(200, content=b"hello")
        )

        tools = _register_read_tools(settings, auth_provider)
        raw = await tools["attachment_download_by_name"](
            table_name="incident",
            table_sys_id=TABLE_SYS_ID,
            file_name="hello.txt",
        )
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"]["message"].lower()
        assert not download_route.called

    @pytest.mark.asyncio()
    @respx.mock
    async def test_oversized_download_rejection(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Rejects downloads larger than the MCP attachment transfer limit before fetch."""
        respx.get(f"{BASE_URL}/api/now/attachment/{ATTACHMENT_SYS_ID}").mock(
            return_value=httpx.Response(
                200,
                json={"result": _metadata(size_bytes=str(MAX_ATTACHMENT_BYTES + 1))},
            )
        )
        download_route = respx.get(f"{BASE_URL}/api/now/attachment/{ATTACHMENT_SYS_ID}/file").mock(
            return_value=httpx.Response(200, content=b"x" * (MAX_ATTACHMENT_BYTES + 1))
        )

        tools = _register_read_tools(settings, auth_provider)
        raw = await tools["attachment_download"](sys_id=ATTACHMENT_SYS_ID)
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "exceeds the maximum supported size" in result["error"]["message"]
        assert not download_route.called

    @pytest.mark.asyncio()
    @respx.mock
    async def test_attachment_download_by_name_not_found_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Returns a stable not-found error when no metadata matches the logical attachment identity."""
        respx.get(f"{BASE_URL}/api/now/table/sys_attachment").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_read_tools(settings, auth_provider)
        raw = await tools["attachment_download_by_name"](
            table_name="incident",
            table_sys_id=TABLE_SYS_ID,
            file_name="hello.txt",
        )
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "was not found" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_attachment_download_by_name_warns_when_multiple_matches_exist(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Returns the earliest attachment plus a warning when metadata lookup finds multiple matches."""
        respx.get(f"{BASE_URL}/api/now/table/sys_attachment").mock(
            return_value=httpx.Response(
                200,
                json={"result": [_metadata(), _metadata(sys_id="c" * 32)]},
                headers={"X-Total-Count": "2"},
            )
        )
        respx.get(f"{BASE_URL}/api/now/attachment/{ATTACHMENT_SYS_ID}/file").mock(
            return_value=httpx.Response(200, content=b"hello")
        )

        tools = _register_read_tools(settings, auth_provider)
        raw = await tools["attachment_download_by_name"](
            table_name="incident",
            table_sys_id=TABLE_SYS_ID,
            file_name="hello.txt",
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["sys_id"] == ATTACHMENT_SYS_ID
        assert result["warnings"] == ["Multiple attachments matched; returned the earliest created attachment"]


class TestAttachmentWriteTools:
    """Tests for attachment write tools."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_attachment_upload_success_with_base64(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Uploads decoded base64 content through the attachment API."""
        route = respx.post(f"{BASE_URL}/api/now/attachment/file").mock(
            return_value=httpx.Response(201, json={"result": _metadata()})
        )

        tools = _register_write_tools(settings, auth_provider)
        raw = await tools["attachment_upload"](
            table_name="incident",
            table_sys_id=TABLE_SYS_ID,
            file_name="hello.txt",
            content_base64=base64.b64encode(b"hello").decode("ascii"),
            content_type="text/plain",
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["sys_id"] == ATTACHMENT_SYS_ID
        assert route.calls.last is not None
        assert route.calls.last.request.content == b"hello"

    @pytest.mark.asyncio()
    async def test_attachment_upload_invalid_base64_error_envelope(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Rejects invalid base64 input before any HTTP call."""
        tools = _register_write_tools(settings, auth_provider)
        raw = await tools["attachment_upload"](
            table_name="incident",
            table_sys_id=TABLE_SYS_ID,
            file_name="hello.txt",
            content_base64="not-base64!",
        )
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "invalid base64" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_oversized_upload_rejection(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Rejects uploads larger than the MCP attachment transfer limit."""
        tools = _register_write_tools(settings, auth_provider)
        oversized_content = base64.b64encode(b"x" * (MAX_ATTACHMENT_BYTES + 1)).decode("ascii")

        raw = await tools["attachment_upload"](
            table_name="incident",
            table_sys_id=TABLE_SYS_ID,
            file_name="hello.txt",
            content_base64=oversized_content,
        )
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "exceeds the maximum supported size" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_attachment_delete_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Deletes an attachment after metadata-first policy and write-gate checks."""
        respx.get(f"{BASE_URL}/api/now/attachment/{ATTACHMENT_SYS_ID}").mock(
            return_value=httpx.Response(200, json={"result": _metadata()})
        )
        delete_route = respx.delete(f"{BASE_URL}/api/now/attachment/{ATTACHMENT_SYS_ID}").mock(
            return_value=httpx.Response(204)
        )

        tools = _register_write_tools(settings, auth_provider)
        raw = await tools["attachment_delete"](sys_id=ATTACHMENT_SYS_ID)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["deleted"] is True
        assert delete_route.called

    @pytest.mark.asyncio()
    async def test_upload_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Blocks uploads in production environments."""
        tools = _register_write_tools(prod_settings, prod_auth_provider)
        raw = await tools["attachment_upload"](
            table_name="incident",
            table_sys_id=TABLE_SYS_ID,
            file_name="hello.txt",
            content_base64=base64.b64encode(b"hello").decode("ascii"),
        )
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "production" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_delete_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Blocks deletes in production environments after reading attachment metadata."""
        delete_route = respx.delete(
            f"{prod_settings.servicenow_instance_url}/api/now/attachment/{ATTACHMENT_SYS_ID}"
        ).mock(return_value=httpx.Response(204))
        respx.get(f"{prod_settings.servicenow_instance_url}/api/now/attachment/{ATTACHMENT_SYS_ID}").mock(
            return_value=httpx.Response(200, json={"result": _metadata()})
        )

        tools = _register_write_tools(prod_settings, prod_auth_provider)
        raw = await tools["attachment_delete"](sys_id=ATTACHMENT_SYS_ID)
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "production" in result["error"]["message"].lower()
        assert not delete_route.called


class TestAttachmentHelperFunctions:
    """Tests for small attachment helper branches."""

    def test_get_attachment_size_bytes_rejects_missing_value(self) -> None:
        """Missing size metadata fails fast."""
        with pytest.raises(ValueError, match="missing required field 'size_bytes'"):
            _attachment_common.get_attachment_size_bytes(_metadata(size_bytes=""))

    def test_get_attachment_size_bytes_rejects_non_integer_value(self) -> None:
        """Malformed size metadata fails fast."""
        with pytest.raises(ValueError, match="must be an integer"):
            _attachment_common.get_attachment_size_bytes(_metadata(size_bytes="not-a-number"))

    def test_get_attachment_size_bytes_rejects_negative_value(self) -> None:
        """Negative size metadata fails fast."""
        with pytest.raises(ValueError, match="must be non-negative"):
            _attachment_common.get_attachment_size_bytes(_metadata(size_bytes="-1"))

    def test_get_attachment_field_rejects_missing_value(self) -> None:
        """Missing required metadata fields fail fast."""
        metadata = _metadata()
        metadata["file_name"] = ""

        with pytest.raises(ValueError, match="missing required field 'file_name'"):
            _attachment_common.get_attachment_field(metadata, "file_name")

    def test_append_attachment_order_by_returns_original_query_when_blank(self) -> None:
        """Blank ordering leaves attachment queries unchanged."""
        assert attachment._append_attachment_order_by("table_name=incident", "") == "table_name=incident"

    def test_filter_and_mask_attachment_records_skips_repeated_blocked_table_without_warning_noise(self) -> None:
        """Once a table is denied, later attachments from the same table are skipped immediately."""
        denied_table = next(iter(DENIED_TABLES))

        records, omitted = attachment._filter_and_mask_attachment_records(
            [_metadata(table_name=denied_table), _metadata(table_name=denied_table, sys_id="c" * 32)],
            table_name="",
        )

        assert records == []
        assert omitted is True

    def test_build_attachment_list_metadata_includes_limit_cap_warning(self) -> None:
        """Pagination metadata includes the limit cap warning when safety reduces the limit."""
        pagination, warnings = attachment._build_attachment_list_metadata(
            requested_limit=50,
            effective_limit=20,
            offset=5,
            visible_total=3,
            omitted_by_policy=False,
        )

        assert pagination == {"offset": 5, "limit": 20, "total": 3}
        assert warnings == ["Limit capped at 20"]

    def test_require_bytes_content_rejects_non_bytes(self) -> None:
        """Download payloads only accept binary content."""
        with pytest.raises(TypeError, match="must be bytes"):
            attachment._require_bytes_content("hello")
