"""Grouped audit counts preserve results while avoiding repeated table scans."""

from typing import Any

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.client import ServiceNowClient
from servicenow_mcp.config import Settings
from servicenow_mcp.errors import ServiceNowMCPError
from servicenow_mcp.tools._audit_counts import fetch_field_counts
from tests._mock_transport import http_mock


STATS_URL = "https://test.service-now.com/api/now/stats/sys_audit"


def _group(name: str, count: Any) -> dict[str, Any]:
    return {"groupby_fields": [{"field": "fieldname", "value": name}], "stats": {"count": count}}


async def test_batch_counts_use_one_bounded_request_and_remain_fresh(settings: Settings) -> None:
    fields = [f"field_{index}" for index in range(50)]
    route = http_mock.get(STATS_URL).mock(
        side_effect=[
            httpx2.Response(200, json={"result": [_group(fields[-1], "7"), _group(fields[0], 3)]}),
            httpx2.Response(200, json={"result": [_group(fields[0], "4")]}),
        ],
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        first = await fetch_field_counts(client, table="incident", fields=fields, since="2026-09-17")
        second = await fetch_field_counts(client, table="incident", fields=fields, since="2026-09-17")

    assert first == dict.fromkeys(fields, 0) | {fields[0]: 3, fields[-1]: 7}
    assert second == dict.fromkeys(fields, 0) | {fields[0]: 4}
    assert route.call_count == 2
    for call in route.calls:
        params = call.request.url.params
        assert params["sysparm_count"] == "true"
        assert params["sysparm_group_by"] == "fieldname"
        assert params["sysparm_query"] == (
            f"tablename=incident^fieldnameIN{','.join(fields)}^sys_created_on>=2026-09-17"
        )


async def test_no_confirmed_fields_skips_grouped_query(settings: Settings) -> None:
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        assert await fetch_field_counts(client, table="incident", fields=[], since="2026-09-17") == {}
    assert not http_mock.calls


async def test_empty_groups_mean_zero_activity(settings: Settings) -> None:
    http_mock.get(STATS_URL).respond(200, json={"result": []})
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        assert await fetch_field_counts(client, table="incident", fields=["state"], since="2026-09-17") == {
            "state": 0,
        }


@pytest.mark.parametrize(
    "result",
    [
        {},
        None,
        [None],
        [{}],
        [{"groupby_fields": []}],
        [{"groupby_fields": [None]}],
        [{"groupby_fields": [{"field": "wrong_field", "value": "state"}]}],
        [{"groupby_fields": [{"field": "fieldname", "value": "state"}]}],
        [_group("unexpected_field", "1")],
        [_group("state", "1"), _group("state", "2")],
        [_group("state", "not-a-number")],
        [_group("state", -1)],
        [_group("state", True)],
        [_group("state", 1.5)],
        [_group("state", None)],
    ],
)
async def test_malformed_groups_fail_instead_of_reporting_inactivity(settings: Settings, result: Any) -> None:
    http_mock.get(STATS_URL).respond(200, json={"result": result})
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(ServiceNowMCPError, match="sys_audit Stats API"):
            await fetch_field_counts(client, table="incident", fields=["state"], since="2026-09-17")


async def test_grouped_timeout_propagates_without_retry(settings: Settings) -> None:
    route = http_mock.get(STATS_URL).mock(side_effect=httpx2.ReadTimeout("upstream timeout"))
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(httpx2.ReadTimeout):
            await fetch_field_counts(client, table="incident", fields=["state"], since="2026-09-17")
    assert route.call_count == 1
