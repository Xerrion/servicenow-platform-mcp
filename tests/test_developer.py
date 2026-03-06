"""Tests for record CRUD tools (record_create, record_update, record_delete + preview/apply)."""

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


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings: Any, auth_provider: BasicAuthProvider) -> dict[str, Any]:
    """Helper: register developer tools on a fresh MCP server and return tool map."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.developer import register_tools

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
        respx.post(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                201,
                json={
                    "result": {
                        "sys_id": "new001",
                        "short_description": "Test incident",
                        "state": "1",
                        "password": "s3cret",
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
        assert result["data"]["record"]["password"] == "***MASKED***"

    @pytest.mark.asyncio()
    async def test_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Returns error when environment is production."""
        from mcp.server.fastmcp import FastMCP

        from servicenow_mcp.tools.developer import register_tools

        mcp = FastMCP("test")
        register_tools(mcp, prod_settings, prod_auth_provider)
        tools = get_tool_functions(mcp)

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
            "servicenow_mcp.tools.developer.ServiceNowClient.__aenter__",
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
            data=json.dumps({"short_description": "Test", "password": "s3cret"}),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert "token" in result["data"]
        assert result["data"]["table"] == "incident"
        assert result["data"]["action"] == "create"
        assert result["data"]["data"]["password"] == "***MASKED***"

    @pytest.mark.asyncio()
    async def test_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Returns error when environment is production."""
        from mcp.server.fastmcp import FastMCP

        from servicenow_mcp.tools.developer import register_tools

        mcp = FastMCP("test")
        register_tools(mcp, prod_settings, prod_auth_provider)
        tools = get_tool_functions(mcp)

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
        respx.patch(f"{BASE_URL}/api/now/table/incident/inc001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "inc001",
                        "state": "2",
                        "password": "s3cret",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_update"](
            table="incident",
            sys_id="inc001",
            changes=json.dumps({"state": "2"}),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["sys_id"] == "inc001"
        assert result["data"]["record"]["state"] == "2"
        assert result["data"]["record"]["password"] == "***MASKED***"

    @pytest.mark.asyncio()
    async def test_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Returns error in production."""
        from mcp.server.fastmcp import FastMCP

        from servicenow_mcp.tools.developer import register_tools

        mcp = FastMCP("test")
        register_tools(mcp, prod_settings, prod_auth_provider)
        tools = get_tool_functions(mcp)

        raw = await tools["record_update"](
            table="incident",
            sys_id="inc001",
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
        respx.patch(f"{BASE_URL}/api/now/table/incident/missing").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_update"](
            table="incident",
            sys_id="missing",
            changes=json.dumps({"state": "2"}),
        )
        result = decode_response(raw)
        assert result["status"] == "error"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_acl_denied_returns_clear_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns clear ACL error."""
        respx.patch(f"{BASE_URL}/api/now/table/incident/inc001").mock(
            return_value=httpx.Response(403, json={"error": {"message": "ACL denied"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_update"](
            table="incident",
            sys_id="inc001",
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
        respx.get(f"{BASE_URL}/api/now/table/incident/inc001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "inc001",
                        "state": "1",
                        "short_description": "Original",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_preview_update"](
            table="incident",
            sys_id="inc001",
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
        respx.get(f"{BASE_URL}/api/now/table/incident/inc001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "inc001",
                        "password": "old_password",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_preview_update"](
            table="incident",
            sys_id="inc001",
            changes=json.dumps({"password": "new_password"}),
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["diff"]["password"]["old"] == "***MASKED***"
        assert result["data"]["diff"]["password"]["new"] == "***MASKED***"

    @pytest.mark.asyncio()
    async def test_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Returns error in production."""
        from mcp.server.fastmcp import FastMCP

        from servicenow_mcp.tools.developer import register_tools

        mcp = FastMCP("test")
        register_tools(mcp, prod_settings, prod_auth_provider)
        tools = get_tool_functions(mcp)

        raw = await tools["record_preview_update"](
            table="incident",
            sys_id="inc001",
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
        respx.delete(f"{BASE_URL}/api/now/table/incident/inc001").mock(return_value=httpx.Response(204))

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_delete"](table="incident", sys_id="inc001")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["table"] == "incident"
        assert result["data"]["sys_id"] == "inc001"
        assert result["data"]["deleted"] is True

    @pytest.mark.asyncio()
    async def test_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Returns error in production."""
        from mcp.server.fastmcp import FastMCP

        from servicenow_mcp.tools.developer import register_tools

        mcp = FastMCP("test")
        register_tools(mcp, prod_settings, prod_auth_provider)
        tools = get_tool_functions(mcp)

        raw = await tools["record_delete"](table="incident", sys_id="inc001")
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
        respx.delete(f"{BASE_URL}/api/now/table/incident/missing").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_delete"](table="incident", sys_id="missing")
        result = decode_response(raw)
        assert result["status"] == "error"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_acl_denied_returns_clear_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns clear ACL error on 403."""
        respx.delete(f"{BASE_URL}/api/now/table/incident/inc001").mock(
            return_value=httpx.Response(403, json={"error": {"message": "ACL denied"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_delete"](table="incident", sys_id="inc001")
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
        respx.get(f"{BASE_URL}/api/now/table/incident/inc001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "inc001",
                        "short_description": "Test incident",
                        "state": "1",
                        "password": "s3cret",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_preview_delete"](table="incident", sys_id="inc001")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert "token" in result["data"]
        assert result["data"]["table"] == "incident"
        assert result["data"]["sys_id"] == "inc001"
        assert result["data"]["record_snapshot"]["short_description"] == "Test incident"
        assert result["data"]["record_snapshot"]["password"] == "***MASKED***"

    @pytest.mark.asyncio()
    async def test_blocked_in_prod(self, prod_settings: Settings, prod_auth_provider: BasicAuthProvider) -> None:
        """Returns error in production."""
        from mcp.server.fastmcp import FastMCP

        from servicenow_mcp.tools.developer import register_tools

        mcp = FastMCP("test")
        register_tools(mcp, prod_settings, prod_auth_provider)
        tools = get_tool_functions(mcp)

        raw = await tools["record_preview_delete"](table="incident", sys_id="inc001")
        result = decode_response(raw)
        assert result["status"] == "error"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_not_found_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error when record doesn't exist."""
        respx.get(f"{BASE_URL}/api/now/table/incident/missing").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["record_preview_delete"](table="incident", sys_id="missing")
        result = decode_response(raw)
        assert result["status"] == "error"


# -- record_apply --------------------------------------------------------------


class TestRecordApply:
    """Tests for the record_apply tool (applies any previewed action)."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_apply_create(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Applies a previewed create action."""
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
        respx.get(f"{BASE_URL}/api/now/table/incident/inc001").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": "inc001", "state": "1"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        preview_raw = await tools["record_preview_update"](
            table="incident",
            sys_id="inc001",
            changes=json.dumps({"state": "2"}),
        )
        token = decode_response(preview_raw)["data"]["token"]

        # Phase 2: Apply
        respx.patch(f"{BASE_URL}/api/now/table/incident/inc001").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": "inc001", "state": "2"}},
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
        respx.get(f"{BASE_URL}/api/now/table/incident/inc001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "inc001",
                        "short_description": "To be deleted",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        preview_raw = await tools["record_preview_delete"](table="incident", sys_id="inc001")
        token = decode_response(preview_raw)["data"]["token"]

        # Phase 2: Apply
        respx.delete(f"{BASE_URL}/api/now/table/incident/inc001").mock(return_value=httpx.Response(204))

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
                        "password": "s3cret",
                    }
                },
            )
        )

        raw = await tools["record_apply"](preview_token=token)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["record"]["password"] == "***MASKED***"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_apply_acl_denied(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns clear ACL error when ServiceNow denies the apply operation."""
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

METADATA_URL = f"{BASE_URL}/api/now/table/sys_dictionary"

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
