"""Tests for ServiceNow REST client."""

from typing import Any

import httpx
import pytest
import respx

from servicenow_mcp.auth import BasicAuthProvider
from servicenow_mcp.config import Settings


BASE_URL = "https://test.service-now.com"


@pytest.fixture()
def auth_provider(settings: Settings) -> BasicAuthProvider:
    """Create a BasicAuthProvider from test settings."""
    return BasicAuthProvider(settings)


class TestServiceNowClientGetRecord:
    """Test get_record method."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_record_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Fetches a single record by sys_id."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/now/table/incident/6367c48dd193d56ea7b0baad25b19455").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": "6367c48dd193d56ea7b0baad25b19455", "number": "INC0001"}},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            record = await client.get_record("incident", "6367c48dd193d56ea7b0baad25b19455")

        assert record == {"sys_id": "6367c48dd193d56ea7b0baad25b19455", "number": "INC0001"}
        assert route.called

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_record_with_fields(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Respects field selection parameter."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/now/table/incident/6367c48dd193d56ea7b0baad25b19455").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": "6367c48dd193d56ea7b0baad25b19455", "number": "INC0001"}},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            await client.get_record("incident", "6367c48dd193d56ea7b0baad25b19455", fields=["sys_id", "number"])

        assert route.calls.last is not None
        assert "sysparm_fields" in str(route.calls.last.request.url)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_record_with_display_values(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Passes display_value parameter."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/now/table/incident/6367c48dd193d56ea7b0baad25b19455").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": "6367c48dd193d56ea7b0baad25b19455"}},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            await client.get_record("incident", "6367c48dd193d56ea7b0baad25b19455", display_values=True)

        assert route.calls.last is not None
        assert "sysparm_display_value=true" in str(route.calls.last.request.url)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_record_without_display_values(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Omits display_value param when display_values is False."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/now/table/incident/6367c48dd193d56ea7b0baad25b19455").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": "6367c48dd193d56ea7b0baad25b19455"}},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            await client.get_record("incident", "6367c48dd193d56ea7b0baad25b19455", display_values=False)

        assert route.calls.last is not None
        assert "sysparm_display_value" not in str(route.calls.last.request.url)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_record_not_found(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Raises NotFoundError for 404."""
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.errors import NotFoundError

        missing_sys_id = "00000000000000000000000000000000"
        respx.get(f"{BASE_URL}/api/now/table/incident/{missing_sys_id}").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(NotFoundError):
                await client.get_record("incident", missing_sys_id)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_record_auth_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Raises AuthError for 401."""
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.errors import AuthError

        respx.get(f"{BASE_URL}/api/now/table/incident/6367c48dd193d56ea7b0baad25b19455").mock(
            return_value=httpx.Response(401, json={"error": {"message": "Unauthorized"}})
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(AuthError):
                await client.get_record("incident", "6367c48dd193d56ea7b0baad25b19455")

    @pytest.mark.asyncio()
    async def test_get_record_rejects_non_hex_sys_id(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """_table_url invokes validate_sys_id; non-hex sys_ids are rejected before any HTTP call.

        This is the chokepoint that blocks path-traversal-style sys_id inputs
        (e.g. '../foo', URL-encoded junk) from ever reaching the network.
        """
        from servicenow_mcp.client import ServiceNowClient

        async with ServiceNowClient(settings, auth_provider) as client:
            # Path traversal attempt
            with pytest.raises(ValueError, match="Invalid sys_id"):
                await client.get_record("incident", "../secrets/admin")
            # URL-encoded junk
            with pytest.raises(ValueError, match="Invalid sys_id"):
                await client.get_record("incident", "%2E%2E%2Fadmin")
            # Uppercase hex (valid length, invalid casing)
            with pytest.raises(ValueError, match="Invalid sys_id"):
                await client.get_record("incident", "A" * 32)
            # 33-char hex (one over)
            with pytest.raises(ValueError, match="Invalid sys_id"):
                await client.get_record("incident", "a" * 33)


class TestServiceNowClientQueryRecords:
    """Test query_records method."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_query_records_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns list of matching records."""
        from servicenow_mcp.client import ServiceNowClient

        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {"sys_id": "1", "number": "INC0001"},
                        {"sys_id": "2", "number": "INC0002"},
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records("incident", "active=true")

        assert len(result["records"]) == 2
        assert result["count"] == 2

    @pytest.mark.asyncio()
    @respx.mock
    async def test_query_records_with_limit_and_offset(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Passes limit and offset parameters."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            await client.query_records("incident", "active=true", limit=10, offset=20)

        assert route.calls.last is not None
        url = str(route.calls.last.request.url)
        assert "sysparm_limit=10" in url
        assert "sysparm_offset=20" in url

    @pytest.mark.asyncio()
    @respx.mock
    async def test_query_records_with_order_by(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Passes order_by parameter."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            await client.query_records("incident", "active=true", order_by="sys_created_on")

        assert route.calls.last is not None
        url = str(route.calls.last.request.url)
        assert "sysparm_orderby" in url

    @pytest.mark.asyncio()
    @respx.mock
    async def test_query_records_empty_results(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Handles empty result set."""
        from servicenow_mcp.client import ServiceNowClient

        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records("incident", "number=NONEXISTENT")

        assert result["records"] == []
        assert result["count"] == 0

    @pytest.mark.asyncio()
    @respx.mock
    async def test_query_records_with_display_values(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Passes display_value parameter when display_values=True."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            await client.query_records("incident", "active=true", display_values=True)

        assert route.calls.last is not None
        assert "sysparm_display_value=true" in str(route.calls.last.request.url)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_query_records_without_display_values(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Omits display_value param when display_values is False."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            await client.query_records("incident", "active=true", display_values=False)

        assert route.calls.last is not None
        assert "sysparm_display_value" not in str(route.calls.last.request.url)


class TestGetRecordsPrivileged:
    """Test get_records_privileged - the allowlist-gated bypass for DENIED_TABLES.

    This path is only meant for investigation modules that own the decision
    to read a hardened table (e.g. acl_conflicts -> sys_security_acl). The
    caller-declared allowlist is the gate: any table outside it - including
    tables that are NOT on DENIED_TABLES - is rejected so mistakes fail closed.
    """

    @pytest.mark.asyncio()
    @respx.mock
    async def test_allowed_table_returns_masked_records(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Allowed table returns records with sensitive fields masked."""
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.policy import MASK_VALUE

        respx.get(f"{BASE_URL}/api/now/table/sys_security_acl").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "sys_id": "a" * 32,
                            "name": "incident",
                            "operation": "read",
                            "password": "hunter2",
                        },
                    ]
                },
                headers={"X-Total-Count": "1"},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.get_records_privileged(
                "sys_security_acl",
                allowed_tables=frozenset({"sys_security_acl"}),
                query="name=incident",
                fields="sys_id,name,operation,password",
                limit=10,
            )

        assert len(result["records"]) == 1
        record = result["records"][0]
        assert record["name"] == "incident"
        assert record["password"] == MASK_VALUE

    @pytest.mark.asyncio()
    async def test_denied_table_outside_allowlist_raises(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """A DENIED_TABLES entry not in the allowlist is rejected."""
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.errors import PolicyError

        async with ServiceNowClient(settings, auth_provider) as client:
            # sys_credentials is on DENIED_TABLES but not in the caller's allowlist.
            with pytest.raises(PolicyError, match="not in the caller's"):
                await client.get_records_privileged(
                    "sys_credentials",
                    allowed_tables=frozenset({"sys_security_acl"}),
                    query="",
                    limit=1,
                )

    @pytest.mark.asyncio()
    async def test_non_denied_table_outside_allowlist_raises(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Even a benign (non-denied) table is rejected if not in the allowlist.

        The allowlist is the gate - not DENIED_TABLES membership - so a typo
        or accidental call path cannot silently succeed.
        """
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.errors import PolicyError

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(PolicyError, match="not in the caller's"):
                await client.get_records_privileged(
                    "incident",
                    allowed_tables=frozenset({"sys_security_acl"}),
                    query="",
                    limit=1,
                )

    @pytest.mark.asyncio()
    async def test_invalid_identifier_raises(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Malformed table names are rejected by validate_identifier via _table_url."""
        from servicenow_mcp.client import ServiceNowClient

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ValueError, match="Invalid identifier"):
                await client.get_records_privileged(
                    "sys_security_acl; DROP TABLE users",
                    allowed_tables=frozenset({"sys_security_acl; DROP TABLE users"}),
                    query="",
                    limit=1,
                )

    @pytest.mark.asyncio()
    @respx.mock
    async def test_display_values_flag_propagates(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """display_values=True adds sysparm_display_value=true to the request."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/now/table/sys_security_acl").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            await client.get_records_privileged(
                "sys_security_acl",
                allowed_tables=frozenset({"sys_security_acl"}),
                display_values=True,
            )

        assert route.calls.last is not None
        assert "sysparm_display_value=true" in str(route.calls.last.request.url)


class TestServiceNowClientAttachmentMethods:
    """Test attachment-specific client helpers."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_attachments_with_query_offset_and_order_by(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Builds attachment list params with filters, offset, and ordering."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/now/attachment").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "invalid"})
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            client_any: Any = client
            result = await client_any.list_attachments(
                query="table_name=incident", limit=10, offset=5, order_by="sys_id"
            )

        assert result == {"records": [], "count": 0}
        assert route.calls.last is not None
        params = route.calls.last.request.url.params
        assert params["sysparm_limit"] == "10"
        assert params["sysparm_offset"] == "5"
        assert params["sysparm_query"] == "table_name=incident^ORDERBYsys_id"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_attachments_with_order_by_only(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Builds attachment ordering query even without a base filter."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/now/attachment").mock(
            return_value=httpx.Response(200, json={"result": []}, headers={"X-Total-Count": "0"})
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            client_any: Any = client
            await client_any.list_attachments(query="", order_by="sys_created_on")

        assert route.calls.last is not None
        params = route.calls.last.request.url.params
        assert "sysparm_offset" not in params
        assert params["sysparm_query"] == "ORDERBYsys_created_on"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_upload_attachment_omits_optional_params_when_none(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Leaves optional upload params out when callers pass None."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.post(f"{BASE_URL}/api/now/attachment/file").mock(
            return_value=httpx.Response(201, json={"result": {"sys_id": "abc"}})
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            client_any: Any = client
            await client_any.upload_attachment(
                table_name="incident",
                table_sys_id="b" * 32,
                file_name="hello.txt",
                content=b"hello",
                encryption_context=None,
                creation_time=None,
            )

        assert route.calls.last is not None
        params = route.calls.last.request.url.params
        assert "encryption_context" not in params
        assert "creation_time" not in params

    @pytest.mark.asyncio()
    @respx.mock
    async def test_upload_attachment_includes_optional_params_when_present(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Passes optional upload params through when callers provide them."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.post(f"{BASE_URL}/api/now/attachment/file").mock(
            return_value=httpx.Response(201, json={"result": {"sys_id": "abc"}})
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

    @pytest.mark.asyncio()
    @respx.mock
    async def test_download_attachment_by_name_success(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Downloads attachment content by record sys_id and file name."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/now/attachment/{'b' * 32}/hello%20world.txt/file").mock(
            return_value=httpx.Response(200, content=b"hello")
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            content = await client.download_attachment_by_name("b" * 32, "hello world.txt")

        assert content == b"hello"
        assert route.called


class TestServiceNowClientGetMetadata:
    """Test get_metadata method."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_metadata_queries_sys_dictionary(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Queries sys_dictionary for the given table."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/now/table/sys_dictionary").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "element": "number",
                            "internal_type": "string",
                            "max_length": "40",
                        }
                    ]
                },
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            await client.get_metadata("incident")

        assert route.called
        assert route.calls.last is not None
        url = str(route.calls.last.request.url)
        assert "name%3Dincident" in url


class TestServiceNowClientAggregate:
    """Test aggregate method."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_aggregate_count(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Performs aggregate query for counts."""
        from servicenow_mcp.client import ServiceNowClient

        respx.get(f"{BASE_URL}/api/now/stats/incident").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "stats": {"count": "42"},
                    }
                },
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.aggregate("incident", "active=true")

        assert result is not None

    @pytest.mark.asyncio()
    @respx.mock
    async def test_aggregate_with_field_specific_stats(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Passes field-specific avg/min/max/sum params."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/now/stats/incident").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"stats": {"count": "10", "avg": {"priority": "2.5"}}}},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            await client.aggregate(
                "incident",
                "active=true",
                avg_fields=["priority", "impact"],
                min_fields=["priority"],
                max_fields=["impact"],
                sum_fields=["reassignment_count"],
            )

        assert route.calls.last is not None
        url = str(route.calls.last.request.url)
        assert "sysparm_avg_fields" in url
        assert "sysparm_min_fields" in url
        assert "sysparm_max_fields" in url
        assert "sysparm_sum_fields" in url

    @pytest.mark.asyncio()
    @respx.mock
    async def test_aggregate_with_having_and_order_by(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Passes having and order_by parameters."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/now/stats/incident").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"stats": {"count": "5"}}},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            await client.aggregate(
                "incident",
                "active=true",
                group_by="priority",
                order_by="count",
                having="count>5",
            )

        assert route.calls.last is not None
        url = str(route.calls.last.request.url)
        assert "sysparm_orderby" in url
        assert "sysparm_having" in url

    @pytest.mark.asyncio()
    @respx.mock
    async def test_aggregate_with_display_value(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Passes display_value parameter."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/now/stats/incident").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"stats": {"count": "5"}}},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            await client.aggregate(
                "incident",
                "active=true",
                display_value=True,
            )

        assert route.calls.last is not None
        url = str(route.calls.last.request.url)
        assert "sysparm_display_value=true" in url


