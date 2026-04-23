"""Tests for documentation tools (docs_logic_map, docs_artifact_summary, docs_test_scenarios, docs_review_notes)."""

from typing import Any

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
    """Helper: register documentation tools on a fresh MCP server and return tool map."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.documentation import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return get_tool_functions(mcp)


# ── docs_logic_map ────────────────────────────────────────────────────────


class TestDocsLogicMap:
    """Tests for the docs_logic_map tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_lifecycle_map(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns automation grouped by lifecycle phase."""
        # Business rules
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "8baeabad365de895ab58ec0d6dd2c1e2",
                            "name": "Set defaults",
                            "when": "before",
                            "action_insert": "true",
                            "action_update": "false",
                            "action_delete": "false",
                            "active": "true",
                        },
                        {
                            "sys_id": "45caf3e7f1e7ce17456aec79ce1ab853",
                            "name": "Send notification",
                            "when": "after",
                            "action_insert": "false",
                            "action_update": "true",
                            "action_delete": "false",
                            "active": "true",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )
        # Client scripts
        respx.get(f"{BASE_URL}/api/now/table/sys_script_client").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "f9ce352c1d3e957f5b6330307d618b98",
                            "name": "Validate priority",
                            "type": "onChange",
                            "active": "true",
                        }
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        # UI policies
        respx.get(f"{BASE_URL}/api/now/table/sys_ui_policy").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        # UI actions
        respx.get(f"{BASE_URL}/api/now/table/sys_ui_action").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_logic_map"](table="incident")
        result = decode_response(raw)

        assert result["status"] == "success"
        data = result["data"]
        assert data["table"] == "incident"
        assert "phases" in data
        # Should have at least before_insert and after_update
        assert len(data["phases"]) > 0

    @pytest.mark.asyncio()
    @respx.mock
    async def test_empty_table_no_automation(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Table with no automation returns empty phases."""
        for table in [
            "sys_script",
            "sys_script_client",
            "sys_ui_policy",
            "sys_ui_action",
        ]:
            respx.get(f"{BASE_URL}/api/now/table/{table}").mock(
                return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
            )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_logic_map"](table="custom_table")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["total_automations"] == 0

    @pytest.mark.asyncio()
    @respx.mock
    async def test_br_delete_action_and_no_operations(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Covers delete action branch and fallback to 'all' when no operations are set."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "61bf664b40fbd88d122defa600c69f14",
                            "name": "On delete",
                            "when": "before",
                            "action_insert": "false",
                            "action_update": "false",
                            "action_delete": "true",
                            "active": "true",
                        },
                        {
                            "sys_id": "3d94aec2ee47e55f945e280c4f258d94",
                            "name": "On all",
                            "when": "before",
                            "action_insert": "false",
                            "action_update": "false",
                            "action_delete": "false",
                            "active": "true",
                        },
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_script_client").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_ui_policy").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_ui_action").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_logic_map"](table="incident")
        result = decode_response(raw)

        assert result["status"] == "success"
        phases = result["data"]["phases"]
        assert "before_delete" in phases
        assert phases["before_delete"][0]["name"] == "On delete"
        assert "before_all" in phases
        assert phases["before_all"][0]["name"] == "On all"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_ui_policies_and_ui_actions_populated(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Covers non-empty UI policies and UI actions phases."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_script_client").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_ui_policy").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "a1151e6d2642a62fbecdab41e26d6bb2",
                            "short_description": "Make field read-only",
                            "active": "true",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_ui_action").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "d8ebd7fd78247f151e5b789805832857",
                            "name": "Close Incident",
                            "action_name": "close",
                            "active": "true",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_logic_map"](table="incident")
        result = decode_response(raw)

        assert result["status"] == "success"
        phases = result["data"]["phases"]
        assert "ui_policy" in phases
        assert phases["ui_policy"][0]["name"] == "Make field read-only"
        assert "ui_action" in phases
        assert phases["ui_action"][0]["name"] == "Close Incident"
        assert result["data"]["total_automations"] == 2


# ── docs_artifact_summary ─────────────────────────────────────────────────


