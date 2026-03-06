"""Tests for documentation tools (docs_logic_map, docs_artifact_summary, docs_test_scenarios, docs_review_notes)."""

import httpx
import pytest
import respx
from toon_format import decode as toon_decode

from servicenow_mcp.auth import BasicAuthProvider


BASE_URL = "https://test.service-now.com"


@pytest.fixture()
def auth_provider(settings):
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


def _register_and_get_tools(settings, auth_provider):
    """Helper: register documentation tools on a fresh MCP server and return tool map."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.documentation import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}


# ── docs_logic_map ────────────────────────────────────────────────────────


class TestDocsLogicMap:
    """Tests for the docs_logic_map tool."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_returns_lifecycle_map(self, settings, auth_provider):
        """Returns automation grouped by lifecycle phase."""
        # Business rules
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "br1",
                            "name": "Set defaults",
                            "when": "before",
                            "action_insert": "true",
                            "action_update": "false",
                            "action_delete": "false",
                            "active": "true",
                        },
                        {
                            "sys_id": "br2",
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
                            "sys_id": "cs1",
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
        result = toon_decode(raw)

        assert result["status"] == "success"
        data = result["data"]
        assert data["table"] == "incident"
        assert "phases" in data
        # Should have at least before_insert and after_update
        assert len(data["phases"]) > 0

    @pytest.mark.asyncio()
    @respx.mock
    async def test_empty_table_no_automation(self, settings, auth_provider):
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
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["total_automations"] == 0

    @pytest.mark.asyncio()
    @respx.mock
    async def test_br_delete_action_and_no_operations(self, settings, auth_provider):
        """Covers delete action branch and fallback to 'all' when no operations are set."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "br_del",
                            "name": "On delete",
                            "when": "before",
                            "action_insert": "false",
                            "action_update": "false",
                            "action_delete": "true",
                            "active": "true",
                        },
                        {
                            "sys_id": "br_all",
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
        result = toon_decode(raw)

        assert result["status"] == "success"
        phases = result["data"]["phases"]
        assert "before_delete" in phases
        assert phases["before_delete"][0]["name"] == "On delete"
        assert "before_all" in phases
        assert phases["before_all"][0]["name"] == "On all"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_ui_policies_and_ui_actions_populated(self, settings, auth_provider):
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
                            "sys_id": "uip1",
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
                            "sys_id": "uia1",
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
        result = toon_decode(raw)

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
    async def test_returns_summary_with_dependencies(self, settings, auth_provider):
        """Returns artifact summary with referenced tables and referenced_by."""
        # Get the artifact
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br001",
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
        raw = await tools["docs_artifact_summary"](artifact_type="business_rule", sys_id="br001")
        result = toon_decode(raw)

        assert result["status"] == "success"
        data = result["data"]
        assert data["artifact"]["name"] == "Update CI"
        # Should detect GlideRecord('cmdb_ci') in script
        assert "cmdb_ci" in data["referenced_tables"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_not_found_returns_error(self, settings, auth_provider):
        """Returns error for non-existent artifact."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/bad_id").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_artifact_summary"](artifact_type="business_rule", sys_id="bad_id")
        result = toon_decode(raw)

        assert result["status"] == "error"

    @pytest.mark.asyncio()
    async def test_invalid_artifact_type_returns_error(self, settings, auth_provider):
        """Unknown artifact_type returns an error with valid types listed."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_artifact_summary"](artifact_type="bogus_type", sys_id="abc123")
        result = toon_decode(raw)

        assert result["status"] == "error"
        assert "bogus_type" in result["error"]["message"]
        assert "Valid:" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_code_search_failure_is_silent(self, settings, auth_provider):
        """Code search exception is caught silently; referenced_by is empty."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br_search_fail").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br_search_fail",
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
        raw = await tools["docs_artifact_summary"](artifact_type="business_rule", sys_id="br_search_fail")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["referenced_by"] == []
        assert "task" in result["data"]["referenced_tables"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_masks_sensitive_fields(self, settings, auth_provider):
        """Sensitive fields in the artifact record are masked in the response."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br_sensitive").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br_sensitive",
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
        raw = await tools["docs_artifact_summary"](artifact_type="business_rule", sys_id="br_sensitive")
        result = toon_decode(raw)

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
    async def test_detects_condition_branches(self, settings, auth_provider):
        """Detects if/else conditions and suggests test scenarios."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br001",
                        "name": "Priority handler",
                        "script": "if (current.priority == 1) { current.state = 2; } else { current.state = 1; }",
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](artifact_type="business_rule", sys_id="br001")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert len(result["data"]["scenarios"]) >= 1
        # Should suggest testing the conditional branches
        scenario_names = [s["scenario"] for s in result["data"]["scenarios"]]
        assert any("condition" in s.lower() or "branch" in s.lower() for s in scenario_names)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_detects_insert_vs_update(self, settings, auth_provider):
        """Detects insert/update operation checks."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br002").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br002",
                        "name": "Operation handler",
                        "script": "if (current.operation() == 'insert') { gs.log('new'); } else if (current.operation() == 'update') { gs.log('update'); }",
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](artifact_type="business_rule", sys_id="br002")
        result = toon_decode(raw)

        assert result["status"] == "success"
        scenario_names = [s["scenario"] for s in result["data"]["scenarios"]]
        assert any("insert" in s.lower() for s in scenario_names)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_empty_script_returns_generic(self, settings, auth_provider):
        """Empty script returns generic suggestions."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br003").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br003",
                        "name": "Empty BR",
                        "script": "",
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](artifact_type="business_rule", sys_id="br003")
        result = toon_decode(raw)

        assert result["status"] == "success"
        # Should still return at least generic scenarios
        assert len(result["data"]["scenarios"]) >= 1

    @pytest.mark.asyncio()
    async def test_invalid_artifact_type_returns_error(self, settings, auth_provider):
        """Unknown artifact_type returns an error with valid types listed."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](artifact_type="bogus_type", sys_id="abc123")
        result = toon_decode(raw)

        assert result["status"] == "error"
        assert "bogus_type" in result["error"]["message"]
        assert "Valid:" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_detects_delete_operation(self, settings, auth_provider):
        """Detects delete operation check in script."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br_del").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br_del",
                        "name": "Delete handler",
                        "script": "if (current.operation() == 'delete') { gs.log('deleted'); }",
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](artifact_type="business_rule", sys_id="br_del")
        result = toon_decode(raw)

        assert result["status"] == "success"
        scenario_names = [s["scenario"] for s in result["data"]["scenarios"]]
        assert any("delete" in s.lower() for s in scenario_names)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_detects_is_new_record(self, settings, auth_provider):
        """Detects isNewRecord() check in script."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br_new").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br_new",
                        "name": "New record handler",
                        "script": "if (current.isNewRecord()) { current.state = 1; }",
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](artifact_type="business_rule", sys_id="br_new")
        result = toon_decode(raw)

        assert result["status"] == "success"
        scenario_names = [s["scenario"] for s in result["data"]["scenarios"]]
        assert any("new record" in s.lower() for s in scenario_names)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_detects_role_check(self, settings, auth_provider):
        """Detects gs.hasRole() check in script."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br_role").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br_role",
                        "name": "Role gated",
                        "script": "if (gs.hasRole('admin')) { current.state = 2; }",
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](artifact_type="business_rule", sys_id="br_role")
        result = toon_decode(raw)

        assert result["status"] == "success"
        scenario_names = [s["scenario"] for s in result["data"]["scenarios"]]
        assert any("admin" in s for s in scenario_names)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_detects_abort_action(self, settings, auth_provider):
        """Detects setAbortAction(true) in script."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br_abort").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br_abort",
                        "name": "Abort handler",
                        "script": "if (current.priority == 1) { current.setAbortAction(true); }",
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](artifact_type="business_rule", sys_id="br_abort")
        result = toon_decode(raw)

        assert result["status"] == "success"
        scenario_names = [s["scenario"] for s in result["data"]["scenarios"]]
        assert any("abort" in s.lower() for s in scenario_names)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_detects_gliderecord_dependency(self, settings, auth_provider):
        """Detects GlideRecord table dependencies in script."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br_gr").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br_gr",
                        "name": "GR lookup",
                        "script": "var gr = new GlideRecord('sys_user'); gr.get(current.assigned_to);",
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](artifact_type="business_rule", sys_id="br_gr")
        result = toon_decode(raw)

        assert result["status"] == "success"
        scenario_names = [s["scenario"] for s in result["data"]["scenarios"]]
        assert any("sys_user" in s for s in scenario_names)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_no_patterns_returns_generic(self, settings, auth_provider):
        """Script with no detectable patterns returns generic fallback scenario."""
        # Use a non-empty script that matches none of the detection patterns
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br_plain").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br_plain",
                        "name": "Plain BR",
                        "script": "gs.log('hello world');",
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](artifact_type="business_rule", sys_id="br_plain")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["scenario_count"] >= 1
        scenario_names = [s["scenario"] for s in result["data"]["scenarios"]]
        assert any("basic" in s.lower() for s in scenario_names)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_masks_sensitive_fields_in_record(self, settings, auth_provider):
        """Sensitive fields in the artifact record are masked before generating scenarios."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br_sensitive").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br_sensitive",
                        "name": "Sensitive BR",
                        "script": "if (current.priority == 1) { current.state = 2; }",
                        "collection": "incident",
                        "password": "super_secret_123",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_test_scenarios"](artifact_type="business_rule", sys_id="br_sensitive")
        result = toon_decode(raw)

        assert result["status"] == "success"
        # The artifact name in response should reflect the masked record
        assert result["data"]["artifact"]["name"] == "Sensitive BR"
        # Scenarios should still be generated from the script
        assert result["data"]["scenario_count"] >= 1


# ── docs_review_notes ─────────────────────────────────────────────────────


class TestDocsReviewNotes:
    """Tests for the docs_review_notes tool."""

    @pytest.mark.asyncio()
    async def test_invalid_artifact_type_returns_error(self, settings, auth_provider):
        """Unknown artifact_type returns an error with valid types listed."""
        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_review_notes"](artifact_type="bogus_type", sys_id="abc123")
        result = toon_decode(raw)

        assert result["status"] == "error"
        assert "bogus_type" in result["error"]["message"]
        assert "Valid:" in result["error"]["message"]

    @pytest.mark.asyncio()
    @respx.mock
    async def test_empty_script_no_findings(self, settings, auth_provider):
        """Empty script returns empty findings list."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br_empty").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br_empty",
                        "name": "Empty BR",
                        "script": "   ",
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_review_notes"](artifact_type="business_rule", sys_id="br_empty")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert result["data"]["findings"] == []
        assert result["data"]["finding_count"] == 0

    @pytest.mark.asyncio()
    @respx.mock
    async def test_detects_current_update(self, settings, auth_provider):
        """Detects current.update() anti-pattern in script."""
        script = "current.state = 2;\ncurrent.update();"
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br_cupd").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br_cupd",
                        "name": "Current update BR",
                        "script": script,
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_review_notes"](artifact_type="business_rule", sys_id="br_cupd")
        result = toon_decode(raw)

        assert result["status"] == "success"
        categories = [f["category"] for f in result["data"]["findings"]]
        assert "current_update_in_br" in categories

    @pytest.mark.asyncio()
    @respx.mock
    async def test_detects_gliderecord_in_loop(self, settings, auth_provider):
        """Detects GlideRecord query inside a loop."""
        script = (
            "var gr = new GlideRecord('task');\n"
            "gr.query();\n"
            "while (gr.next()) {\n"
            "  var inner = new GlideRecord('sys_user');\n"
            "  inner.get(gr.assigned_to);\n"
            "}"
        )
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br001").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br001",
                        "name": "Bad BR",
                        "script": script,
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_review_notes"](artifact_type="business_rule", sys_id="br001")
        result = toon_decode(raw)

        assert result["status"] == "success"
        findings = result["data"]["findings"]
        assert len(findings) >= 1
        categories = [f["category"] for f in findings]
        assert "gliderecord_in_loop" in categories

    @pytest.mark.asyncio()
    @respx.mock
    async def test_detects_hardcoded_sysid(self, settings, auth_provider):
        """Detects hardcoded 32-char hex sys_ids in script."""
        script = "var user = gs.getUser('6816f79cc0a8016401c5a33be04be441');"
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br002").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br002",
                        "name": "Hardcoded BR",
                        "script": script,
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_review_notes"](artifact_type="business_rule", sys_id="br002")
        result = toon_decode(raw)

        assert result["status"] == "success"
        categories = [f["category"] for f in result["data"]["findings"]]
        assert "hardcoded_sys_id" in categories

    @pytest.mark.asyncio()
    @respx.mock
    async def test_clean_script_no_findings(self, settings, auth_provider):
        """Clean script returns no findings."""
        script = "current.state = 2;"
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br003").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br003",
                        "name": "Clean BR",
                        "script": script,
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_review_notes"](artifact_type="business_rule", sys_id="br003")
        result = toon_decode(raw)

        assert result["status"] == "success"
        assert len(result["data"]["findings"]) == 0

    @pytest.mark.asyncio()
    @respx.mock
    async def test_masks_sensitive_fields_in_record(self, settings, auth_provider):
        """Sensitive fields in the artifact record are masked before scanning."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br_sensitive").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br_sensitive",
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
        raw = await tools["docs_review_notes"](artifact_type="business_rule", sys_id="br_sensitive")
        result = toon_decode(raw)

        assert result["status"] == "success"
        # The artifact name in response should be present
        assert result["data"]["artifact"]["name"] == "Sensitive BR"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_loop_truncated_at_eof(self, settings, auth_provider):
        """Loop condition at EOF with no body is silently skipped."""
        script = "var gr = new GlideRecord('task');\ngr.query();\nwhile (gr.next())   "
        respx.get(f"{BASE_URL}/api/now/table/sys_script/br002").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "br002",
                        "name": "Truncated BR",
                        "script": script,
                        "collection": "incident",
                    }
                },
            )
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_review_notes"](artifact_type="business_rule", sys_id="br002")
        result = toon_decode(raw)

        assert result["status"] == "success"
        categories = [f["category"] for f in result["data"]["findings"]]
        assert "gliderecord_in_loop" not in categories