class TestServiceNowClientCreateRecord:
    """Test create_record method."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_create_record_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Creates a record via POST."""
        from servicenow_mcp.client import ServiceNowClient

        respx.post(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                201,
                json={"result": {"sys_id": "bdb8465ce041d94a0e490564f2162dcc", "number": "INC0099"}},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            record = await client.create_record("incident", {"short_description": "Test incident"})

        assert record["sys_id"] == "bdb8465ce041d94a0e490564f2162dcc"


class TestServiceNowClientUpdateRecord:
    """Test update_record method."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_update_record_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Updates a record via PATCH."""
        from servicenow_mcp.client import ServiceNowClient

        respx.patch(f"{BASE_URL}/api/now/table/incident/6367c48dd193d56ea7b0baad25b19455").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": "6367c48dd193d56ea7b0baad25b19455", "state": "2"}},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            record = await client.update_record("incident", "6367c48dd193d56ea7b0baad25b19455", {"state": "2"})

        assert record["state"] == "2"


class TestServiceNowClientDeleteRecord:
    """Test delete_record method."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_delete_record_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Deletes a record via DELETE."""
        from servicenow_mcp.client import ServiceNowClient

        respx.delete(f"{BASE_URL}/api/now/table/incident/6367c48dd193d56ea7b0baad25b19455").mock(return_value=httpx.Response(204))

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.delete_record("incident", "6367c48dd193d56ea7b0baad25b19455")

        assert result is True


class TestServiceNowClientErrorHandling:
    """Test error mapping for various HTTP status codes."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_403_raises_forbidden_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """403 maps to ForbiddenError."""
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.errors import ForbiddenError

        respx.get(f"{BASE_URL}/api/now/table/incident/6367c48dd193d56ea7b0baad25b19455").mock(
            return_value=httpx.Response(403, json={"error": {"message": "Forbidden"}})
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ForbiddenError):
                await client.get_record("incident", "6367c48dd193d56ea7b0baad25b19455")

    @pytest.mark.asyncio()
    @respx.mock
    async def test_500_raises_server_error(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """500 maps to ServerError."""
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.errors import ServerError

        respx.get(f"{BASE_URL}/api/now/table/incident/6367c48dd193d56ea7b0baad25b19455").mock(
            return_value=httpx.Response(500, json={"error": {"message": "Internal"}})
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServerError):
                await client.get_record("incident", "6367c48dd193d56ea7b0baad25b19455")


class TestServiceNowClientCorrelationId:
    """Test that correlation ID is included in requests."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_correlation_id_in_headers(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Every request includes an X-Correlation-ID header."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/now/table/incident/6367c48dd193d56ea7b0baad25b19455").mock(
            return_value=httpx.Response(200, json={"result": {"sys_id": "6367c48dd193d56ea7b0baad25b19455"}})
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            await client.get_record("incident", "6367c48dd193d56ea7b0baad25b19455")

        assert route.calls.last is not None
        request_headers = dict(route.calls.last.request.headers)
        assert "x-correlation-id" in request_headers


class TestServiceNowClientGetEmail:
    """Test get_email method."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_email_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Fetches an email record by ID."""
        from servicenow_mcp.client import ServiceNowClient

        respx.get(f"{BASE_URL}/api/now/v1/email/285b9ff22fbce171a68a2b88194bf4c9").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "285b9ff22fbce171a68a2b88194bf4c9",
                        "subject": "Test email",
                        "type": "send",
                    }
                },
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.get_email("285b9ff22fbce171a68a2b88194bf4c9")

        assert result["sys_id"] == "285b9ff22fbce171a68a2b88194bf4c9"
        assert result["subject"] == "Test email"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_email_with_fields(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Passes sysparm_fields parameter."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/now/v1/email/285b9ff22fbce171a68a2b88194bf4c9").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"sys_id": "285b9ff22fbce171a68a2b88194bf4c9"}},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            await client.get_email("285b9ff22fbce171a68a2b88194bf4c9", fields=["sys_id", "subject"])

        assert route.calls.last is not None
        assert "sysparm_fields" in str(route.calls.last.request.url)