class TestDocsArtifactSummary:
    """Tests for the docs_artifact_summary tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_summary_with_dependencies(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Returns artifact summary with referenced tables and referenced_by."""
        # Get the artifact
        respx.get(f"{BASE_URL}/api/now/table/sys_script/5f94cbf2a18c848c38da0c789d5da01b").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "5f94cbf2a18c848c38da0c789d5da01b",
                        "name": "Update CI",
                        "script": "var gr = new GlideRecord('cmdb_ci'); gr.addQuery('name', current.ci); gr.query();",
                        "collection": "incident",
                        "active": "true",
                    }
                },
            )
        )
        # Code search for what references this artifact
        respx.get(f"{BASE_URL}/api/sn_codesearch/code_search/search").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"search_results": []}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_artifact_summary"](
            artifact_type="business_rule", sys_id="5f94cbf2a18c848c38da0c789d5da01b"
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        data = result["data"]
        assert data["artifact"]["name"] == "Update CI"
        # Should detect GlideRecord('cmdb_ci') in script
        assert "cmdb_ci" in data["referenced_tables"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_not_found_returns_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns error for non-existent artifact."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/35a1af58a3b00bde3d8af97f82562ac2").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_artifact_summary"](
            artifact_type="business_rule", sys_id="35a1af58a3b00bde3d8af97f82562ac2"
        )
        result = decode_response(raw)

        assert result["status"] == "error"

    @pytest.mark.asyncio()
    async def test_invalid_artifact_type_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Unknown artifact_type returns an error with valid types listed."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_artifact_summary"](
            artifact_type="bogus_type", sys_id="6367c48dd193d56ea7b0baad25b19455"
        )
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "bogus_type" in result["error"]["message"]
        assert "Valid:" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_code_search_failure_is_silent(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Code search exception is caught silently; referenced_by is empty."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/2edaa2dc068427312415c976a18155dd").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "2edaa2dc068427312415c976a18155dd",
                        "name": "SearchFail BR",
                        "script": "var gr = new GlideRecord('task'); gr.query();",
                        "collection": "incident",
                        "active": "true",
                    }
                },
            )
        )
        # Code search returns a server error
        respx.get(f"{BASE_URL}/api/sn_codesearch/code_search/search").mock(
            return_value=httpx.Response(500, json={"error": {"message": "Internal error"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_artifact_summary"](
            artifact_type="business_rule", sys_id="2edaa2dc068427312415c976a18155dd"
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["referenced_by"] == []
        assert "task" in result["data"]["referenced_tables"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_masks_sensitive_fields(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Sensitive fields in the artifact record are masked in the response."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/a4735c7a88ec47eec3fba55319b9df81").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "a4735c7a88ec47eec3fba55319b9df81",
                        "name": "Sensitive BR",
                        "script": "gs.log('hello');",
                        "collection": "incident",
                        "active": "true",
                        "password": "super_secret_123",
                        "auth_token": "tok_abc",
                    }
                },
            )
        )
        respx.get(f"{BASE_URL}/api/sn_codesearch/code_search/search").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"search_results": []}},
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_artifact_summary"](
            artifact_type="business_rule", sys_id="a4735c7a88ec47eec3fba55319b9df81", include_script_body=True
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        artifact = result["data"]["artifact"]
        # Non-sensitive fields should be present
        assert artifact["name"] == "Sensitive BR"
        assert artifact["script"] == "gs.log('hello');"
        # Sensitive fields should be masked
        assert artifact["password"] != "super_secret_123"
        assert artifact["password"] == "***MASKED***"
        assert artifact["auth_token"] == "***MASKED***"


# ── docs_test_scenarios ───────────────────────────────────────────────────


