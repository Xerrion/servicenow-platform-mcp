"""Tests for developer action tools (dev_toggle, dev_set_property, dev_seed_test_data, dev_cleanup, table_preview_update, table_apply_update)."""

import json

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider

BASE_URL = "https://test.service-now.com"


@pytest.fixture
def auth_provider(settings):
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings, auth_provider):
    """Helper: register developer tools on a fresh MCP server and return tool map."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.developer import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}


# ── dev_toggle ────────────────────────────────────────────────────────────


class TestDevToggle:
    """Tests for the dev_toggle tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_toggles_active_field(self, settings, auth_provider):
        """Toggles the active field and returns old/new values."""
        # Mock GET to read current record
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br001",
                        "name": "My Business Rule",
                        "active": "true",
                    }
                },
            )
        )
        # Mock PATCH to update active field
        respx.patch(f"{BASE_URL}/api/now/table/sys_script/br001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br001",
                        "name": "My Business Rule",
                        "active": "false",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["dev_toggle"](artifact_type="business_rule", sys_id="br001", active=False)
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["old_active"] == "true"
        assert result["data"]["new_active"] == "false"

    @pytest.mark.asyncio
    @respx.mock
    async def test_blocked_in_prod(self, prod_settings, auth_provider):
        """Returns error when environment is production."""
        from mcp.server.fastmcp import FastMCP

        from servicenow_mcp.tools.developer import register_tools

        # Create auth from prod_settings
        prod_auth = BasicAuthProvider(prod_settings)
        mcp = FastMCP("test")
        register_tools(mcp, prod_settings, prod_auth)
        tools = {t.name: t.fn for t in mcp._tool_manager._tools.values()}

        raw = await tools["dev_toggle"](artifact_type="business_rule", sys_id="br001", active=False)
        result = json.loads(raw)

        assert result["status"] == "error"

    @pytest.mark.asyncio
    @respx.mock
    async def test_unknown_artifact_type(self, settings, auth_provider):
        """Returns error for unknown artifact type."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["dev_toggle"](artifact_type="unknown_type", sys_id="br001", active=False)
        result = json.loads(raw)

        assert result["status"] == "error"


# ── dev_set_property ──────────────────────────────────────────────────────


class TestDevSetProperty:
    """Tests for the dev_set_property tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_updates_property_value(self, settings, auth_provider):
        """Updates a system property and returns old value."""
        # Mock query to find the property
        respx.get(f"{BASE_URL}/api/now/table/sys_properties").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "prop001",
                            "name": "glide.ui.session_timeout",
                            "value": "30",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        # Mock PATCH to update value
        respx.patch(f"{BASE_URL}/api/now/table/sys_properties/prop001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "prop001",
                        "name": "glide.ui.session_timeout",
                        "value": "60",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["dev_set_property"](name="glide.ui.session_timeout", value="60")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["old_value"] == "30"
        assert result["data"]["new_value"] == "60"

    @pytest.mark.asyncio
    @respx.mock
    async def test_blocked_in_prod(self, prod_settings):
        """Returns error when environment is production."""
        from mcp.server.fastmcp import FastMCP

        from servicenow_mcp.tools.developer import register_tools

        prod_auth = BasicAuthProvider(prod_settings)
        mcp = FastMCP("test")
        register_tools(mcp, prod_settings, prod_auth)
        tools = {t.name: t.fn for t in mcp._tool_manager._tools.values()}

        raw = await tools["dev_set_property"](name="glide.ui.session_timeout", value="60")
        result = json.loads(raw)

        assert result["status"] == "error"

    @pytest.mark.asyncio
    @respx.mock
    async def test_property_not_found(self, settings, auth_provider):
        """Returns error when property doesn't exist."""
        respx.get(f"{BASE_URL}/api/now/table/sys_properties").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["dev_set_property"](name="nonexistent.property", value="x")
        result = json.loads(raw)

        assert result["status"] == "error"


# ── dev_seed_test_data ────────────────────────────────────────────────────


class TestDevSeedTestData:
    """Tests for the dev_seed_test_data tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_inserts_records_and_returns_sys_ids(self, settings, auth_provider):
        """Creates records and returns the created sys_ids with a seed tag."""
        # Mock POST for each record creation
        respx.post(f"{BASE_URL}/api/now/table/incident").mock(
            side_effect=[
                httpx.Response(
                    201,
                    json={"result": {"sys_id": "new1", "number": "INC0099001"}},
                ),
                httpx.Response(
                    201,
                    json={"result": {"sys_id": "new2", "number": "INC0099002"}},
                ),
            ]
        )

        tools = _register_and_get_tools(settings, auth_provider)
        records_json = json.dumps(
            [
                {"short_description": "Test 1", "urgency": "3"},
                {"short_description": "Test 2", "urgency": "3"},
            ]
        )
        raw = await tools["dev_seed_test_data"](table="incident", records=records_json)
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["created_count"] == 2
        assert "new1" in result["data"]["sys_ids"]
        assert "new2" in result["data"]["sys_ids"]
        assert "tag" in result["data"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_blocked_in_prod(self, prod_settings):
        """Returns error when environment is production."""
        from mcp.server.fastmcp import FastMCP

        from servicenow_mcp.tools.developer import register_tools

        prod_auth = BasicAuthProvider(prod_settings)
        mcp = FastMCP("test")
        register_tools(mcp, prod_settings, prod_auth)
        tools = {t.name: t.fn for t in mcp._tool_manager._tools.values()}

        raw = await tools["dev_seed_test_data"](
            table="incident",
            records=json.dumps([{"short_description": "Test"}]),
        )
        result = json.loads(raw)

        assert result["status"] == "error"


# ── dev_cleanup ───────────────────────────────────────────────────────────


class TestDevCleanup:
    """Tests for the dev_cleanup tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_deletes_tracked_records(self, settings, auth_provider):
        """Deletes records previously seeded and returns summary."""
        # First seed some records to create the tracking state
        respx.post(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                201,
                json={"result": {"sys_id": "new1", "number": "INC0099001"}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)

        # Seed first
        seed_raw = await tools["dev_seed_test_data"](
            table="incident",
            records=json.dumps([{"short_description": "To cleanup"}]),
            tag="cleanup-test-tag",
        )
        seed_result = json.loads(seed_raw)
        assert seed_result["status"] == "success"

        # Now mock DELETE
        respx.delete(f"{BASE_URL}/api/now/table/incident/new1").mock(return_value=httpx.Response(204))

        # Cleanup
        raw = await tools["dev_cleanup"](tag="cleanup-test-tag")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["deleted_count"] == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_unknown_tag_returns_error(self, settings, auth_provider):
        """Returns error for a tag that was never seeded."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["dev_cleanup"](tag="nonexistent-tag")
        result = json.loads(raw)

        assert result["status"] == "error"


# ── table_preview_update ──────────────────────────────────────────────────


class TestTablePreviewUpdate:
    """Tests for the table_preview_update tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_diff_and_token(self, settings, auth_provider):
        """Returns a field-level diff and a preview token."""
        # Mock GET for current record
        respx.get(f"{BASE_URL}/api/now/table/incident/inc001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "inc001",
                        "state": "1",
                        "urgency": "2",
                        "short_description": "Original",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        changes_json = json.dumps({"state": "2", "short_description": "Updated"})
        raw = await tools["table_preview_update"](table="incident", sys_id="inc001", changes=changes_json)
        result = json.loads(raw)

        assert result["status"] == "success"
        assert "token" in result["data"]
        diff = result["data"]["diff"]
        assert "state" in diff
        assert diff["state"]["old"] == "1"
        assert diff["state"]["new"] == "2"

    @pytest.mark.asyncio
    @respx.mock
    async def test_blocked_in_prod(self, prod_settings):
        """Returns error when environment is production."""
        from mcp.server.fastmcp import FastMCP

        from servicenow_mcp.tools.developer import register_tools

        prod_auth = BasicAuthProvider(prod_settings)
        mcp = FastMCP("test")
        register_tools(mcp, prod_settings, prod_auth)
        tools = {t.name: t.fn for t in mcp._tool_manager._tools.values()}

        raw = await tools["table_preview_update"](
            table="incident",
            sys_id="inc001",
            changes=json.dumps({"state": "2"}),
        )
        result = json.loads(raw)

        assert result["status"] == "error"


# ── table_apply_update ────────────────────────────────────────────────────


class TestTableApplyUpdate:
    """Tests for the table_apply_update tool."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_applies_update_with_valid_token(self, settings, auth_provider):
        """Applies the update using a valid preview token."""
        # Step 1: Create a preview
        respx.get(f"{BASE_URL}/api/now/table/incident/inc001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "inc001",
                        "state": "1",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        preview_raw = await tools["table_preview_update"](
            table="incident",
            sys_id="inc001",
            changes=json.dumps({"state": "2"}),
        )
        preview_result = json.loads(preview_raw)
        token = preview_result["data"]["token"]

        # Step 2: Apply with the token
        respx.patch(f"{BASE_URL}/api/now/table/incident/inc001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "inc001",
                        "state": "2",
                    }
                },
            )
        )

        raw = await tools["table_apply_update"](preview_token=token)
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["record"]["state"] == "2"

    @pytest.mark.asyncio
    @respx.mock
    async def test_rejects_invalid_token(self, settings, auth_provider):
        """Returns error for an invalid/unknown token."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_apply_update"](preview_token="invalid-token")
        result = json.loads(raw)

        assert result["status"] == "error"


# ── Error handling ────────────────────────────────────────────────────────


class TestDeveloperErrorHandling:
    """Tests for partial-failure handling in seed and cleanup operations."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_seed_partial_failure(self, settings, auth_provider):
        """One record succeeds, one fails — response reflects partial success."""
        respx.post(f"{BASE_URL}/api/now/table/incident").mock(
            side_effect=[
                httpx.Response(
                    201,
                    json={"result": {"sys_id": "new1", "number": "INC0099001"}},
                ),
                httpx.Response(500, json={"error": {"message": "Internal error"}}),
            ]
        )

        tools = _register_and_get_tools(settings, auth_provider)
        records_json = json.dumps(
            [
                {"short_description": "Good record"},
                {"short_description": "Bad record"},
            ]
        )
        raw = await tools["dev_seed_test_data"](table="incident", records=records_json, tag="partial-seed")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["created_count"] == 1
        assert result["data"]["failed_count"] == 1
        assert "new1" in result["data"]["sys_ids"]
        assert len(result["data"]["sys_ids"]) == 1

        # Verify only the successful record is tracked for cleanup
        # by attempting a cleanup — the tag should exist with 1 record
        respx.delete(f"{BASE_URL}/api/now/table/incident/new1").mock(return_value=httpx.Response(204))

        raw = await tools["dev_cleanup"](tag="partial-seed")
        cleanup_result = json.loads(raw)

        assert cleanup_result["status"] == "success"
        assert cleanup_result["data"]["deleted_count"] == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_cleanup_partial_failure(self, settings, auth_provider):
        """One delete succeeds, one fails — tag remains in tracker with failed record."""
        # Seed two records first
        respx.post(f"{BASE_URL}/api/now/table/incident").mock(
            side_effect=[
                httpx.Response(
                    201,
                    json={"result": {"sys_id": "del1", "number": "INC0099010"}},
                ),
                httpx.Response(
                    201,
                    json={"result": {"sys_id": "del2", "number": "INC0099011"}},
                ),
            ]
        )

        tools = _register_and_get_tools(settings, auth_provider)
        records_json = json.dumps(
            [
                {"short_description": "Will delete"},
                {"short_description": "Will fail delete"},
            ]
        )
        seed_raw = await tools["dev_seed_test_data"](table="incident", records=records_json, tag="partial-cleanup")
        seed_result = json.loads(seed_raw)
        assert seed_result["status"] == "success"
        assert seed_result["data"]["created_count"] == 2

        # Mock deletions: first succeeds, second fails
        respx.delete(f"{BASE_URL}/api/now/table/incident/del1").mock(return_value=httpx.Response(204))
        respx.delete(f"{BASE_URL}/api/now/table/incident/del2").mock(return_value=httpx.Response(500))

        raw = await tools["dev_cleanup"](tag="partial-cleanup")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["deleted_count"] == 1
        assert result["data"]["failed_count"] == 1

        # Tag should still exist in tracker with the failed record
        # Attempting cleanup again should find the tag
        respx.delete(f"{BASE_URL}/api/now/table/incident/del2").mock(return_value=httpx.Response(204))

        retry_raw = await tools["dev_cleanup"](tag="partial-cleanup")
        retry_result = json.loads(retry_raw)
        assert retry_result["status"] == "success"
        assert retry_result["data"]["deleted_count"] == 1


# ── Security: write gating & table access ────────────────────────────────


class TestDevCleanupWriteGate:
    """Tests for write-gate enforcement on dev_cleanup."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_dev_cleanup_blocked_in_production(self, prod_settings):
        """Cleanup returns error when environment is production."""
        from unittest.mock import patch as mock_patch

        from mcp.server.fastmcp import FastMCP

        from servicenow_mcp.tools.developer import register_tools

        prod_auth = BasicAuthProvider(prod_settings)
        mcp = FastMCP("test")
        register_tools(mcp, prod_settings, prod_auth)
        tools = {t.name: t.fn for t in mcp._tool_manager._tools.values()}

        # Patch SeededRecordTracker.get to return a fake entry so the tag lookup succeeds
        with mock_patch(
            "servicenow_mcp.state.SeededRecordTracker.get",
            return_value=[{"table": "incident", "sys_ids": ["rec1"]}],
        ):
            raw = await tools["dev_cleanup"](tag="test-tag")

        result = json.loads(raw)
        assert result["status"] == "error"
        assert "blocked" in result["error"].lower() or "production" in result["error"].lower()


class TestTableApplyUpdateSecurity:
    """Tests for write-gate and table-access enforcement on table_apply_update."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_table_apply_update_blocked_in_production(self, prod_settings):
        """Apply returns error when environment is production."""
        from unittest.mock import patch as mock_patch

        from mcp.server.fastmcp import FastMCP

        from servicenow_mcp.tools.developer import register_tools

        prod_auth = BasicAuthProvider(prod_settings)
        mcp = FastMCP("test")
        register_tools(mcp, prod_settings, prod_auth)
        tools = {t.name: t.fn for t in mcp._tool_manager._tools.values()}

        # Patch the preview store to return a valid payload
        with mock_patch(
            "servicenow_mcp.state.PreviewTokenStore.consume",
            return_value={"table": "incident", "sys_id": "inc001", "changes": {"state": "2"}},
        ):
            raw = await tools["table_apply_update"](preview_token="fake-token")

        result = json.loads(raw)
        assert result["status"] == "error"
        assert "blocked" in result["error"].lower() or "production" in result["error"].lower()

    @pytest.mark.asyncio
    @respx.mock
    async def test_table_apply_update_denied_table(self, settings, auth_provider):
        """Apply returns error when token references a denied table."""
        from unittest.mock import patch as mock_patch

        tools = _register_and_get_tools(settings, auth_provider)

        # Patch the preview store to return a payload with a denied table
        with mock_patch(
            "servicenow_mcp.state.PreviewTokenStore.consume",
            return_value={"table": "sys_user_has_password", "sys_id": "x", "changes": {"value": "y"}},
        ):
            raw = await tools["table_apply_update"](preview_token="fake-token")

        result = json.loads(raw)
        assert result["status"] == "error"
        assert "denied" in result["error"].lower()


class TestTablePreviewUpdateSecurity:
    """Tests for table-access enforcement on table_preview_update."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_table_preview_update_denied_table(self, settings, auth_provider):
        """Preview returns error when table is on the deny list."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["table_preview_update"](
            table="sys_user_has_password",
            sys_id="x",
            changes=json.dumps({"value": "y"}),
        )
        result = json.loads(raw)
        assert result["status"] == "error"
        assert "denied" in result["error"].lower()


class TestDevSeedTestDataSecurity:
    """Tests for table-access enforcement on dev_seed_test_data."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_dev_seed_test_data_denied_table(self, settings, auth_provider):
        """Seed returns error when table is on the deny list."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["dev_seed_test_data"](
            table="sys_user_has_password",
            records=json.dumps([{"value": "test"}]),
        )
        result = json.loads(raw)
        assert result["status"] == "error"
        assert "denied" in result["error"].lower()


# ── Security: sensitive field masking ─────────────────────────────────────


class TestSensitiveFieldMasking:
    """Tests for sensitive field masking in developer tool responses."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_table_apply_update_masks_sensitive_fields(self, settings, auth_provider):
        """Apply update masks sensitive fields like 'password' in the returned record."""
        # Step 1: Create a preview
        respx.get(f"{BASE_URL}/api/now/table/sys_user/u001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "u001",
                        "user_name": "admin",
                        "password": "old_secret",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        preview_raw = await tools["table_preview_update"](
            table="sys_user",
            sys_id="u001",
            changes=json.dumps({"user_name": "new_admin"}),
        )
        preview_result = json.loads(preview_raw)
        token = preview_result["data"]["token"]

        # Step 2: Apply — response includes a password field
        respx.patch(f"{BASE_URL}/api/now/table/sys_user/u001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "u001",
                        "user_name": "new_admin",
                        "password": "still_secret",
                    }
                },
            )
        )

        raw = await tools["table_apply_update"](preview_token=token)
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["record"]["user_name"] == "new_admin"
        assert result["data"]["record"]["password"] == "***MASKED***"

    @pytest.mark.asyncio
    @respx.mock
    async def test_dev_set_property_masks_sensitive_value(self, settings, auth_provider):
        """Setting a property with a sensitive name masks old/new values in response."""
        # Mock query to find the property
        respx.get(f"{BASE_URL}/api/now/table/sys_properties").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "prop002",
                            "name": "my.api_key_token",
                            "value": "old_secret_key",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        # Mock PATCH to update value
        respx.patch(f"{BASE_URL}/api/now/table/sys_properties/prop002").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "prop002",
                        "name": "my.api_key_token",
                        "value": "new_secret_key",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["dev_set_property"](name="my.api_key_token", value="new_secret_key")
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["old_value"] == "***MASKED***"
        assert result["data"]["new_value"] == "***MASKED***"
        # Name should still be visible
        assert result["data"]["name"] == "my.api_key_token"


# ── Coverage: generic exception handlers ──────────────────────────────────


class TestDevToggleGenericException:
    """Tests for dev_toggle generic exception handler (lines 82-83)."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_unexpected_exception(self, settings, auth_provider):
        """dev_toggle returns error envelope when an unexpected exception occurs."""
        from unittest.mock import AsyncMock, patch

        tools = _register_and_get_tools(settings, auth_provider)
        with patch(
            "servicenow_mcp.tools.developer.ServiceNowClient.__aenter__",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection failed"),
        ):
            raw = await tools["dev_toggle"](artifact_type="business_rule", sys_id="br001", active=False)
        result = json.loads(raw)
        assert result["status"] == "error"
        assert "connection failed" in result["error"]


class TestDevSetPropertyGenericException:
    """Tests for dev_set_property generic exception handler (lines 154-155)."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_unexpected_exception(self, settings, auth_provider):
        """dev_set_property returns error envelope when an unexpected exception occurs."""
        from unittest.mock import AsyncMock, patch

        tools = _register_and_get_tools(settings, auth_provider)
        with patch(
            "servicenow_mcp.tools.developer.ServiceNowClient.__aenter__",
            new_callable=AsyncMock,
            side_effect=RuntimeError("network failure"),
        ):
            raw = await tools["dev_set_property"](name="glide.ui.session_timeout", value="60")
        result = json.loads(raw)
        assert result["status"] == "error"
        assert "network failure" in result["error"]


class TestDevCleanupGenericException:
    """Tests for dev_cleanup generic exception handler (lines 322-323)."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_unexpected_exception(self, settings, auth_provider):
        """dev_cleanup returns error envelope when an unexpected exception occurs."""
        from unittest.mock import patch

        tools = _register_and_get_tools(settings, auth_provider)

        # Patch SeededRecordTracker.get to raise an unexpected exception
        with patch(
            "servicenow_mcp.state.SeededRecordTracker.get",
            side_effect=RuntimeError("tracker exploded"),
        ):
            raw = await tools["dev_cleanup"](tag="test-tag")

        result = json.loads(raw)
        assert result["status"] == "error"
        assert "tracker exploded" in result["error"]


class TestTablePreviewUpdateSensitiveField:
    """Tests for table_preview_update with sensitive field (line 368)."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_sensitive_field_masked_in_diff(self, settings, auth_provider):
        """Diff masks both old and new values for sensitive fields like 'password'."""
        respx.get(f"{BASE_URL}/api/now/table/sys_user/u001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "u001",
                        "user_name": "admin",
                        "password": "old_secret",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        changes_json = json.dumps({"password": "new_secret"})
        raw = await tools["table_preview_update"](table="sys_user", sys_id="u001", changes=changes_json)
        result = json.loads(raw)

        assert result["status"] == "success"
        diff = result["data"]["diff"]
        assert "password" in diff
        assert diff["password"]["old"] == "***MASKED***"
        assert diff["password"]["new"] == "***MASKED***"