class TestServiceNowClientGetImportSetRecord:
    """Test get_import_set_record method."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_import_set_record_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Retrieves an import set record."""
        from servicenow_mcp.client import ServiceNowClient

        respx.get(f"{BASE_URL}/api/now/import/u_staging_table/aa97905375e489695050579dce8b0ab2").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "aa97905375e489695050579dce8b0ab2",
                        "import_set": "IMP001",
                        "status": "inserted",
                    }
                },
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.get_import_set_record("u_staging_table", "aa97905375e489695050579dce8b0ab2")

        assert result["sys_id"] == "aa97905375e489695050579dce8b0ab2"
        assert result["status"] == "inserted"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_import_set_record_not_found(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Raises NotFoundError for missing import set record."""
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.errors import NotFoundError

        missing_sys_id = "00000000000000000000000000000000"
        respx.get(f"{BASE_URL}/api/now/import/u_staging_table/{missing_sys_id}").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(NotFoundError):
                await client.get_import_set_record("u_staging_table", missing_sys_id)


class TestServiceNowClientReportingAPIs:
    """Test reporting API methods."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_reports_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns a list of reports."""
        from servicenow_mcp.client import ServiceNowClient

        respx.get(f"{BASE_URL}/api/now/reporting").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {"sys_id": "7ed1b3474f1c2b181abf7930a484c423", "title": "Incident Report"},
                        {"sys_id": "bb6d7f12edfd300254797a296ee73d31", "title": "Change Report"},
                    ]
                },
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.list_reports()

        assert len(result) == 2
        assert result[0]["title"] == "Incident Report"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_list_reports_with_params(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Passes search, sort, and pagination parameters."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/now/reporting").mock(return_value=httpx.Response(200, json={"result": []}))

        async with ServiceNowClient(settings, auth_provider) as client:
            await client.list_reports(
                search="incident",
                sort_by="title",
                sort_dir="asc",
                page=2,
                per_page=25,
            )

        assert route.calls.last is not None
        url = str(route.calls.last.request.url)
        assert "sysparm_contains" in url
        assert "sysparm_sortby" in url
        assert "sysparm_sortdir" in url
        assert "sysparm_page" in url
        assert "sysparm_per_page" in url

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_table_description_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns table description."""
        from servicenow_mcp.client import ServiceNowClient

        respx.get(f"{BASE_URL}/api/now/reporting_table_description/incident").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"label": "Incident", "name": "incident"}},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.get_table_description("incident")

        assert result["label"] == "Incident"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_field_descriptions_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns field descriptions for a table."""
        from servicenow_mcp.client import ServiceNowClient

        respx.get(f"{BASE_URL}/api/now/reporting_table_description/field_description/incident").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {"name": "number", "label": "Number"},
                        {"name": "state", "label": "State"},
                    ]
                },
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.get_field_descriptions("incident")

        assert len(result) == 2
        assert result[0]["label"] == "Number"


