"""Tests for ServiceNow REST client."""

from typing import TYPE_CHECKING, Any

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from tests._mock_transport import http_mock

if TYPE_CHECKING:
    from pydantic import JsonValue

    from servicenow_mcp.config import Settings


BASE_URL = "https://test.service-now.com"


@pytest.fixture()
def auth_provider(settings: Settings) -> OAuthPKCEProvider:
    """Create a OAuthPKCEProvider from test settings."""
    return OAuthPKCEProvider(settings)


class TestServiceNowClientGetRecord:
    """Test get_record method."""

    @pytest.mark.asyncio()
    async def test_get_record_acl_error(
        self, settings: Settings, auth_provider: OAuthPKCEProvider
    ) -> None:
        """Raises ACLError for explicit ACL 403 responses."""
        from servicenow_mcp.errors import ACLError
        from servicenow_mcp.platform.client import ServiceNowClient

        http_mock.get(f"{BASE_URL}/api/now/table/incident/abc123").mock(
            return_value=httpx2.Response(
                403,
                json={
                    "error": {
                        "message": "User Not Authorized",
                        "detail": "Failed API level ACL Validation",
                    },
                    "status": "failure",
                },
            ),
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ACLError) as exc_info:
                await client.get_record("incident", "abc123")

        assert str(exc_info.value) == "User Not Authorized"