class TestDocsTestScenarios:
    """Tests for the docs_test_scenarios tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_detects_condition_branches(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Detects if/else conditions and suggests test scenarios."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/5f94cbf2a18c848c38da0c789d5da01b").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "5f94cbf2a18c848c38da0c789d5da01b",
                        "name": "Priority handler",
                        "script": "if (current.priority == 1) { current.state = 2; } else { current.state = 1; }",
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](
            artifact_type="business_rule", sys_id="5f94cbf2a18c848c38da0c789d5da01b"
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert len(result["data"]["scenarios"]) >= 1
        # Should suggest testing the conditional branches
        scenario_names = [s["scenario"] for s in result["data"]["scenarios"]]
        assert any("condition" in s.lower() or "branch" in s.lower() for s in scenario_names)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_detects_insert_vs_update(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Detects insert/update operation checks."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/3d89a2d60f9752f52469325512b23c1e").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "3d89a2d60f9752f52469325512b23c1e",
                        "name": "Operation handler",
                        "script": "if (current.operation() == 'insert') { gs.log('new'); } else if (current.operation() == 'update') { gs.log('update'); }",
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](
            artifact_type="business_rule", sys_id="3d89a2d60f9752f52469325512b23c1e"
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        scenario_names = [s["scenario"] for s in result["data"]["scenarios"]]
        assert any("insert" in s.lower() for s in scenario_names)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_empty_script_returns_generic(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Empty script returns generic suggestions."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/1bf9bbae004e659911b334ee1b5bc4b6").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "1bf9bbae004e659911b334ee1b5bc4b6",
                        "name": "Empty BR",
                        "script": "",
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](
            artifact_type="business_rule", sys_id="1bf9bbae004e659911b334ee1b5bc4b6"
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        # Should still return at least generic scenarios
        assert len(result["data"]["scenarios"]) >= 1

    @pytest.mark.asyncio()
    async def test_invalid_artifact_type_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Unknown artifact_type returns an error with valid types listed."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](artifact_type="bogus_type", sys_id="6367c48dd193d56ea7b0baad25b19455")
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "bogus_type" in result["error"]["message"]
        assert "Valid:" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_detects_delete_operation(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Detects delete operation check in script."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/61bf664b40fbd88d122defa600c69f14").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "61bf664b40fbd88d122defa600c69f14",
                        "name": "Delete handler",
                        "script": "if (current.operation() == 'delete') { gs.log('deleted'); }",
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](
            artifact_type="business_rule", sys_id="61bf664b40fbd88d122defa600c69f14"
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        scenario_names = [s["scenario"] for s in result["data"]["scenarios"]]
        assert any("delete" in s.lower() for s in scenario_names)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_detects_is_new_record(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Detects isNewRecord() check in script."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/83839a9c296d5abac37d82d3b4840022").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "83839a9c296d5abac37d82d3b4840022",
                        "name": "New record handler",
                        "script": "if (current.isNewRecord()) { current.state = 1; }",
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](
            artifact_type="business_rule", sys_id="83839a9c296d5abac37d82d3b4840022"
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        scenario_names = [s["scenario"] for s in result["data"]["scenarios"]]
        assert any("new record" in s.lower() for s in scenario_names)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_detects_role_check(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Detects gs.hasRole() check in script."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/e70b73e9294b3ae08fa13456ec932bdd").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "e70b73e9294b3ae08fa13456ec932bdd",
                        "name": "Role gated",
                        "script": "if (gs.hasRole('admin')) { current.state = 2; }",
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](
            artifact_type="business_rule", sys_id="e70b73e9294b3ae08fa13456ec932bdd"
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        scenario_names = [s["scenario"] for s in result["data"]["scenarios"]]
        assert any("admin" in s for s in scenario_names)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_detects_abort_action(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Detects setAbortAction(true) in script."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/c00bbddf942d596518d4ccdad89a4bdc").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "c00bbddf942d596518d4ccdad89a4bdc",
                        "name": "Abort handler",
                        "script": "if (current.priority == 1) { current.setAbortAction(true); }",
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](
            artifact_type="business_rule", sys_id="c00bbddf942d596518d4ccdad89a4bdc"
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        scenario_names = [s["scenario"] for s in result["data"]["scenarios"]]
        assert any("abort" in s.lower() for s in scenario_names)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_detects_gliderecord_dependency(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Detects GlideRecord table dependencies in script."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/302dfaa738c5008f7359702d3a68d9bb").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "302dfaa738c5008f7359702d3a68d9bb",
                        "name": "GR lookup",
                        "script": "var gr = new GlideRecord('sys_user'); gr.get(current.assigned_to);",
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](
            artifact_type="business_rule", sys_id="302dfaa738c5008f7359702d3a68d9bb"
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        scenario_names = [s["scenario"] for s in result["data"]["scenarios"]]
        assert any("sys_user" in s for s in scenario_names)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_no_patterns_returns_generic(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Script with no detectable patterns returns generic fallback scenario."""
        # Use a non-empty script that matches none of the detection patterns
        respx.get(f"{BASE_URL}/api/now/table/sys_script/3190599a75cb88b0c8873d2695a1eaa2").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "3190599a75cb88b0c8873d2695a1eaa2",
                        "name": "Plain BR",
                        "script": "gs.log('hello world');",
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](
            artifact_type="business_rule", sys_id="3190599a75cb88b0c8873d2695a1eaa2"
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["scenario_count"] >= 1
        scenario_names = [s["scenario"] for s in result["data"]["scenarios"]]
        assert any("basic" in s.lower() for s in scenario_names)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_masks_sensitive_fields_in_record(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Sensitive fields in the artifact record are masked before generating scenarios."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/a4735c7a88ec47eec3fba55319b9df81").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "a4735c7a88ec47eec3fba55319b9df81",
                        "name": "Sensitive BR",
                        "script": "if (current.priority == 1) { current.state = 2; }",
                        "collection": "incident",
                        "password": "super_secret_123",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](
            artifact_type="business_rule", sys_id="a4735c7a88ec47eec3fba55319b9df81"
        )
        result = decode_response(raw)

        assert result["status"] == "success"
        # The artifact name in response should reflect the masked record
        assert result["data"]["artifact"]["name"] == "Sensitive BR"
        # Scenarios should still be generated from the script
        assert result["data"]["scenario_count"] >= 1


