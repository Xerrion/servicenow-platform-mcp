"""Tests for change intelligence tools (changes_updateset_inspect, changes_diff_artifact, changes_last_touched, changes_release_notes)."""

from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings
from tests.helpers import decode_response, get_tool_functions


BASE_URL = "https://test.service-now.com"


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings: Settings, auth_provider: BasicAuthProvider) -> dict[str, Any]:
    """Helper: register change tools on a fresh MCP server and return tool map."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.changes import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return get_tool_functions(mcp)


class TestChangesUpdatesetInspect:
    """Tests for the changes_updateset_inspect tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_grouped_summary(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns update set members grouped by table/type."""
        # Mock the update set record fetch
        respx.get(f"{BASE_URL}/api/now/table/sys_update_set/us001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "us001",
                        "name": "My Update Set",
                        "state": "in progress",
                        "application": "Global",
                    }
                },
            )
        )
        # Mock the update set members query
        respx.get(f"{BASE_URL}/api/now/table/sys_update_xml").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "m1",
                            "name": "sys_script_include_abc",
                            "type": "sys_script_include",
                            "action": "INSERT_OR_UPDATE",
                            "target_name": "MyUtil",
                            "update_set": "us001",
                        },
                        {
                            "sys_id": "m2",
                            "name": "sys_script_def",
                            "type": "sys_script",
                            "action": "INSERT_OR_UPDATE",
                            "target_name": "BR: Validate incident",
                            "update_set": "us001",
                        },
                        {
                            "sys_id": "m3",
                            "name": "sys_ui_policy_ghi",
                            "type": "sys_ui_policy",
                            "action": "INSERT_OR_UPDATE",
                            "target_name": "Hide fields when resolved",
                            "update_set": "us001",
                        },
                    ]
                },
                headers={"X-Total-Count": "3"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["changes_updateset_inspect"](update_set_id="us001")
        result = decode_response(raw)

        assert result["status"] == "success"
        data = result["data"]
        assert data["update_set"]["sys_id"] == "us001"
        assert data["total_members"] == 3
        # Should be grouped by type
        assert "groups" in data
        assert len(data["groups"]) == 3

    @pytest.mark.asyncio()
    @respx.mock
    async def test_flags_risk_indicators(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Flags risk when dangerous artifact types are present (ACLs, scripts)."""
        respx.get(f"{BASE_URL}/api/now/table/sys_update_set/us002").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "us002",
                        "name": "Risky Changes",
                        "state": "in progress",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_update_xml").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "m1",
                            "name": "sys_security_acl_abc",
                            "type": "sys_security_acl",
                            "action": "INSERT_OR_UPDATE",
                            "target_name": "ACL: incident.write",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["changes_updateset_inspect"](update_set_id="us002")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert len(result["data"]["risk_flags"]) > 0

    @pytest.mark.asyncio()
    @respx.mock
    async def test_empty_update_set(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Handles update set with no members."""
        respx.get(f"{BASE_URL}/api/now/table/sys_update_set/us003").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "us003",
                        "name": "Empty",
                        "state": "in progress",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_update_xml").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["changes_updateset_inspect"](update_set_id="us003")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["total_members"] == 0

    @pytest.mark.asyncio()
    @respx.mock
    async def test_rejects_invalid_update_set_id(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error when update_set_id contains invalid characters."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["changes_updateset_inspect"](update_set_id="invalid;id")
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "invalid identifier" in result["error"]["message"].lower()


class TestChangesDiffArtifact:
    """Tests for the changes_diff_artifact tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_diff_between_versions(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns a text diff between the two most recent versions."""
        route = respx.get(f"{BASE_URL}/api/now/table/sys_update_version").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "v2",
                            "name": "sys_script_include_abc",
                            "payload": '<record><sys_script_include action="INSERT_OR_UPDATE"><script>function hello() { return "world"; }</script></sys_script_include></record>',
                            "sys_recorded_at": "2026-02-20 10:00:00",
                        },
                        {
                            "sys_id": "v1",
                            "name": "sys_script_include_abc",
                            "payload": '<record><sys_script_include action="INSERT_OR_UPDATE"><script>function hello() { return "hello"; }</script></sys_script_include></record>',
                            "sys_recorded_at": "2026-02-19 10:00:00",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["changes_diff_artifact"](table="sys_script_include", sys_id="abc")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert "diff" in result["data"]

        # Verify the query includes inline ordering via ^ORDERBY
        assert route.calls.last is not None
        request = route.calls.last.request
        url_str = str(request.url)
        assert "ORDERBYDESCsys_recorded_at" in url_str

        # Verify that order_by is NOT sent as a separate sysparm_orderby parameter
        parsed = urlparse(url_str)
        qs = parse_qs(parsed.query)
        assert "sysparm_orderby" not in qs

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_error_when_no_versions(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error when fewer than 2 versions exist."""
        respx.get(f"{BASE_URL}/api/now/table/sys_update_version").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["changes_diff_artifact"](table="sys_script_include", sys_id="abc")
        result = decode_response(raw)

        assert result["status"] == "error"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_diff_version_ordering(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Verifies that versions[1] (older) is treated as the base 'Old' in the diff output."""
        old_payload = '<record><sys_script_include action="INSERT_OR_UPDATE"><script>// old version</script></sys_script_include></record>'
        new_payload = '<record><sys_script_include action="INSERT_OR_UPDATE"><script>// new version</script></sys_script_include></record>'

        # DESC order: newest first, oldest second
        respx.get(f"{BASE_URL}/api/now/table/sys_update_version").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "v2",
                            "name": "sys_script_include_xyz",
                            "payload": new_payload,
                            "sys_recorded_at": "2026-02-21 10:00:00",
                        },
                        {
                            "sys_id": "v1",
                            "name": "sys_script_include_xyz",
                            "payload": old_payload,
                            "sys_recorded_at": "2026-02-20 10:00:00",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["changes_diff_artifact"](table="sys_script_include", sys_id="xyz")
        result = decode_response(raw)

        assert result["status"] == "success"
        diff_text = result["data"]["diff"]
        # The diff should show the old version (versions[1]) as the "from" file
        assert result["data"]["old_version"] == "2026-02-20 10:00:00"
        assert result["data"]["new_version"] == "2026-02-21 10:00:00"
        # The diff should contain the removed old content and added new content
        assert "// old version" in diff_text
        assert "// new version" in diff_text

    @pytest.mark.asyncio()
    @respx.mock
    async def test_diff_artifact_uses_builder_order_by(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Verifies the query uses ServiceNowQuery builder with inline ORDERBYDESC."""
        route = respx.get(f"{BASE_URL}/api/now/table/sys_update_version").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "v2",
                            "name": "sys_script_include_abc",
                            "payload": "<new/>",
                            "sys_recorded_at": "2026-02-21 10:00:00",
                        },
                        {
                            "sys_id": "v1",
                            "name": "sys_script_include_abc",
                            "payload": "<old/>",
                            "sys_recorded_at": "2026-02-20 10:00:00",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["changes_diff_artifact"](table="sys_script_include", sys_id="abc")
        result = decode_response(raw)

        assert result["status"] == "success"

        # Verify the query uses the builder-generated format with equals() + order_by()
        assert route.calls.last is not None
        request = route.calls.last.request
        url_str = str(request.url)

        # Builder produces: name=sys_script_include_abc^ORDERBYDESCsys_recorded_at
        assert "name%3Dsys_script_include_abc" in url_str or "name=sys_script_include_abc" in url_str
        assert "ORDERBYDESCsys_recorded_at" in url_str

        # Verify that order_by is NOT sent as a separate sysparm_orderby parameter
        parsed = urlparse(url_str)
        qs = parse_qs(parsed.query)
        assert "sysparm_orderby" not in qs

    @pytest.mark.asyncio()
    @respx.mock
    async def test_caret_in_sys_id_single_sanitized(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """sys_id with carets produces single-sanitized update_name (^ → ^^), not double (^ → ^^^^)."""
        route = respx.get(f"{BASE_URL}/api/now/table/sys_update_version").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "v2",
                            "name": "sys_script_include_abc^^def",
                            "payload": "<new/>",
                            "sys_recorded_at": "2026-02-21 10:00:00",
                        },
                        {
                            "sys_id": "v1",
                            "name": "sys_script_include_abc^^def",
                            "payload": "<old/>",
                            "sys_recorded_at": "2026-02-20 10:00:00",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["changes_diff_artifact"](table="sys_script_include", sys_id="abc^def")
        result = decode_response(raw)

        assert result["status"] == "success"
        # The update_name should be "sys_script_include_abc^def" (unsanitized),
        # and the builder's .equals() will sanitize ^ to ^^ in the query.
        assert route.calls.last is not None
        request = route.calls.last.request
        parsed = urlparse(str(request.url))
        qs = parse_qs(parsed.query)
        query_str = qs["sysparm_query"][0]
        # Single sanitization: the query value should contain abc^^def
        assert "abc^^def" in query_str
        # Double sanitization would produce abc^^^^def
        assert "abc^^^^def" not in query_str


class TestChangesLastTouched:
    """Tests for the changes_last_touched tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_audit_history(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns who/when/what from sys_audit."""
        respx.get(f"{BASE_URL}/api/now/table/sys_audit").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "a1",
                            "user": "admin",
                            "fieldname": "state",
                            "oldvalue": "1",
                            "newvalue": "2",
                            "sys_created_on": "2026-02-20 09:00:00",
                            "documentkey": "inc001",
                        },
                        {
                            "sys_id": "a2",
                            "user": "admin",
                            "fieldname": "assigned_to",
                            "oldvalue": "",
                            "newvalue": "John Doe",
                            "sys_created_on": "2026-02-20 08:30:00",
                            "documentkey": "inc001",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["changes_last_touched"](table="incident", sys_id="inc001")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert len(result["data"]["changes"]) == 2
        assert result["data"]["changes"][0]["user"] == "admin"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_empty_for_no_audit(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns success with empty changes when no audit trail exists."""
        respx.get(f"{BASE_URL}/api/now/table/sys_audit").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["changes_last_touched"](table="incident", sys_id="inc999")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["changes"] == []


class TestChangesReleaseNotes:
    """Tests for the changes_release_notes tool."""

    @staticmethod
    def _mock_release_notes_requests(update_set_id: str) -> None:
        """Mock the update set and member requests for release notes tests."""
        respx.get(f"{BASE_URL}/api/now/table/sys_update_set/{update_set_id}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": update_set_id,
                        "name": "Sprint 42 Release",
                        "state": "complete",
                        "description": "Bug fixes and improvements",
                        "sys_created_by": "admin",
                        "sys_updated_on": "2026-02-20 12:00:00",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_update_xml").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "m1",
                            "type": "sys_script_include",
                            "action": "INSERT_OR_UPDATE",
                            "target_name": "IncidentUtils",
                        },
                        {
                            "sys_id": "m2",
                            "type": "sys_script",
                            "action": "INSERT_OR_UPDATE",
                            "target_name": "BR: Auto-assign",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

    @pytest.mark.asyncio()
    @respx.mock
    async def test_generates_markdown_release_notes(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Generates Markdown release notes from update set."""
        self._mock_release_notes_requests("us001")

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["changes_release_notes"](update_set_id="us001")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["format"] == "markdown"
        notes = result["data"]["release_notes"]
        assert "Sprint 42 Release" in notes
        assert "IncidentUtils" in notes

    @pytest.mark.asyncio()
    @pytest.mark.parametrize("format_value", ["markdown", "Markdown", " MARKDOWN ", "md", "MD", "html"])
    @respx.mock
    async def test_normalizes_release_note_format_variants(
        self,
        settings: Settings,
        auth_provider: BasicAuthProvider,
        format_value: str,
    ) -> None:
        """Accepts markdown variants and preserves legacy fallback-to-markdown behavior."""
        self._mock_release_notes_requests("us005")

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["changes_release_notes"](update_set_id="us005", format=format_value)
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["format"] == "markdown"
        assert "Sprint 42 Release" in result["data"]["release_notes"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_handles_empty_update_set(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Generates notes even for empty update set."""
        respx.get(f"{BASE_URL}/api/now/table/sys_update_set/us004").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "us004",
                        "name": "Empty Release",
                        "state": "in progress",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_update_xml").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["changes_release_notes"](update_set_id="us004")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert "Empty Release" in result["data"]["release_notes"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_rejects_invalid_update_set_id(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error when update_set_id contains invalid characters."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["changes_release_notes"](update_set_id="invalid;id")
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "invalid identifier" in result["error"]["message"].lower()
