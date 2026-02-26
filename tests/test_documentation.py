"""Tests for documentation tools (docs_logic_map, docs_artifact_summary, docs_test_scenarios, docs_review_notes)."""

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
    """Helper: register documentation tools on a fresh MCP server and return tool map."""
    from mcp.server.fastmcp import FastMCP

    from servicenow_mcp.tools.documentation import register_tools

    mcp = FastMCP("test")
    register_tools(mcp, settings, auth_provider)
    return {t.name: t.fn for t in mcp._tool_manager._tools.values()}


# ── docs_logic_map ────────────────────────────────────────────────────────


class TestDocsLogicMap:
    """Tests for the docs_logic_map tool."""

    @pytest.mark.asyncio
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
        result = json.loads(raw)

        assert result["status"] == "success"
        data = result["data"]
        assert data["table"] == "incident"
        assert "phases" in data
        # Should have at least before_insert and after_update
        assert len(data["phases"]) > 0

    @pytest.mark.asyncio
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
        result = json.loads(raw)

        assert result["status"] == "success"
        assert result["data"]["total_automations"] == 0


# ── docs_artifact_summary ─────────────────────────────────────────────────


class TestDocsArtifactSummary:
    """Tests for the docs_artifact_summary tool."""

    @pytest.mark.asyncio
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
        result = json.loads(raw)

        assert result["status"] == "success"
        data = result["data"]
        assert data["artifact"]["name"] == "Update CI"
        # Should detect GlideRecord('cmdb_ci') in script
        assert "cmdb_ci" in data["referenced_tables"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_not_found_returns_error(self, settings, auth_provider):
        """Returns error for non-existent artifact."""
        respx.get(f"{BASE_URL}/api/now/table/sys_script/bad_id").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
        )

        tools = _register_and_get_tools(settings, auth_provider)
        raw = await tools["docs_artifact_summary"](artifact_type="business_rule", sys_id="bad_id")
        result = json.loads(raw)

        assert result["status"] == "error"

    @pytest.mark.asyncio
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
        result = json.loads(raw)

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

    @pytest.mark.asyncio
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
        result = json.loads(raw)

        assert result["status"] == "success"
        assert len(result["data"]["scenarios"]) >= 1
        # Should suggest testing the conditional branches
        scenario_names = [s["scenario"] for s in result["data"]["scenarios"]]
        assert any("condition" in s.lower() or "branch" in s.lower() for s in scenario_names)

    @pytest.mark.asyncio
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
        result = json.loads(raw)

        assert result["status"] == "success"
        scenario_names = [s["scenario"] for s in result["data"]["scenarios"]]
        assert any("insert" in s.lower() for s in scenario_names)

    @pytest.mark.asyncio
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
        result = json.loads(raw)

        assert result["status"] == "success"
        # Should still return at least generic scenarios
        assert len(result["data"]["scenarios"]) >= 1


# ── docs_review_notes ─────────────────────────────────────────────────────


class TestDocsReviewNotes:
    """Tests for the docs_review_notes tool."""

    @pytest.mark.asyncio
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
        result = json.loads(raw)

        assert result["status"] == "success"
        findings = result["data"]["findings"]
        assert len(findings) >= 1
        categories = [f["category"] for f in findings]
        assert "gliderecord_in_loop" in categories

    @pytest.mark.asyncio
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
        result = json.loads(raw)

        assert result["status"] == "success"
        categories = [f["category"] for f in result["data"]["findings"]]
        assert "hardcoded_sys_id" in categories

    @pytest.mark.asyncio
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
        result = json.loads(raw)

        assert result["status"] == "success"
        assert len(result["data"]["findings"]) == 0
