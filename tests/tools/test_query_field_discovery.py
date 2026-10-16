"""Query schema discovery preserves inactive fields and incomplete visibility."""

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import patch

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.records._query_preparation import validate_query_fields
from servicenow_mcp.platform.metadata._dictionary import DictionaryRegistry
from servicenow_mcp.platform.metadata._dictionary_inheritance import _MAX_CHAIN_DEPTH
from servicenow_mcp.policy import DENIED_TABLES
from tests._mock_transport import http_mock

if TYPE_CHECKING:
    from pydantic import JsonValue

    from servicenow_mcp.config import Settings


BASE = "https://test.service-now.com/api/now/table"
OBJECTS = f"{BASE}/sys_db_object"
DICTIONARY = f"{BASE}/sys_dictionary"


def registry(settings: Settings) -> DictionaryRegistry:
    return DictionaryRegistry(settings, OAuthPKCEProvider(settings))


def root_tables(parents: dict[str, str] | None = None) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        table = request.url.params["sysparm_query"].removeprefix("name=")
        parent = (parents or {}).get(table, "")
        return httpx2.Response(
            200, json={"result": [{"super_class": parent, "super_class.name": parent}]}
        )

    http_mock.get(OBJECTS).mock(side_effect=respond)


def fields_by_table(definitions: dict[str, list[dict[str, JsonValue]]]) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        query = request.url.params["sysparm_query"].split("^ORDERBY", 1)[0]
        table, selection = query.split("^elementIN")
        names = selection.split(",")
        assert "^active=true" not in query
        assert (
            request.url.params["sysparm_fields"]
            == "element,internal_type.name,reference"
        )
        rows = [
            row
            for row in definitions.get(table.removeprefix("name="), [])
            if row.get("element") in names
        ]
        offset = int(request.url.params.get("sysparm_offset", 0))
        limit = int(request.url.params["sysparm_limit"])
        return httpx2.Response(
            200,
            json={"result": rows[offset : offset + limit]},
            headers={"X-Total-Count": str(len(rows))},
        )

    http_mock.get(DICTIONARY).mock(side_effect=respond)