class TestServiceNowClientQueryRecords:
    """Test query_records method."""

    @pytest.mark.parametrize("offset", [pytest.param(20, id="20")])
    async def test_query_records_preserves_pagination_without_link_headers(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        offset: int,
    ) -> None:
        from servicenow_mcp.platform.client import ServiceNowClient

        record = {"sys_id": "a" * 32, "asset_tag": "TEST001"}
        route = http_mock.get(f"{BASE_URL}/api/now/table/alm_asset").respond(
            200,
            json={"result": [record]},
            headers={"X-Total-Count": "42"},
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records(
                "alm_asset",
                "active=true",
                fields=["sys_id", "asset_tag"],
                limit=10,
                offset=offset,
                order_by="sys_id",
                display_values=True,
            )

        assert result == {"records": [record], "count": 42}
        assert route.call_count == 1
        params = dict(route.calls.last.request.url.params)
        assert params == {
            "sysparm_limit": "10",
            "sysparm_display_value": "true",
            "sysparm_suppress_pagination_header": "true",
            "sysparm_query": "active=true^ORDERBYsys_id",
            "sysparm_fields": "sys_id,asset_tag",
            **({"sysparm_offset": str(offset)} if offset else {}),
        }


class TestServiceNowClientAttachmentMethods:
    """Test attachment-specific client helpers."""

    @pytest.mark.asyncio()
    async def test_list_attachments_with_query_offset_and_order_by(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
    ) -> None:
        """Builds attachment list params with filters, offset, and ordering."""
        from servicenow_mcp.platform.client import ServiceNowClient

        route = http_mock.get(f"{BASE_URL}/api/now/attachment").mock(
            return_value=httpx2.Response(
                200, json={"result": []}, headers={"X-Total-Count": "invalid"}
            ),
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            client_any: Any = client
            result = await client_any.list_attachments(
                query="table_name=incident",
                limit=10,
                offset=5,
                order_by="sys_id",
            )

        assert result == {"records": [], "count": 0}
        assert route.calls.last is not None
        params = route.calls.last.request.url.params
        assert params["sysparm_limit"] == "10"
        assert params["sysparm_offset"] == "5"
        assert params["sysparm_query"] == "table_name=incident^ORDERBYsys_id"

    @pytest.mark.asyncio()
    async def test_upload_attachment_includes_optional_params_when_present(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
    ) -> None:
        """Passes optional upload params through when callers provide them."""
        from servicenow_mcp.platform.client import ServiceNowClient

        route = http_mock.post(f"{BASE_URL}/api/now/attachment/file").mock(
            return_value=httpx2.Response(201, json={"result": {"sys_id": "abc"}}),
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            client_any: Any = client
            await client_any.upload_attachment(
                table_name="incident",
                table_sys_id="b" * 32,
                file_name="hello.txt",
                content=b"hello",
                encryption_context="ctx",
                creation_time="2026-03-11 10:00:00",
            )

        assert route.calls.last is not None
        params = route.calls.last.request.url.params
        assert params["encryption_context"] == "ctx"
        assert params["creation_time"] == "2026-03-11 10:00:00"


class TestServiceNowClientErrorHandling:
    """Test error mapping for various HTTP status codes."""

    @pytest.mark.parametrize(
        "payload,expected",
        [
            pytest.param(
                {"error": {"message": None}},
                "Default error",
                id="payload2-Default error",
            ),
            pytest.param([], "Default error", id="payload9-Default error"),
        ],
    )
    def test_error_message_requires_a_string(
        self, payload: JsonValue, expected: str
    ) -> None:
        from servicenow_mcp.platform.client import ServiceNowClient

        response = httpx2.Response(500, json=payload)
        assert (
            ServiceNowClient._extract_error_message(response, "Default error")
            == expected
        )


class TestServiceNowClientCodeSearch:
    """Test Code Search API methods."""

    @pytest.mark.asyncio()
    async def test_code_search_success(
        self, settings: Settings, auth_provider: OAuthPKCEProvider
    ) -> None:
        """Performs code search and returns results."""
        from servicenow_mcp.platform.client import ServiceNowClient

        http_mock.get(f"{BASE_URL}/api/sn_codesearch/code_search/search").mock(
            return_value=httpx2.Response(
                200,
                json={
                    "result": {
                        "search_results": [
                            {
                                "className": "sys_script_include",
                                "name": "TestUtil",
                                "match": "AbstractAjaxProcessor",
                            },
                        ],
                    },
                },
            ),
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.code_search("AbstractAjaxProcessor")

        assert result is not None


class TestClientNotInitialized:
    """Test that calling methods without async with context raises RuntimeError."""

    @pytest.mark.asyncio()
    async def test_get_record_without_context_manager(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
    ) -> None:
        """get_record raises RuntimeError when client is not initialized."""
        from servicenow_mcp.platform.client import ServiceNowClient

        client = ServiceNowClient(settings, auth_provider)
        with pytest.raises(RuntimeError, match="Client not initialized"):
            await client.get_record("incident", "abc123")


class TestUrlBuilderValidation:
    """Test that URL builder methods validate identifiers."""

    def test_table_url_rejects_invalid_name(
        self, settings: Settings, auth_provider: OAuthPKCEProvider
    ) -> None:
        """_table_url raises ValueError for invalid table name."""
        from servicenow_mcp.platform.client import ServiceNowClient

        client = ServiceNowClient(settings, auth_provider)
        with pytest.raises(ValueError, match="Invalid identifier"):
            client._table_url("INVALID-TABLE!")

    def test_stats_url_rejects_invalid_name(
        self, settings: Settings, auth_provider: OAuthPKCEProvider
    ) -> None:
        """_stats_url raises ValueError for invalid table name."""
        from servicenow_mcp.platform.client import ServiceNowClient

        client = ServiceNowClient(settings, auth_provider)
        with pytest.raises(ValueError, match="Invalid identifier"):
            client._stats_url("bad/table")

    def test_attachment_url_rejects_invalid_sys_id(
        self, settings: Settings, auth_provider: OAuthPKCEProvider
    ) -> None:
        """_attachment_url raises ValueError for invalid attachment sys_id."""
        from servicenow_mcp.platform.client import ServiceNowClient

        client = ServiceNowClient(settings, auth_provider)
        with pytest.raises(ValueError, match="Invalid sys_id"):
            client._attachment_url("invalid-sys-id")


class TestServiceNowClientFlowDesigner:
    """Flow Designer client methods (Phase 3)."""

    @pytest.mark.asyncio()
    async def test_find_flows_by_name_builds_or_query(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
    ) -> None:
        """Encoded query joins ``name=`` and ``internal_name=`` with ``^OR``."""
        from servicenow_mcp.platform.client import ServiceNowClient

        route = http_mock.get(f"{BASE_URL}/api/now/table/sys_hub_flow").mock(
            return_value=httpx2.Response(200, json={"result": []}),
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            await client.find_flows_by_name("My Flow")

        params = route.calls.last.request.url.params
        query = params["sysparm_query"]
        assert "name=My Flow" in query
        assert "^ORinternal_name=My Flow" in query

    @pytest.mark.asyncio()
    async def test_list_flow_variables_filters_by_model(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
    ) -> None:
        """Flow variables are restricted to the inspected flow's model record."""
        from servicenow_mcp.platform.client import ServiceNowClient

        flow_id = "a" * 32
        route = http_mock.get(f"{BASE_URL}/api/now/table/sys_hub_flow_variable").mock(
            return_value=httpx2.Response(200, json={"result": []}),
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            assert await client.list_flow_variables(flow_id) == []

        assert (
            route.calls.last.request.url.params["sysparm_query"]
            == f"model={flow_id}^ORDERBYorder"
        )

    @pytest.mark.asyncio()
    async def test_list_flow_stages_uses_explicit_projection_and_flow_relation(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
    ) -> None:
        """Stage reads select the lifecycle fields and bind them to one flow-base record."""
        from servicenow_mcp.platform.client import ServiceNowClient

        flow_id = "a" * 32
        route = http_mock.get(f"{BASE_URL}/api/now/table/sys_hub_flow_stage").mock(
            return_value=httpx2.Response(200, json={"result": []}),
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            assert await client.list_flow_stages(flow_id, limit=17) == []

        params = route.calls.last.request.url.params
        assert params["sysparm_query"] == f"flow={flow_id}^ORDERBYorder"
        assert params["sysparm_limit"] == "17"
        assert params["sysparm_fields"] == (
            "stage_id,label,value,states,type,order,component_indexes,ancestor_component_id,"
            "ancestor_stage_id,ancestral_if_else_logic,always_show"
        )

    @pytest.mark.asyncio()
    async def test_list_action_definition_fields_join_through_action_type(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
    ) -> None:
        """Action input/output definitions use their documented ``action_type`` relation."""
        from servicenow_mcp.platform.client import ServiceNowClient

        action_type_ids = ["a" * 32, "b" * 32]
        input_route = http_mock.get(
            f"{BASE_URL}/api/now/table/sys_hub_action_input"
        ).mock(
            return_value=httpx2.Response(200, json={"result": [{"name": "input"}]}),
        )
        output_route = http_mock.get(
            f"{BASE_URL}/api/now/table/sys_hub_action_output"
        ).mock(
            return_value=httpx2.Response(200, json={"result": [{"name": "output"}]}),
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            assert await client.list_action_input_definitions(action_type_ids) == [
                {"name": "input"}
            ]
            assert await client.list_action_output_definitions(action_type_ids) == [
                {"name": "output"}
            ]

        expected_prefix = f"action_typeIN{','.join(action_type_ids)}"
        assert input_route.calls.last.request.url.params["sysparm_query"].startswith(
            expected_prefix
        )
        assert output_route.calls.last.request.url.params["sysparm_query"].startswith(
            expected_prefix
        )
        assert (
            "default_value"
            in input_route.calls.last.request.url.params["sysparm_fields"]
        )
        assert (
            "default_value"
            not in output_route.calls.last.request.url.params["sysparm_fields"]
        )

    @pytest.mark.asyncio()
    async def test_list_action_definition_fields_empty_input_no_http_call(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
    ) -> None:
        """Empty action-type input short-circuits both definition lookups."""
        from servicenow_mcp.platform.client import ServiceNowClient

        async with ServiceNowClient(settings, auth_provider) as client:
            assert await client.list_action_input_definitions([]) == []
            assert await client.list_action_output_definitions([]) == []

    @pytest.mark.asyncio()
    async def test_list_record_triggers_empty_input_no_http_call(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
    ) -> None:
        """Empty input short-circuits to ``[]`` without touching the network."""
        from servicenow_mcp.platform.client import ServiceNowClient

        # No mock route registered: any HTTP call would fail the test.
        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.list_record_triggers([])

        assert result == []

    @pytest.mark.asyncio()
    async def test_get_flows_bulk_empty_input_no_http_call(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
    ) -> None:
        """Empty input short-circuits to ``[]`` without an HTTP call."""
        from servicenow_mcp.platform.client import ServiceNowClient

        async with ServiceNowClient(settings, auth_provider) as client:
            assert await client.get_flows_bulk([]) == []