# ── docs_review_notes ─────────────────────────────────────────────────────


class TestDocsReviewNotes:
    """Tests for the docs_review_notes tool."""

    @pytest.mark.asyncio()
    async def test_invalid_artifact_type_returns_error(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Unknown artifact_type returns an error with valid types listed."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_review_notes"](artifact_type="bogus_type", sys_id="6367c48dd193d56ea7b0baad25b19455")
        result = decode_response(raw)

        assert result["status"] == "error"
        assert "bogus_type" in result["error"]["message"]
        assert "Valid:" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_empty_script_no_findings(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Empty script returns empty findings list."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/5be222eb14c6f8214c606ca364c61ff2").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "5be222eb14c6f8214c606ca364c61ff2",
                        "name": "Empty BR",
                        "script": "   ",
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_review_notes"](artifact_type="business_rule", sys_id="5be222eb14c6f8214c606ca364c61ff2")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert result["data"]["findings"] == []
        assert result["data"]["finding_count"] == 0

    @pytest.mark.asyncio()
    @respx.mock
    async def test_detects_current_update(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Detects current.update() anti-pattern in script."""
        script = "current.state = 2;\ncurrent.update();"
        respx.get(f"{BASE_URL}/api/now/table/sys_script/d833ee4ab99720915651478f5fef8e68").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "d833ee4ab99720915651478f5fef8e68",
                        "name": "Current update BR",
                        "script": script,
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_review_notes"](artifact_type="business_rule", sys_id="d833ee4ab99720915651478f5fef8e68")
        result = decode_response(raw)

        assert result["status"] == "success"
        categories = [f["category"] for f in result["data"]["findings"]]
        assert "current_update_in_br" in categories

    @pytest.mark.asyncio()
    @respx.mock
    async def test_detects_gliderecord_in_loop(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Detects GlideRecord query inside a loop."""
        script = (
            "var gr = new GlideRecord('task');\n"
            "gr.query();\n"
            "while (gr.next()) {\n"
            "  var inner = new GlideRecord('sys_user');\n"
            "  inner.get(gr.assigned_to);\n"
            "}"
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_script/5f94cbf2a18c848c38da0c789d5da01b").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "5f94cbf2a18c848c38da0c789d5da01b",
                        "name": "Bad BR",
                        "script": script,
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_review_notes"](artifact_type="business_rule", sys_id="5f94cbf2a18c848c38da0c789d5da01b")
        result = decode_response(raw)

        assert result["status"] == "success"
        findings = result["data"]["findings"]
        assert len(findings) >= 1
        categories = [f["category"] for f in findings]
        assert "gliderecord_in_loop" in categories

    @pytest.mark.asyncio()
    @respx.mock
    async def test_detects_hardcoded_sysid(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Detects hardcoded 32-char hex sys_ids in script."""
        script = "var user = gs.getUser('6816f79cc0a8016401c5a33be04be441');"
        respx.get(f"{BASE_URL}/api/now/table/sys_script/3d89a2d60f9752f52469325512b23c1e").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "3d89a2d60f9752f52469325512b23c1e",
                        "name": "Hardcoded BR",
                        "script": script,
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_review_notes"](artifact_type="business_rule", sys_id="3d89a2d60f9752f52469325512b23c1e")
        result = decode_response(raw)

        assert result["status"] == "success"
        categories = [f["category"] for f in result["data"]["findings"]]
        assert "hardcoded_sys_id" in categories

    @pytest.mark.asyncio()
    @respx.mock
    async def test_clean_script_no_findings(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Clean script returns no findings."""
        script = "current.state = 2;"
        respx.get(f"{BASE_URL}/api/now/table/sys_script/1bf9bbae004e659911b334ee1b5bc4b6").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "1bf9bbae004e659911b334ee1b5bc4b6",
                        "name": "Clean BR",
                        "script": script,
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_review_notes"](artifact_type="business_rule", sys_id="1bf9bbae004e659911b334ee1b5bc4b6")
        result = decode_response(raw)

        assert result["status"] == "success"
        assert len(result["data"]["findings"]) == 0

    @pytest.mark.asyncio()
    @respx.mock
    async def test_masks_sensitive_fields_in_record(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Sensitive fields in the artifact record are masked before scanning."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/a4735c7a88ec47eec3fba55319b9df81").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "a4735c7a88ec47eec3fba55319b9df81",
                        "name": "Sensitive BR",
                        "script": "current.state = 2;",
                        "collection": "incident",
                        "password": "super_secret_123",
                        "api_key": "key_abc_123",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_review_notes"](artifact_type="business_rule", sys_id="a4735c7a88ec47eec3fba55319b9df81")
        result = decode_response(raw)

        assert result["status"] == "success"
        # The artifact name in response should be present
        assert result["data"]["artifact"]["name"] == "Sensitive BR"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_loop_truncated_at_eof(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Loop condition at EOF with no body is silently skipped."""
        script = "var gr = new GlideRecord('task');\ngr.query();\nwhile (gr.next())   "
        respx.get(f"{BASE_URL}/api/now/table/sys_script/3d89a2d60f9752f52469325512b23c1e").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "3d89a2d60f9752f52469325512b23c1e",
                        "name": "Truncated BR",
                        "script": script,
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_review_notes"](artifact_type="business_rule", sys_id="3d89a2d60f9752f52469325512b23c1e")
        result = decode_response(raw)

        assert result["status"] == "success"
        categories = [f["category"] for f in result["data"]["findings"]]
        assert "gliderecord_in_loop" not in categories


class TestDocumentationHelpers:
    """Unit tests for extracted helper functions _find_block_end and _extract_loop_body."""

    def test_find_block_end_no_matching_brace(self) -> None:
        """Return last index when script has no closing brace."""
        from servicenow_mcp.tools.documentation import _find_block_end

        script = "function test() {"
        result = _find_block_end(script, 16)
        assert result == len(script) - 1

    def test_find_block_end_nested_braces(self) -> None:
        """Correctly match the outermost closing brace with nested blocks."""
        from servicenow_mcp.tools.documentation import _find_block_end

        script = "function test() { if (x) { } }"
        result = _find_block_end(script, 16)
        assert result == len(script) - 1
        assert script[result] == "}"

    def test_extract_loop_body_single_statement_semicolon(self) -> None:
        """Extract single statement terminated by semicolon (no braces)."""
        from servicenow_mcp.tools.documentation import _extract_loop_body

        script = "while (gr.next()) doSomething();\nvar x = 1;"
        result = _extract_loop_body(script, 18)
        assert "doSomething();" in result
        assert "var x" not in result

    def test_extract_loop_body_single_statement_newline(self) -> None:
        """Extract single statement terminated by newline (no semicolon before newline)."""
        from servicenow_mcp.tools.documentation import _extract_loop_body

        script = "while (gr.next()) doSomething()\nvar x = 1;"
        result = _extract_loop_body(script, 18)
        assert "doSomething()" in result
        assert "var x" not in result

    def test_extract_loop_body_empty_past_end(self) -> None:
        """Return empty string when only whitespace remains after condition."""
        from servicenow_mcp.tools.documentation import _extract_loop_body

        script = "while (true) "
        result = _extract_loop_body(script, 13)
        assert result == ""