class TestServiceNowClientCodeSearch:
    """Test Code Search API methods."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_code_search_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Performs code search and returns results."""
        from servicenow_mcp.client import ServiceNowClient

        respx.get(f"{BASE_URL}/api/sn_codesearch/code_search/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "search_results": [
                            {
                                "className": "sys_script_include",
                                "name": "TestUtil",
                                "match": "AbstractAjaxProcessor",
                            }
                        ]
                    }
                },
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.code_search("AbstractAjaxProcessor")

        assert result is not None

    @pytest.mark.asyncio()
    @respx.mock
    async def test_code_search_with_table_and_group(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Passes table and search_group parameters."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/sn_codesearch/code_search/search").mock(
            return_value=httpx.Response(200, json={"result": {}})
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            await client.code_search(
                "myFunction",
                table="sys_script_include",
                search_group="sn_codesearch.Default Search Group",
            )

        assert route.calls.last is not None
        url = str(route.calls.last.request.url)
        assert "table" in url
        assert "search_group" in url

    @pytest.mark.asyncio()
    @respx.mock
    async def test_code_search_with_limit(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Passes limit parameter."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/sn_codesearch/code_search/search").mock(
            return_value=httpx.Response(200, json={"result": {}})
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            await client.code_search("myFunction", limit=50)

        assert route.calls.last is not None
        url = str(route.calls.last.request.url)
        assert "limit=50" in url

    @pytest.mark.asyncio()
    @respx.mock
    async def test_code_search_tables_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Returns list of searchable tables."""
        from servicenow_mcp.client import ServiceNowClient

        respx.get(f"{BASE_URL}/api/sn_codesearch/code_search/tables").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "tables": [
                            {"name": "sys_script_include"},
                            {"name": "sys_script"},
                        ]
                    }
                },
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.code_search_tables()

        assert result is not None


class TestServiceNowClientCMDB:
    """Test CMDB API methods."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_cmdb_query_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Queries CMDB instances for a class."""
        from servicenow_mcp.client import ServiceNowClient

        respx.get(f"{BASE_URL}/api/now/cmdb/instance/cmdb_ci_linux_server").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {"sys_id": "d5f759f849bd82f4d43947407ffce511", "name": "365c544c62cbe6bb0b789bdb702b190e"},
                        {"sys_id": "6f7aa3d044bfc90517430647fcbddac7", "name": "a9b964002520dfa399588247c37460c8"},
                    ]
                },
                headers={"X-Total-Count": "2"},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.cmdb_query("cmdb_ci_linux_server")

        assert len(result["records"]) == 2
        assert result["count"] == 2

    @pytest.mark.asyncio()
    @respx.mock
    async def test_cmdb_query_with_params(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Passes query, limit, and offset parameters."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/now/cmdb/instance/cmdb_ci_linux_server").mock(
            return_value=httpx.Response(
                200,
                json={"result": []},
                headers={"X-Total-Count": "0"},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            await client.cmdb_query(
                "cmdb_ci_linux_server",
                query="name=365c544c62cbe6bb0b789bdb702b190e",
                limit=10,
                offset=5,
            )

        assert route.calls.last is not None
        url = str(route.calls.last.request.url)
        assert "sysparm_query" in url
        assert "sysparm_limit=10" in url
        assert "sysparm_offset=5" in url

    @pytest.mark.asyncio()
    @respx.mock
    async def test_cmdb_get_instance_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Retrieves a CMDB CI with relationships."""
        from servicenow_mcp.client import ServiceNowClient

        respx.get(f"{BASE_URL}/api/now/cmdb/instance/cmdb_ci_linux_server/73767563ce47fbef89395fe3a44f456a").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "attributes": {"sys_id": "73767563ce47fbef89395fe3a44f456a", "name": "365c544c62cbe6bb0b789bdb702b190e"},
                        "inbound_relations": [],
                        "outbound_relations": [],
                    }
                },
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.cmdb_get_instance("cmdb_ci_linux_server", "73767563ce47fbef89395fe3a44f456a")

        assert result["attributes"]["sys_id"] == "73767563ce47fbef89395fe3a44f456a"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_cmdb_get_instance_not_found(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Raises NotFoundError for missing CI."""
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.errors import NotFoundError

        missing_sys_id = "00000000000000000000000000000000"
        respx.get(f"{BASE_URL}/api/now/cmdb/instance/cmdb_ci_linux_server/{missing_sys_id}").mock(
            return_value=httpx.Response(404, json={"error": {"message": "Not found"}})
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(NotFoundError):
                await client.cmdb_get_instance("cmdb_ci_linux_server", missing_sys_id)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_cmdb_get_meta_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Retrieves CMDB class metadata."""
        from servicenow_mcp.client import ServiceNowClient

        respx.get(f"{BASE_URL}/api/now/cmdb/meta/cmdb_ci_linux_server").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": {
                        "name": "cmdb_ci_linux_server",
                        "label": "Linux Server",
                        "attributes": [],
                    }
                },
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.cmdb_get_meta("cmdb_ci_linux_server")

        assert result["name"] == "cmdb_ci_linux_server"


class TestServiceNowClientEncodedQueryTranslator:
    """Test encoded query translator method."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_translate_encoded_query_success(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Translates encoded query to human-readable form."""
        from servicenow_mcp.client import ServiceNowClient

        respx.get(f"{BASE_URL}/api/now/cmdb_workspace_api/encodedquery").mock(
            return_value=httpx.Response(
                200,
                json={"result": {"display_value": "Name contains linux OR Name contains lin"}},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.translate_encoded_query("cmdb_ci_linux_server", "nameLIKElnux^ORnameLIKElin")

        assert result is not None

    @pytest.mark.asyncio()
    @respx.mock
    async def test_translate_encoded_query_passes_params(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Passes table and query parameters correctly."""
        from servicenow_mcp.client import ServiceNowClient

        route = respx.get(f"{BASE_URL}/api/now/cmdb_workspace_api/encodedquery").mock(
            return_value=httpx.Response(200, json={"result": {"display_value": "Active is true"}})
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            await client.translate_encoded_query("incident", "active=true")

        assert route.calls.last is not None
        url = str(route.calls.last.request.url)
        assert "table" in url
        assert "query" in url


class TestClientNotInitialized:
    """Test that calling methods without async with context raises RuntimeError."""

    @pytest.mark.asyncio()
    async def test_get_record_without_context_manager(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """get_record raises RuntimeError when client is not initialized."""
        from servicenow_mcp.client import ServiceNowClient

        client = ServiceNowClient(settings, auth_provider)
        with pytest.raises(RuntimeError, match="Client not initialized"):
            await client.get_record("incident", "6367c48dd193d56ea7b0baad25b19455")

    @pytest.mark.asyncio()
    async def test_query_records_without_context_manager(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """query_records raises RuntimeError when client is not initialized."""
        from servicenow_mcp.client import ServiceNowClient

        client = ServiceNowClient(settings, auth_provider)
        with pytest.raises(RuntimeError, match="Client not initialized"):
            await client.query_records("incident", "active=true")


class TestMissingResultKey:
    """Test that missing 'result' key in API response raises ServerError."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_record_missing_result_key(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """ServerError raised when API response lacks 'result' key."""
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.errors import ServerError

        respx.get(f"{BASE_URL}/api/now/table/incident/6367c48dd193d56ea7b0baad25b19455").mock(
            return_value=httpx.Response(
                200,
                json={"error": "something went wrong"},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServerError, match="missing 'result' key"):
                await client.get_record("incident", "6367c48dd193d56ea7b0baad25b19455")

    @pytest.mark.asyncio()
    @respx.mock
    async def test_query_records_missing_result_key(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """ServerError raised when query response lacks 'result' key."""
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.errors import ServerError

        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                200,
                json={"data": []},
                headers={"X-Total-Count": "0"},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServerError, match="missing 'result' key"):
                await client.query_records("incident", "active=true")


class TestInvalidTotalCount:
    """Test that invalid X-Total-Count header defaults to 0."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_query_records_invalid_total_count(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """Non-numeric X-Total-Count defaults to 0."""
        from servicenow_mcp.client import ServiceNowClient

        respx.get(f"{BASE_URL}/api/now/table/incident").mock(
            return_value=httpx.Response(
                200,
                json={"result": [{"sys_id": "1"}]},
                headers={"X-Total-Count": "invalid"},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.query_records("incident", "active=true")

        assert result["count"] == 0
        assert len(result["records"]) == 1

    @pytest.mark.asyncio()
    @respx.mock
    async def test_cmdb_query_invalid_total_count(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """Non-numeric X-Total-Count in CMDB query defaults to 0."""
        from servicenow_mcp.client import ServiceNowClient

        respx.get(f"{BASE_URL}/api/now/cmdb/instance/cmdb_ci_linux_server").mock(
            return_value=httpx.Response(
                200,
                json={"result": [{"sys_id": "d5f759f849bd82f4d43947407ffce511"}]},
                headers={"X-Total-Count": "not_a_number"},
            )
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.cmdb_query("cmdb_ci_linux_server")

        assert result["count"] == 0
        assert len(result["records"]) == 1


class TestUrlBuilderValidation:
    """Test that URL builder methods validate identifiers."""

    def test_table_url_rejects_invalid_name(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """_table_url raises ValueError for invalid table name."""
        from servicenow_mcp.client import ServiceNowClient

        client = ServiceNowClient(settings, auth_provider)
        with pytest.raises(ValueError, match="Invalid identifier"):
            client._table_url("INVALID-TABLE!")

    def test_stats_url_rejects_invalid_name(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """_stats_url raises ValueError for invalid table name."""
        from servicenow_mcp.client import ServiceNowClient

        client = ServiceNowClient(settings, auth_provider)
        with pytest.raises(ValueError, match="Invalid identifier"):
            client._stats_url("bad/table")

    def test_import_set_url_rejects_invalid_name(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """_import_set_url raises ValueError for invalid staging table name."""
        from servicenow_mcp.client import ServiceNowClient

        client = ServiceNowClient(settings, auth_provider)
        with pytest.raises(ValueError, match="Invalid identifier"):
            client._import_set_url("../etc/passwd", "6367c48dd193d56ea7b0baad25b19455")

    def test_cmdb_instance_url_rejects_invalid_name(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """_cmdb_instance_url raises ValueError for invalid class name."""
        from servicenow_mcp.client import ServiceNowClient

        client = ServiceNowClient(settings, auth_provider)
        with pytest.raises(ValueError, match="Invalid identifier"):
            client._cmdb_instance_url("INVALID-CLASS!")

    def test_cmdb_meta_url_rejects_invalid_name(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """_cmdb_meta_url raises ValueError for invalid class name."""
        from servicenow_mcp.client import ServiceNowClient

        client = ServiceNowClient(settings, auth_provider)
        with pytest.raises(ValueError, match="Invalid identifier"):
            client._cmdb_meta_url("bad class")

    def test_table_description_url_rejects_invalid_name(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """_table_description_url raises ValueError for invalid table name."""
        from servicenow_mcp.client import ServiceNowClient

        client = ServiceNowClient(settings, auth_provider)
        with pytest.raises(ValueError, match="Invalid identifier"):
            client._table_description_url("bad-table")

    def test_field_descriptions_url_rejects_invalid_name(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """_field_descriptions_url raises ValueError for invalid table name."""
        from servicenow_mcp.client import ServiceNowClient

        client = ServiceNowClient(settings, auth_provider)
        with pytest.raises(ValueError, match="Invalid identifier"):
            client._field_descriptions_url("bad-table")

    def test_table_url_accepts_valid_name(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """_table_url accepts valid snake_case table names."""
        from servicenow_mcp.client import ServiceNowClient

        client = ServiceNowClient(settings, auth_provider)
        url = client._table_url("incident")
        assert "incident" in url

    def test_attachment_url_rejects_invalid_sys_id(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """_attachment_url raises ValueError for invalid attachment sys_id."""
        from servicenow_mcp.client import ServiceNowClient

        client = ServiceNowClient(settings, auth_provider)
        with pytest.raises(ValueError, match="Invalid sys_id"):
            client._attachment_url("invalid-sys-id")

    def test_attachment_by_name_url_rejects_invalid_table_sys_id(
        self, settings: Settings, auth_provider: BasicAuthProvider
    ) -> None:
        """_attachment_file_by_name_url raises ValueError for invalid table_sys_id."""
        from servicenow_mcp.client import ServiceNowClient

        client = ServiceNowClient(settings, auth_provider)
        with pytest.raises(ValueError, match="Invalid sys_id"):
            client._attachment_file_by_name_url("bad-id", "hello.txt")


class TestATFCloudRunner404:
    """Test ATF Cloud Runner methods raise NotFoundError with plugin hint on 404."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_atf_run_not_found(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """404 from atf_run raises NotFoundError with plugin hint."""
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.errors import NotFoundError

        respx.post(f"{BASE_URL}/api/now/sn_atf_tg/test_runner").mock(
            return_value=httpx.Response(404),
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(NotFoundError, match="ATF Cloud Runner"):
                await client.atf_run("test_sys_id_123")

    @pytest.mark.asyncio()
    @respx.mock
    async def test_atf_progress_not_found(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """404 from atf_progress raises NotFoundError with plugin hint."""
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.errors import NotFoundError

        respx.get(f"{BASE_URL}/api/now/sn_atf_tg/test_runner_progress").mock(
            return_value=httpx.Response(404),
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(NotFoundError, match="ATF Cloud Runner"):
                await client.atf_progress("snboq_id_123")

    @pytest.mark.asyncio()
    @respx.mock
    async def test_atf_cancel_not_found(self, settings: Settings, auth_provider: BasicAuthProvider) -> None:
        """404 from atf_cancel raises NotFoundError with plugin hint."""
        from servicenow_mcp.client import ServiceNowClient
        from servicenow_mcp.errors import NotFoundError

        respx.post(f"{BASE_URL}/api/now/sn_atf_tg/cancel_test_runner").mock(
            return_value=httpx.Response(404),
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(NotFoundError, match="ATF Cloud Runner"):
                await client.atf_cancel("snboq_id_123")
