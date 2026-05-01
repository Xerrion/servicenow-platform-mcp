"""Tests for record-level write tools (record_create, record_update, record_delete, previews, record_apply)."""

import json
from typing import Any

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from servicenow_mcp.policy import DENIED_TABLES
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"
METADATA_URL = f"{BASE_URL}/api/now/table/sys_dictionary"
NO_MANDATORY_RESPONSE = httpx.Response(200, json={"result": []})

# Valid 32-char hex sys_ids for tests (validate_sys_id requires this format)
SYS_ID_INC001 = "a" * 32  # aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
SYS_ID_MISSING = "b" * 32  # bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict[str, Any]:
    """Helper: register record_write tools on a fresh MCP server and return tool map."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.record_write import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return get_tool_functions(mcp)


# -- record_create -------------------------------------------------------------


class TestRecordCreate:
    """Tests for the record_create tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_creates_record_and_returns_masked(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Creates a record and returns it with sensitive fields masked."""
        respx.get(METADATA_URL).mock(return_value=NO_MANDATORY_RESPONSE)
        respx.post(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                201,
                json={
                    "result": {
                        "sys_id": "new001",
                        "short_description": "Test incident",
                        "state": "1",
                        "password": "s3cret",  # NOSONAR
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_create"](
            table="incident",
            data=json.dumps({"short_description": "Test incident", "state": "1"}),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["table"] == "incident"
        assert result["data"]["sys_id"] == "new001"
        assert result["data"]["record"]["short_description"] == "Test incident"
        assert result["data"]["record"]["password"] == "***MASKED***"  # NOSONAR

    @pytest.mark.asyncio()
    async def test_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Returns error when environment is production."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)

        raw = await tools["record_create"](
            table="incident",
            data=json.dumps({"short_description": "Test"}),
        )
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "production" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_denied_table_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error when table is denied by policy."""
        denied = next(iter(DENIED_TABLES))
        tools = _register_and_get_tools(settings, auth_provider)

        raw = await tools["record_create"](
            table=denied,
            data=json.dumps({"value": "test"}),
        )
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "denied" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_invalid_json_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error when data is not valid JSON."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_create"](table="incident", data="not valid json")
        result = decode_response(raw)

        assert result["status"] == "error"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_acl_denied_returns_clear_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns clear error when ServiceNow ACL denies the operation."""
        respx.get(METADATA_URL).mock(return_value=NO_MANDATORY_RESPONSE)
        respx.post(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(403, json={"error": {"message": "ACL denied"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_create"](
            table="incident",
            data=json.dumps({"short_description": "Test"}),
        )
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "acl" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_generic_exception(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error envelope on unexpected exception."""
        from unittest.mock import AsyncMock, patch

        tools = _register_and_get_tools(settings, auth_provider)
        with patch(
            "servicenow_mcp.tools.record_write.ServiceNowClient.__aenter__",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection failed"),
        ):
            raw = await tools["record_create"](
                table="incident",
                data=json.dumps({"short_description": "Test"}),
            )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "connection failed" in result["error"]["message"]


# -- record_preview_create -----------------------------------------------------


class TestRecordPreviewCreate:
    """Tests for the record_preview_create tool."""

    @pytest.mark.asyncio()
    async def test_returns_token_and_data(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns a preview token and the masked data summary."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_preview_create"](
            table="incident",
            data=json.dumps({"short_description": "Test", "password": "s3cret"}),  # NOSONAR
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert "token" in result["data"]
        assert result["data"]["table"] == "incident"
        assert result["data"]["action"] == "create"
        assert result["data"]["data"]["password"] == "***MASKED***"  # NOSONAR

    @pytest.mark.asyncio()
    async def test_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Returns error when environment is production."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)

        raw = await tools["record_preview_create"](
            table="incident",
            data=json.dumps({"short_description": "Test"}),
        )
        result = decode_response(raw)
        assert result["status"] == "error"

    @pytest.mark.asyncio()
    async def test_invalid_json_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error when data is not valid JSON."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_preview_create"](table="incident", data="{bad json")
        result = decode_response(raw)
        assert result["status"] == "error"


# -- record_update -------------------------------------------------------------