def column(
    name: str,
    kind: str = "string",
    reference: str | dict[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    return {"element": name, "internal_type.name": kind, "reference": reference}


async def test_inactive_field_is_queryable_without_changing_active_discovery(
    settings: Settings,
) -> None:
    root_tables()
    inactive = {**column("u_retired"), "active": "false"}

    def respond(request: httpx2.Request) -> httpx2.Response:
        rows = (
            [] if "^active=true" in request.url.params["sysparm_query"] else [inactive]
        )
        return httpx2.Response(200, json={"result": rows})

    http_mock.get(DICTIONARY).mock(side_effect=respond)
    metadata = registry(settings)
    assert await metadata.get_fields("incident", ["u_retired"]) == []
    result = await metadata.get_query_field_paths("incident", ["u_retired"])
    assert result[0].status == "valid"
    assert result[0].field is not None
    assert result[0].field.metadata["active"] == "false"
    assert await validate_query_fields("incident", "u_retired=one", metadata) == []


async def test_empty_discovery_does_not_query_metadata(settings: Settings) -> None:
    metadata = registry(settings)
    assert (await metadata.get_query_fields("incident", [])).fields == []
    assert await metadata.get_query_field_paths("incident", []) == []
    assert not http_mock.calls


async def test_inherited_fields_and_child_precedence(settings: Settings) -> None:
    root_tables({"incident": "task"})
    fields_by_table(
        {
            "incident": [column("overridden", "reference", "sys_user")],
            "task": [column("overridden"), column("priority", "integer")],
        },
    )
    result = await registry(settings).get_query_fields(
        "incident", ["overridden", "priority"]
    )
    assert result.inheritance.tables == ["incident", "task"]
    assert result.inheritance.complete
    assert [
        (field.name, field.inherited_from, field.internal_type)
        for field in result.fields
    ] == [
        ("overridden", None, "reference"),
        ("priority", "task", "integer"),
    ]


async def test_query_columns_are_batched_and_stop_when_resolved(
    settings: Settings,
) -> None:
    root_tables({"incident": "task"})
    names = [f"field_{index}" for index in range(205)]
    fields_by_table({"incident": [column(name) for name in names]})
    result = await registry(settings).get_query_fields("incident", names)
    assert {field.name for field in result.fields} == set(names)
    route = http_mock.routes[-1]
    assert route.call_count == 3
    assert all(
        "name=incident^" in call.request.url.params["sysparm_query"]
        for call in route.calls
    )
    assert all(
        int(call.request.url.params["sysparm_limit"]) <= 101 for call in route.calls
    )


async def test_full_reference_paths_share_batched_lookups(settings: Settings) -> None:
    root_tables({"incident": "task"})
    fields_by_table(
        {
            "task": [
                column("assigned_to", "reference", "sys_user"),
                column("caller_id", "reference", {"value": "sys_user"}),
            ],
            "sys_user": [column("email"), column("name")],
        },
    )
    paths = ["assigned_to.email", "caller_id.email", "assigned_to.name"]
    metadata = registry(settings)
    result = await metadata.get_query_field_paths("incident", paths)
    assert [entry.path for entry in result] == paths
    assert all(entry.status == "valid" for entry in result)
    assert http_mock.routes[-1].call_count == 3
    before = len(http_mock.calls)
    assert await metadata.get_query_field_paths("incident", paths) == result
    assert len(http_mock.calls) == before
    assert (
        await validate_query_fields(
            "incident", "assigned_to.email=one^caller_id.email=two", metadata
        )
        == []
    )


async def test_known_reference_root_does_not_hide_an_unverified_terminal_field(
    settings: Settings,
) -> None:
    root_tables()
    fields_by_table(
        {
            "incident": [column("assigned_to", "reference", "sys_user")],
            "sys_user": [column("email")],
        },
    )
    warnings = await validate_query_fields(
        "incident", "assigned_to.missing=one", registry(settings)
    )
    assert len(warnings) == 1
    assert "assigned_to.missing" in warnings[0]
    assert "unverified" in warnings[0]
    assert "does not prove a field is invalid" in warnings[0]


@pytest.mark.parametrize(
    ("field", "path", "reason"),
    [
        (
            column("assigned_to", "reference", None),
            "assigned_to.email",
            "reference target",
        ),
        (
            column("assigned_to", "reference", "bad^target"),
            "assigned_to.email",
            "could not be resolved",
        ),
        (column("assigned_to", "document_id"), "assigned_to.email", "static reference"),
        (column("assigned_to", "string"), "assigned_to.email", "static reference"),
        (
            column("assigned_to", "reference", "sys_user"),
            "assigned_to.missing",
            "not visible",
        ),
    ],
)
async def test_unresolved_paths_remain_unverified(
    settings: Settings,
    field: dict[str, JsonValue],
    path: str,
    reason: str,
) -> None:
    root_tables()
    fields_by_table({"incident": [field], "sys_user": [column("email")]})
    result = (await registry(settings).get_query_field_paths("incident", [path]))[0]
    assert result.status == "unverified"
    assert result.reason is not None
    assert reason in result.reason


@pytest.mark.parametrize(
    "object_row",
    [
        None,
        {},
        {"super_class.name": ""},
        {"super_class": {"value": "a" * 32}},
        {"super_class": {"display_value": ""}},
    ],
)
async def test_missing_table_or_parent_metadata_is_not_a_root(
    settings: Settings,
    object_row: dict[str, JsonValue] | None,
) -> None:
    http_mock.get(OBJECTS).respond(
        200, json={"result": [] if object_row is None else [object_row]}
    )
    fields_by_table({})
    metadata = registry(settings)
    inheritance = await metadata.get_inheritance("incident")
    assert not inheritance.complete
    assert inheritance.reason in {"table_not_visible", "parent_not_visible"}
    result = (await metadata.get_query_field_paths("incident", ["missing"]))[0]
    assert result.status == "unverified"
    assert result.reason is not None
    assert "inheritance was incomplete" in result.reason.lower()


async def test_missing_field_stays_unverified_even_with_observed_root(
    settings: Settings,
) -> None:
    root_tables()
    fields_by_table({})
    metadata = registry(settings)
    lookup = await metadata.get_query_fields("incident", ["missing"])
    assert lookup.inheritance.complete
    assert lookup.fields == []
    assert (await metadata.get_query_field_paths("incident", ["missing"]))[
        0
    ].status == "unverified"


async def test_cycle_and_depth_limit_are_reported(settings: Settings) -> None:
    root_tables({"a": "b", "b": "a"})
    metadata = registry(settings)
    result = await metadata.get_inheritance("a")
    assert result.tables == ["a", "b"]
    assert result.reason == "inheritance_cycle"
    assert not result.complete
    root_tables({f"level{i}": f"level{i + 1}" for i in range(_MAX_CHAIN_DEPTH + 1)})
    result = await metadata.get_inheritance("level0")
    assert len(result.tables) == _MAX_CHAIN_DEPTH
    assert result.reason == "inheritance_depth_limit"


async def test_pagination_reaches_later_selected_fields(settings: Settings) -> None:
    root_tables()
    fields_by_table(
        {
            "incident": [
                column("alpha"),
                column("alpha"),
                column("alpha"),
                column("beta"),
            ],
        },
    )
    result = await registry(settings).get_query_fields("incident", ["alpha", "beta"])
    assert {field.name for field in result.fields} == {"alpha", "beta"}
    assert not result.limitations
    assert http_mock.routes[-1].call_count == 2
    assert http_mock.calls.last.request.url.params["sysparm_offset"] == "3"


async def test_acl_filtered_empty_page_does_not_stop_known_remaining_rows(
    settings: Settings,
) -> None:
    root_tables()

    def respond(request: httpx2.Request) -> httpx2.Response:
        offset = int(request.url.params.get("sysparm_offset", 0))
        return httpx2.Response(
            200,
            json={"result": [] if offset == 0 else [column("alpha")]},
            headers={"X-Total-Count": "3"},
        )

    http_mock.get(DICTIONARY).mock(side_effect=respond)
    result = await registry(settings).get_query_field_paths("incident", ["alpha"])
    assert result[0].status == "valid"
    assert http_mock.routes[-1].call_count == 2


async def test_bounded_dictionary_pages_do_not_claim_completeness(
    settings: Settings,
) -> None:
    root_tables()
    fields_by_table({"incident": [column("alpha")] * 10})
    with patch(
        "servicenow_mcp.platform.metadata._dictionary._QUERY_METADATA_ROW_LIMIT", 5
    ):
        metadata = registry(settings)
        result = await metadata.get_query_fields("incident", ["alpha", "beta"])
        assert result.limitations
        resolutions = await metadata.get_query_field_paths(
            "incident", ["alpha", "beta"]
        )
    assert all(value.status == "unverified" for value in resolutions)
    assert http_mock.routes[-1].call_count == 2
    assert (
        sum(
            int(call.request.url.params["sysparm_limit"])
            for call in http_mock.routes[-1].calls
        )
        == 5
    )


@pytest.mark.parametrize(
    "response", [{"result": [{"reference": "sys_user"}]}, {"result": {}}]
)
async def test_malformed_metadata_is_explicitly_incomplete(
    settings: Settings, response: dict[str, JsonValue]
) -> None:
    root_tables()
    http_mock.get(DICTIONARY).respond(200, json=response)
    result = await registry(settings).get_query_field_paths(
        "incident", ["assigned_to.email"]
    )
    assert result[0].status == "unverified"
    assert result[0].reason is not None
    assert "incomplete" in result[0].reason


@pytest.mark.parametrize(
    "failure",
    [
        httpx2.ReadTimeout("private marker"),
        httpx2.Response(403, json={"error": {"message": "private marker"}}),
    ],
)
async def test_reference_metadata_failure_does_not_erase_other_positive_evidence(
    settings: Settings,
    failure: httpx2.ReadTimeout | httpx2.Response,
) -> None:
    root_tables()
    fields_by_table(
        {
            "incident": [
                column("assigned_to", "reference", "sys_user"),
                column("short_description"),
            ],
        },
    )

    def respond(request: httpx2.Request) -> httpx2.Response:
        if request.url.params["sysparm_query"] == "name=sys_user":
            if isinstance(failure, Exception):
                raise failure
            return failure
        return httpx2.Response(
            200, json={"result": [{"super_class": "", "super_class.name": ""}]}
        )

    http_mock.get(OBJECTS).mock(side_effect=respond)
    result = await registry(settings).get_query_field_paths(
        "incident", ["short_description", "assigned_to.email"]
    )
    assert [entry.status for entry in result] == ["valid", "unverified"]
    assert "private marker" not in str(result)


async def test_denied_reference_target_is_not_introspected(settings: Settings) -> None:
    root_tables()
    denied = next(iter(DENIED_TABLES))
    fields_by_table({"incident": [column("target", "reference", denied)]})
    result = await registry(settings).get_query_field_paths(
        "incident", ["target.secret"]
    )
    assert result[0].status == "unverified"
    assert len(http_mock.calls) == 2


async def test_reference_traversal_is_bounded(settings: Settings) -> None:
    root_tables()
    fields_by_table({"incident": [column("parent", "reference", "incident")]})
    result = await registry(settings).get_query_field_paths(
        "incident", [".".join(["parent"] * 9)]
    )
    assert result[0].status == "unverified"
    assert result[0].reason is not None
    assert "depth bound" in result[0].reason
    assert http_mock.routes[-1].call_count == 1


async def test_reference_target_flush_refreshes_cached_discovery(
    settings: Settings,
) -> None:
    root_tables()
    definitions = {
        "incident": [column("assigned_to", "reference", "sys_user")],
        "sys_user": [],
    }
    fields_by_table(definitions)
    metadata = registry(settings)
    path = ["assigned_to.email"]
    assert (await metadata.get_query_field_paths("incident", path))[
        0
    ].status == "unverified"
    definitions["sys_user"] = [column("email")]
    metadata.flush("sys_user")
    assert (await metadata.get_query_field_paths("incident", path))[0].status == "valid"


async def test_ancestor_flush_refreshes_cached_query_fields(settings: Settings) -> None:
    root_tables({"incident": "task"})
    definitions = {"task": []}
    fields_by_table(definitions)
    metadata = registry(settings)
    assert (await metadata.get_query_field_paths("incident", ["priority"]))[
        0
    ].status == "unverified"
    definitions["task"] = [column("priority", "integer")]
    metadata.flush("task")
    assert (await metadata.get_query_field_paths("incident", ["priority"]))[
        0
    ].status == "valid"


async def test_query_metadata_ttl_refreshes_unverified_results(
    settings: Settings,
) -> None:
    root_tables()
    definitions = {"incident": []}
    fields_by_table(definitions)
    metadata = registry(settings)
    with patch.object(metadata._query_fields_cache, "_clock", return_value=0):
        assert (await metadata.get_query_field_paths("incident", ["alpha"]))[
            0
        ].status == "unverified"
    definitions["incident"] = [column("alpha")]
    with patch.object(
        metadata._query_fields_cache,
        "_clock",
        return_value=settings.metadata_cache_ttl_seconds + 1,
    ):
        assert (await metadata.get_query_field_paths("incident", ["alpha"]))[
            0
        ].status == "valid"


async def test_query_discovery_shares_inflight_loads_and_owns_returned_data(
    settings: Settings,
) -> None:
    root_tables()
    gate = asyncio.Event()

    async def respond(request: httpx2.Request) -> httpx2.Response:
        await gate.wait()
        return httpx2.Response(200, json={"result": [column("alpha")]})

    metadata_route = http_mock.get(DICTIONARY).mock(side_effect=respond)
    metadata = registry(settings)
    first = asyncio.create_task(metadata.get_query_fields("incident", ["alpha"]))
    second = asyncio.create_task(metadata.get_query_fields("incident", ["alpha"]))
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(first, second)
    assert metadata_route.call_count == 1
    results[0].fields.clear()
    assert len(results[1].fields) == 1
    assert len((await metadata.get_query_fields("incident", ["alpha"])).fields) == 1