class TestRecordUpdate:
    """Tests for the record_update tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_updates_record_and_returns_masked(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Updates a record and returns it with sensitive fields masked."""
        respx.patch(f"{BASE_URL}/api/now/table/incident/{SYS_ID_INC001}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": SYS_ID_INC001,
                        "state": "2",
                        "password": "s3cret",  # NOSONAR
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_update"](
            table="incident",
            sys_id=SYS_ID_INC001,
            changes=json.dumps({"state": "2"}),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["sys_id"] == SYS_ID_INC001
        assert result["data"]["record"]["state"] == "2"
        assert result["data"]["record"]["password"] == "***MASKED***"  # NOSONAR

    @pytest.mark.asyncio()
    async def test_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Returns error in production."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)

        raw = await tools["record_update"](
            table="incident",
            sys_id=SYS_ID_INC001,
            changes=json.dumps({"state": "2"}),
        )
        result = decode_response(raw)
        assert result["status"] == "error"

    @pytest.mark.asyncio()
    async def test_denied_table_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error for denied table."""
        denied = next(iter(DENIED_TABLES))
        tools = _register_and_get_tools(settings, auth_provider)

        raw = await tools["record_update"](
            table=denied,
            sys_id="abc",
            changes=json.dumps({"value": "x"}),
        )
        result = decode_response(raw)
        assert result["status"] == "error"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_not_found_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error when record doesn't exist."""
        respx.patch(f"{BASE_URL}/api/now/table/incident/{SYS_ID_MISSING}").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_update"](
            table="incident",
            sys_id=SYS_ID_MISSING,
            changes=json.dumps({"state": "2"}),
        )
        result = decode_response(raw)
        assert result["status"] == "error"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_acl_denied_returns_clear_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns clear ACL error."""
        respx.patch(f"{BASE_URL}/api/now/table/incident/{SYS_ID_INC001}").mock(
            return_value=httpx.Response(403, json={"error": {"message": "ACL denied"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_update"](
            table="incident",
            sys_id=SYS_ID_INC001,
            changes=json.dumps({"state": "2"}),
        )
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "acl" in result["error"]["message"].lower()


# -- record_preview_update -----------------------------------------------------


class TestRecordPreviewUpdate:
    """Tests for the record_preview_update tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_diff_and_token(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Fetches current record and returns field-level diff with token."""
        respx.get(f"{BASE_URL}/api/now/table/incident/{SYS_ID_INC001}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": SYS_ID_INC001,
                        "state": "1",
                        "short_description": "Original",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_preview_update"](
            table="incident",
            sys_id=SYS_ID_INC001,
            changes=json.dumps({"state": "2", "short_description": "Updated"}),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert "token" in result["data"]
        assert result["data"]["diff"]["state"]["old"] == "1"
        assert result["data"]["diff"]["state"]["new"] == "2"
        assert result["data"]["diff"]["short_description"]["old"] == "Original"
        assert result["data"]["diff"]["short_description"]["new"] == "Updated"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_masks_sensitive_fields_in_diff(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Sensitive fields in the diff are masked."""
        respx.get(f"{BASE_URL}/api/now/table/incident/{SYS_ID_INC001}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": SYS_ID_INC001,
                        "password": "old_password",  # NOSONAR
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_preview_update"](
            table="incident",
            sys_id=SYS_ID_INC001,
            changes=json.dumps({"password": "new_password"}),  # NOSONAR
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["diff"]["password"]["old"] == "***MASKED***"  # NOSONAR
        assert result["data"]["diff"]["password"]["new"] == "***MASKED***"  # NOSONAR

    @pytest.mark.asyncio()
    async def test_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Returns error in production."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)

        raw = await tools["record_preview_update"](
            table="incident",
            sys_id=SYS_ID_INC001,
            changes=json.dumps({"state": "2"}),
        )
        result = decode_response(raw)
        assert result["status"] == "error"


# -- record_delete -------------------------------------------------------------


class TestRecordDelete:
    """Tests for the record_delete tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_deletes_record(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Deletes a record and returns confirmation."""
        respx.delete(f"{BASE_URL}/api/now/table/incident/{SYS_ID_INC001}").mock(return_value=httpx.Response(204))

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_delete"](table="incident", sys_id=SYS_ID_INC001)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["table"] == "incident"
        assert result["data"]["sys_id"] == SYS_ID_INC001
        assert result["data"]["deleted"] is True

    @pytest.mark.asyncio()
    async def test_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Returns error in production."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)

        raw = await tools["record_delete"](table="incident", sys_id=SYS_ID_INC001)
        result = decode_response(raw)
        assert result["status"] == "error"

    @pytest.mark.asyncio()
    async def test_denied_table_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error for denied table."""
        denied = next(iter(DENIED_TABLES))
        tools = _register_and_get_tools(settings, auth_provider)

        raw = await tools["record_delete"](table=denied, sys_id="abc")
        result = decode_response(raw)
        assert result["status"] == "error"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_not_found_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error when record doesn't exist."""
        respx.delete(f"{BASE_URL}/api/now/table/incident/{SYS_ID_MISSING}").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_delete"](table="incident", sys_id=SYS_ID_MISSING)
        result = decode_response(raw)
        assert result["status"] == "error"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_acl_denied_returns_clear_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns clear ACL error on 403."""
        respx.delete(f"{BASE_URL}/api/now/table/incident/{SYS_ID_INC001}").mock(
            return_value=httpx.Response(403, json={"error": {"message": "ACL denied"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_delete"](table="incident", sys_id=SYS_ID_INC001)
        result = decode_response(raw)
        assert result["status"] == "error"
        assert "acl" in result["error"]["message"].lower()


# -- record_preview_delete -----------------------------------------------------


class TestRecordPreviewDelete:
    """Tests for the record_preview_delete tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_snapshot_and_token(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Fetches record and returns snapshot with preview token."""
        respx.get(f"{BASE_URL}/api/now/table/incident/{SYS_ID_INC001}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": SYS_ID_INC001,
                        "short_description": "Test incident",
                        "state": "1",
                        "password": "s3cret",  # NOSONAR
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_preview_delete"](table="incident", sys_id=SYS_ID_INC001)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert "token" in result["data"]
        assert result["data"]["table"] == "incident"
        assert result["data"]["sys_id"] == SYS_ID_INC001
        assert result["data"]["record_snapshot"]["short_description"] == "Test incident"
        assert result["data"]["record_snapshot"]["password"] == "***MASKED***"  # NOSONAR

    @pytest.mark.asyncio()
    async def test_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Returns error in production."""
        tools = _register_and_get_tools(prod_settings, prod_auth_provider)

        raw = await tools["record_preview_delete"](table="incident", sys_id=SYS_ID_INC001)
        result = decode_response(raw)
        assert result["status"] == "error"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_not_found_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error when record doesn't exist."""
        respx.get(f"{BASE_URL}/api/now/table/incident/{SYS_ID_MISSING}").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_preview_delete"](table="incident", sys_id=SYS_ID_MISSING)
        result = decode_response(raw)
        assert result["status"] == "error"


# -- record_apply --------------------------------------------------------------


class TestRecordApply:
    """Tests for the record_apply tool (applies any previewed action)."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_apply_create(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Applies a previewed create action."""
        respx.get(METADATA_URL).mock(return_value=NO_MANDATORY_RESPONSE)
        # Phase 1: Preview
        tools = _register_and_get_tools(settings, auth_provider)
        preview_raw = await tools["record_preview_create"](
            table="incident",
            data=json.dumps({"short_description": "Test", "state": "1"}),
        )
        token = decode_response(preview_raw)["data"]["token"]

        # Phase 2: Apply
        respx.post(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                201,
                json={
                    "result": {
                        "sys_id": "new001",
                        "short_description": "Test",
                        "state": "1",
                    }
                },
            )
        )

        raw = await tools["record_apply"](preview_token=token)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["action"] == "create"
        assert result["data"]["sys_id"] == "new001"
        assert result["data"]["record"]["short_description"] == "Test"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_apply_update(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Applies a previewed update action."""
        # Phase 1: Preview (needs GET mock for current record)
        respx.get(f"{BASE_URL}/api/now/table/incident/{SYS_ID_INC001}").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": SYS_ID_INC001, "state": "1"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        preview_raw = await tools["record_preview_update"](
            table="incident",
            sys_id=SYS_ID_INC001,
            changes=json.dumps({"state": "2"}),
        )
        token = decode_response(preview_raw)["data"]["token"]

        # Phase 2: Apply
        respx.patch(f"{BASE_URL}/api/now/table/incident/{SYS_ID_INC001}").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": SYS_ID_INC001, "state": "2"}},
            )
        )

        raw = await tools["record_apply"](preview_token=token)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["action"] == "update"
        assert result["data"]["record"]["state"] == "2"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_apply_delete(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Applies a previewed delete action."""
        # Phase 1: Preview (needs GET mock)
        respx.get(f"{BASE_URL}/api/now/table/incident/{SYS_ID_INC001}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": SYS_ID_INC001,
                        "short_description": "To be deleted",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        preview_raw = await tools["record_preview_delete"](table="incident", sys_id=SYS_ID_INC001)
        token = decode_response(preview_raw)["data"]["token"]

        # Phase 2: Apply
        respx.delete(f"{BASE_URL}/api/now/table/incident/{SYS_ID_INC001}").mock(return_value=httpx.Response(204))

        raw = await tools["record_apply"](preview_token=token)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["action"] == "delete"
        assert result["data"]["deleted"] is True

    @pytest.mark.asyncio()
    async def test_invalid_token_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error for an invalid/unknown token."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_apply"](preview_token="nonexistent-token")
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "invalid" in result["error"]["message"].lower() or "expired" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_token_consumed_only_once(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Token is single-use - second apply with same token fails."""
        respx.get(METADATA_URL).mock(return_value=NO_MANDATORY_RESPONSE)
        # Preview a create
        tools = _register_and_get_tools(settings, auth_provider)
        preview_raw = await tools["record_preview_create"](
            table="incident",
            data=json.dumps({"short_description": "Test"}),
        )
        token = decode_response(preview_raw)["data"]["token"]

        # First apply succeeds
        respx.post(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                201,
                json={"result": {"sys_id": "new001", "short_description": "Test"}},
            )
        )
        raw1 = await tools["record_apply"](preview_token=token)
        result1 = decode_response(raw1)
        assert result1["status"] == "success"

        # Second apply with same token fails
        raw2 = await tools["record_apply"](preview_token=token)
        result2 = decode_response(raw2)
        assert result2["status"] == "error"
        assert "invalid" in result2["error"]["message"].lower() or "expired" in result2["error"]["message"].lower()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_apply_masks_sensitive_fields(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Applied create masks sensitive fields in the returned record."""
        respx.get(METADATA_URL).mock(return_value=NO_MANDATORY_RESPONSE)
        tools = _register_and_get_tools(settings, auth_provider)
        preview_raw = await tools["record_preview_create"](
            table="incident",
            data=json.dumps({"short_description": "Test"}),
        )
        token = decode_response(preview_raw)["data"]["token"]

        respx.post(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                201,
                json={
                    "result": {
                        "sys_id": "new001",
                        "short_description": "Test",
                        "password": "s3cret",  # NOSONAR
                    }
                },
            )
        )

        raw = await tools["record_apply"](preview_token=token)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["record"]["password"] == "***MASKED***"  # NOSONAR

    @pytest.mark.asyncio()
    @respx.mock
    async def test_apply_acl_denied(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns clear ACL error when ServiceNow denies the apply operation."""
        respx.get(METADATA_URL).mock(return_value=NO_MANDATORY_RESPONSE)
        tools = _register_and_get_tools(settings, auth_provider)
        preview_raw = await tools["record_preview_create"](
            table="incident",
            data=json.dumps({"short_description": "Test"}),
        )
        token = decode_response(preview_raw)["data"]["token"]

        respx.post(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(403, json={"error": {"message": "ACL denied"}})
        )

        raw = await tools["record_apply"](preview_token=token)
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "acl" in result["error"]["message"].lower()


# -- mandatory field validation ------------------------------------------------

METADATA_WITH_TWO_MANDATORY = {
    "result": [
        {
            "element": "short_description",
            "mandatory": "true",
            "internal_type": "string",
        },
        {
            "element": "category",
            "mandatory": "true",
            "internal_type": "string",
        },
        {
            "element": "description",
            "mandatory": "false",
            "internal_type": "string",
        },
    ]
}

METADATA_NO_MANDATORY = {
    "result": [
        {
            "element": "short_description",
            "mandatory": "false",
            "internal_type": "string",
        },
        {
            "element": "description",
            "mandatory": "false",
            "internal_type": "string",
        },
    ]
}


class TestMandatoryFieldValidation:
    """Tests for mandatory field pre-flight validation on record creation."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_record_create_missing_mandatory_fields(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Returns error when mandatory fields are missing from create data."""
        respx.get(METADATA_URL).mock(return_value=httpx.Response(200, json=METADATA_WITH_TWO_MANDATORY))

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_create"](
            table="incident",
            data=json.dumps({"short_description": "Test incident"}),
        )
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "missing_fields" in result["data"]
        assert "category" in result["data"]["missing_fields"]
        assert "Missing mandatory fields" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_record_create_all_mandatory_present(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Proceeds with create when all mandatory fields are present."""
        respx.get(METADATA_URL).mock(return_value=httpx.Response(200, json=METADATA_WITH_TWO_MANDATORY))
        respx.post(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                201,
                json={
                    "result": {
                        "sys_id": "new001",
                        "short_description": "Test",
                        "category": "software",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_create"](
            table="incident",
            data=json.dumps({"short_description": "Test", "category": "software"}),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["sys_id"] == "new001"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_record_preview_create_missing_mandatory_fields(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Returns error when mandatory fields are missing from preview create data."""
        respx.get(METADATA_URL).mock(return_value=httpx.Response(200, json=METADATA_WITH_TWO_MANDATORY))

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_preview_create"](
            table="incident",
            data=json.dumps({"short_description": "Test"}),
        )
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "category" in result["data"]["missing_fields"]
        assert "Missing mandatory fields" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_record_preview_create_all_mandatory_present(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Returns preview token when all mandatory fields are present."""
        respx.get(METADATA_URL).mock(return_value=httpx.Response(200, json=METADATA_WITH_TWO_MANDATORY))

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_preview_create"](
            table="incident",
            data=json.dumps({"short_description": "Test", "category": "software"}),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert "token" in result["data"]
        assert result["data"]["action"] == "create"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_record_apply_create_missing_mandatory_fields(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """record_apply catches newly mandatory fields at apply time."""
        # Phase 1: Preview succeeds - metadata has only 1 mandatory field
        respx.get(METADATA_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "element": "short_description",
                            "mandatory": "true",
                            "internal_type": "string",
                        },
                    ]
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        preview_raw = await tools["record_preview_create"](
            table="incident",
            data=json.dumps({"short_description": "Test"}),
        )
        token = decode_response(preview_raw)["data"]["token"]

        # Phase 2: Apply - metadata now returns a NEW mandatory field
        respx.get(METADATA_URL).mock(return_value=httpx.Response(200, json=METADATA_WITH_TWO_MANDATORY))

        raw = await tools["record_apply"](preview_token=token)
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "category" in result["data"]["missing_fields"]
        assert "Missing mandatory fields" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_record_create_no_mandatory_fields(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Proceeds normally when table has no mandatory fields."""
        respx.get(METADATA_URL).mock(return_value=httpx.Response(200, json=METADATA_NO_MANDATORY))
        respx.post(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                201,
                json={
                    "result": {
                        "sys_id": "new002",
                        "short_description": "Test",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_create"](
            table="incident",
            data=json.dumps({"short_description": "Test"}),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["sys_id"] == "new002"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_record_create_metadata_unavailable(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Create proceeds when metadata endpoint returns 500 (best-effort)."""
        respx.get(METADATA_URL).mock(return_value=httpx.Response(500, json={"error": {"message": "Internal error"}}))
        respx.post(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                201,
                json={
                    "result": {
                        "sys_id": "new003",
                        "short_description": "Test",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_create"](
            table="incident",
            data=json.dumps({"short_description": "Test"}),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["sys_id"] == "new003"


# -- Payload validation via parse_payload_json --------------------------------


class TestPayloadValidation:
    """Tests covering size, key, and shape validation now provided by parse_payload_json."""

    @pytest.mark.asyncio()
    async def test_oversize_payload_rejected(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Oversize JSON payload returns an error envelope without hitting the network."""
        tools = _register_and_get_tools(settings, auth_provider)
        oversized = json.dumps({"short_description": "x" * 300_000})
        raw = await tools["record_create"](table="incident", data=oversized)
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "maximum size" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_invalid_top_level_key_rejected(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Top-level key with a space fails validate_identifier and returns an error envelope."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_create"](
            table="incident",
            data=json.dumps({"FOO BAR": "x"}),
        )
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "invalid key" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_non_object_payload_rejected(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """JSON array (non-object) payload returns an error envelope."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_create"](table="incident", data="[1,2,3]")
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "json object" in result["error"]["message"].lower()

    @pytest.mark.asyncio()
    async def test_update_changes_validates_keys(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """record_update validates keys in the changes payload."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_update"](
            table="incident",
            sys_id=SYS_ID_INC001,
            changes=json.dumps({"BAD KEY": "v"}),
        )
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "invalid key" in result["error"]["message"].lower()


# -- _check_mandatory_fields semantics ----------------------------------------


class TestCheckMandatoryFieldsSemantics:
    """Direct unit tests for _check_mandatory_fields helper."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_empty_string_treated_as_missing(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """An empty string for a mandatory field is treated as missing."""
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.tools.record_write import _check_mandatory_fields

        respx.get(METADATA_URL).mock(return_value=httpx.Response(200, json=METADATA_WITH_TWO_MANDATORY))

        async with ServiceNowClient(settings, auth_provider) as client:
            missing = await _check_mandatory_fields(
                client, "incident", {"short_description": "", "category": "software"}
            )

        assert missing == ["short_description"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_none_value_treated_as_missing(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """A None value for a mandatory field is treated as missing."""
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.tools.record_write import _check_mandatory_fields

        respx.get(METADATA_URL).mock(return_value=httpx.Response(200, json=METADATA_WITH_TWO_MANDATORY))

        async with ServiceNowClient(settings, auth_provider) as client:
            missing = await _check_mandatory_fields(
                client, "incident", {"short_description": None, "category": "software"}
            )

        assert missing == ["short_description"]
