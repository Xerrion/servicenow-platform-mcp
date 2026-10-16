"""Offline endpoint, payload, policy, and one-shot contracts for Service Catalog."""

import asyncio
import json
import logging
import re
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import anyio
import httpx2
import pytest
from mcp import ClientSession
from mcp.server import MCPServer
from mcp.shared.memory import create_client_server_memory_streams

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.bootstrap.stdio import create_mcp_server
from servicenow_mcp.capabilities.catalog.masking import mask_catalog
from servicenow_mcp.capabilities.mutations.models import CatalogCommand
from servicenow_mcp.mcp.handlers import _redact_args
from servicenow_mcp.policy import DENIED_TABLES, MASK_VALUE
from tests._mock_transport import http_mock
from tests.helpers import decode_response, get_tool_functions, register_mutations
from tests.test_service_catalog import _register_and_get_tools

if TYPE_CHECKING:
    from servicenow_mcp.config import Settings


BASE = "https://test.service-now.com"
SC = f"{BASE}/api/sn_sc/servicecatalog"
ITEM, USER, CART, CART_ITEM = (letter * 32 for letter in "abcd")
PRODUCER = f"{BASE}/api/now/table/sc_cat_item_producer/{ITEM}"
PRIVATE = "private-fixture-value"
SECRET_KEY_LABELS = ("API key", "API-key", "Private key", "Private-key")


def setup(settings: Settings):
    auth = OAuthPKCEProvider(settings)
    server = MCPServer("catalog-contract")
    coordinator = register_mutations(server, settings, auth, frozenset({"catalog"}))
    state = {
        "item": {"sys_id": ITEM, "name": "Fixture", "type": "record_producer"},
        "definitions": [{"name": "password", "value": PRIVATE}],
        "producer": {
            "sys_id": ITEM,
            "table_name": "incident",
            "script": "fixture script",
            "sys_updated_on": "1",
        },
        "cart": {"cart_id": CART, "items": [{"cart_item_id": CART_ITEM}]},
    }
    for path, key in [
        (f"{SC}/items/{ITEM}", "item"),
        (f"{SC}/items/{ITEM}/variables", "definitions"),
        (PRODUCER, "producer"),
        (f"{SC}/cart", "cart"),
        (f"{SC}/wishlist", "cart"),
    ]:
        http_mock.get(path).mock(
            side_effect=lambda request, key=key: httpx2.Response(
                200, json={"result": state[key]}
            )
        )
    http_mock.get(f"{BASE}/api/now/table/sc_cart/{CART}").respond(
        200, json={"result": {"sys_id": CART}}
    )
    http_mock.get(f"{BASE}/api/now/table/sc_cart_item/{CART_ITEM}").respond(
        200,
        json={"result": {"sys_id": CART_ITEM, "quantity": "1"}},
    )
    http_mock.get(f"{BASE}/api/now/table/sc_cart_item").respond(
        200, json={"result": []}
    )
    return auth, coordinator, get_tool_functions(server), state


MUTATIONS = [
    (
        {
            "action": "submit_producer",
            "item_sys_id": ITEM,
            "variables": {"urgency": "2"},
            "view": "mobile",
        },
        "POST",
        f"items/{ITEM}/submit_producer",
        {"variables": {"urgency": "2"}},
    ),
    (
        {
            "action": "order_now",
            "item_sys_id": ITEM,
            "quantity": 2,
            "requested_for": USER,
            "also_request_for": [USER],
        },
        "POST",
        f"items/{ITEM}/order_now",
        {
            "sysparm_quantity": "2",
            "sysparm_requested_for": USER,
            "sysparm_also_request_for": USER,
        },
    ),
    (
        {"action": "add_to_cart", "item_sys_id": ITEM, "quantity": 0, "variables": {}},
        "POST",
        f"items/{ITEM}/add_to_cart",
        {"sysparm_quantity": "0", "variables": {}},
    ),
    (
        {"action": "add_to_wishlist", "item_sys_id": ITEM, "quantity": 3},
        "POST",
        f"items/{ITEM}/add_to_wishlist",
        {"sysparm_quantity": "3"},
    ),
    (
        {
            "action": "checkout_guide",
            "item_sys_id": ITEM,
            "items": [
                {
                    "sys_id": ITEM,
                    "sysparm_quantity": "2",
                    "variables": {"remote": False},
                },
            ],
        },
        "POST",
        f"items/{ITEM}/checkout_guide",
        {
            "items": [
                {
                    "sys_id": ITEM,
                    "sysparm_quantity": "2",
                    "variables": {"remote": False},
                },
            ],
        },
    ),
    (
        {"action": "submit_guide", "item_sys_id": ITEM, "variables": {"remote": False}},
        "PUT",
        f"items/{ITEM}/submit_guide",
        {"variables": {"remote": False}},
    ),
    (
        {
            "action": "cart_item_update",
            "cart_item_id": CART_ITEM,
            "quantity": 2,
            "variables": {"size": "M"},
        },
        "PUT",
        f"cart/{CART_ITEM}",
        {"sysparm_quantity": "2", "variables": {"size": "M"}},
    ),
    (
        {"action": "cart_item_delete", "cart_item_id": CART_ITEM},
        "DELETE",
        f"cart/{CART_ITEM}",
        None,
    ),
    (
        {"action": "cart_empty", "cart_sys_id": CART},
        "DELETE",
        f"cart/{CART}/empty",
        None,
    ),
    ({"action": "cart_submit"}, "POST", "cart/submit_order", {}),
    ({"action": "cart_checkout"}, "POST", "cart/checkout", {}),
]


@pytest.mark.parametrize(("arguments", "method", "path", "body"), MUTATIONS)
async def test_mutation_contracts(
    settings: Settings, arguments, method, path, body
) -> None:
    _, coordinator, tools, _ = setup(settings)
    response = {
        "sys_id": ITEM,
        "record": f"api/now/table/incident/{ITEM}",
        "number": "INC0000001",
        "redirect_url": "incident.do",
        "table": "incident",
        "parent_id": None,
    }
    route = http_mock.route(method=method, url=f"{SC}/{path}").respond(
        204 if method == "DELETE" else 200,
        **({} if method == "DELETE" else {"json": {"result": response}}),
    )
    preview = decode_response(await tools["service_catalog_preview"](**arguments))
    assert preview["status"] == "success", preview
    assert not route.called
    assert all(call.request.method == "GET" for call in http_mock.calls)
    assert PRIVATE not in str(preview)
    token = preview["data"]["preview_token"]
    applied = decode_response(await tools["mutation_apply"](preview_token=token))
    assert applied["status"] == "success", applied
    assert applied["data"] == ({"deleted": True} if method == "DELETE" else response)
    assert route.call_count == 1
    request = route.calls.last.request
    assert request.method == method
    assert (json.loads(request.content) if request.content else None) == body
    assert dict(request.url.params) == (
        {"sysparm_view": "mobile"} if arguments.get("view") else {}
    )
    assert request.headers["Content-Type"] == "application/json"
    assert "Authorization" in request.headers
    assert (await tools["mutation_apply"](preview_token=token)).is_error
    assert route.call_count == 1
    assert coordinator.store.retained_payload_bytes == 0


READS = [
    (
        {"action": "delivery_address", "user_sys_id": USER},
        "GET",
        f"cart/delivery_address/{USER}",
        None,
    ),
    (
        {"action": "delegation", "item_sys_id": ITEM, "user_sys_id": USER},
        "GET",
        f"items/{ITEM}/delegation/{USER}",
        None,
    ),
    ({"action": "wishlist_get"}, "GET", "wishlist", None),
    (
        {"action": "wishlist_item_get", "cart_item_id": CART_ITEM},
        "GET",
        f"wishlist/{CART_ITEM}",
        None,
    ),
    (
        {
            "action": "get_invalid_delegated_users",
            "item_sys_id": ITEM,
            "also_request_for": [USER],
        },
        "POST",
        f"items/{ITEM}/get_invalid_delegated_users",
        {"sysparm_also_request_for": USER},
    ),
    (
        {"action": "variable_display_value", "sys_id": ITEM, "value": USER},
        "POST",
        f"variables/{ITEM}/display_value",
        {"sysparm_value": USER},
    ),
]


@pytest.mark.parametrize(("arguments", "method", "path", "body"), READS)
async def test_read_contracts(
    settings: Settings, arguments, method, path, body
) -> None:
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    route = http_mock.route(method=method, url=f"{SC}/{path}").respond(
        200, json={"result": {"email": PRIVATE}}
    )
    result = decode_response(await tools["service_catalog"](**arguments))
    assert result["status"] == "success"
    assert PRIVATE not in str(result)
    assert len(http_mock.calls) == 1
    assert (json.loads(route.calls.last.request.content) if body else None) == body


@pytest.mark.parametrize(
    ("action", "path", "arguments"),
    [
        ("catalogs_list", "catalogs", {}),
        ("catalog_get", f"catalogs/{ITEM}", {"sys_id": ITEM}),
        ("categories_list", f"catalogs/{ITEM}/categories", {"catalog_sys_id": ITEM}),
        ("category_get", f"categories/{ITEM}", {"sys_id": ITEM}),
        ("items_list", "items", {"item_type": "record_producer"}),
        ("item_get", f"items/{ITEM}", {"sys_id": ITEM}),
        ("item_variables", f"items/{ITEM}/variables", {"sys_id": ITEM}),
    ],
)
async def test_view_type_and_version(
    settings: Settings, action, path, arguments
) -> None:
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    route = http_mock.get(f"{BASE}/api/sn_sc/v1/servicecatalog/{path}").respond(
        200, json={"result": []}
    )
    result = await tools["service_catalog"](
        action=action, view="both", api_version="v1", **arguments
    )
    assert not result.is_error
    params = dict(route.calls.last.request.url.params)
    assert params["sysparm_view"] == "both"
    if action == "items_list":
        assert params["sysparm_type"] == "record_producer"


@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "submit_producer"},
        {"action": "submit_producer", "item_sys_id": "../private"},
        {"action": "submit_producer", "item_sys_id": ITEM, "quantity": 1},
        {"action": "order_now", "item_sys_id": ITEM, "quantity": -1},
        {"action": "order_now", "item_sys_id": ITEM, "quantity": True},
        {"action": "order_now", "item_sys_id": ITEM, "quantity": 1.5},
        {"action": "order_now", "item_sys_id": ITEM, "requested_for": "invalid"},
        {"action": "order_now", "item_sys_id": ITEM, "also_request_for": [USER] * 51},
        {"action": "cart_item_update", "cart_item_id": CART_ITEM},
        {"action": "cart_item_delete", "cart_item_id": CART_ITEM, "variables": {}},
        {"action": "cart_empty"},
        {"action": "cart_checkout", "item_sys_id": ITEM},
        {"action": "checkout_guide", "item_sys_id": ITEM},
        {
            "action": "checkout_guide",
            "item_sys_id": ITEM,
            "items": [{"sys_id": ITEM, "sysparm_quantity": "0"}],
        },
        {
            "action": "checkout_guide",
            "item_sys_id": ITEM,
            "items": [{"sys_id": ITEM, "extra": PRIVATE}],
        },
        {
            "action": "checkout_guide",
            "item_sys_id": ITEM,
            "items": [{"sys_id": ITEM, "variables": "{}"}],
        },
        {
            "action": "submit_producer",
            "item_sys_id": ITEM,
            "variables": {"value": float("nan")},
        },
        {
            "action": "submit_producer",
            "item_sys_id": ITEM,
            "variables": {"value": "x" * (256 * 1024)},
        },
        {
            "action": "submit_producer",
            "item_sys_id": ITEM,
            "api_version": "v1/../../private",
        },
    ],
)
async def test_invalid_mutations_never_read_or_stage(
    settings: Settings, arguments
) -> None:
    _, coordinator, tools, _ = setup(settings)
    failure = await tools["service_catalog_preview"](**arguments)
    assert failure.is_error
    assert PRIVATE not in str(failure)
    assert not http_mock.calls
    assert len(coordinator.store) == 0


@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "delivery_address"},
        {"action": "delegation", "user_sys_id": USER},
        {"action": "wishlist_item_get", "cart_item_id": "invalid"},
        {
            "action": "get_invalid_delegated_users",
            "item_sys_id": ITEM,
            "also_request_for": [],
        },
        {"action": "variable_display_value", "sys_id": ITEM},
        {"action": "cart_get", "api_version": "invalid"},
    ],
)
async def test_invalid_reads_never_dispatch(settings: Settings, arguments) -> None:
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    assert (await tools["service_catalog"](**arguments)).is_error
    assert not http_mock.calls


@pytest.mark.parametrize("key", ["item", "definitions", "producer"])
async def test_producer_drift_consumes_token(settings: Settings, key: str) -> None:
    _, coordinator, tools, state = setup(settings)
    preview = decode_response(
        await tools["service_catalog_preview"](
            action="submit_producer", item_sys_id=ITEM
        )
    )
    if key == "definitions":
        state[key].append({"name": "new"})
    else:
        state[key]["sys_updated_on"] = "2"
    failure = await tools["mutation_apply"](
        preview_token=preview["data"]["preview_token"]
    )
    assert decode_response(failure)["error"]["code"] == "STALE_PREVIEW"
    assert all(call.request.method == "GET" for call in http_mock.calls)
    assert len(coordinator.store) == 0


@pytest.mark.parametrize(
    "change",
    ["production", "instance", "family", "target_policy", "authorization", "expiry"],
)
async def test_producer_rechecks_safeguards(settings: Settings, change: str) -> None:
    auth, coordinator, tools, _ = setup(settings)
    preview = decode_response(
        await tools["service_catalog_preview"](
            action="submit_producer", item_sys_id=ITEM
        )
    )
    if change == "production":
        settings.servicenow_env = "prod"
    elif change == "instance":
        settings.servicenow_instance_url = "https://other.service-now.com"
    elif change == "family":
        coordinator.families = frozenset()
    elif change == "authorization":
        auth._token = None
    elif change == "target_policy":
        DENIED_TABLES.add("incident")
    try:
        with (
            patch(
                "servicenow_mcp.capabilities.mutations.store.time.monotonic",
                return_value=float("inf"),
            )
            if change == "expiry"
            else nullcontext()
        ):
            result = await tools["mutation_apply"](
                preview_token=preview["data"]["preview_token"]
            )
        assert result.is_error
        assert all(call.request.method == "GET" for call in http_mock.calls)
        assert len(coordinator.store) == 0
    finally:
        DENIED_TABLES.discard("incident")


@pytest.mark.parametrize(
    "failure",
    ["timeout", "invalid_json", "missing_result", "cancel", "api_error", "http_error"],
)
async def test_producer_failure_is_single_use_and_private(
    settings: Settings, failure: str
) -> None:
    _, coordinator, tools, _ = setup(settings)

    def respond(request):
        if failure == "timeout":
            raise httpx2.ReadTimeout(PRIVATE, request=request)
        if failure == "cancel":
            raise asyncio.CancelledError
        if failure == "invalid_json":
            return httpx2.Response(200, content=PRIVATE.encode())
        if failure == "http_error":
            return httpx2.Response(400, json={"error": {"message": PRIVATE}})
        return httpx2.Response(
            200,
            json={"result": {"errCode": "1", "errMsg": PRIVATE}}
            if failure == "api_error"
            else {},
        )

    mutation = http_mock.post(f"{SC}/items/{ITEM}/submit_producer").mock(
        side_effect=respond
    )
    preview = decode_response(
        await tools["service_catalog_preview"](
            action="submit_producer", item_sys_id=ITEM
        )
    )
    token = preview["data"]["preview_token"]
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await tools["mutation_apply"](preview_token=token)
    else:
        result = await tools["mutation_apply"](preview_token=token)
        assert result.is_error
        assert PRIVATE not in str(result)
    assert mutation.call_count == 1
    assert len(coordinator.store) == 0
    assert (await tools["mutation_apply"](preview_token=token)).is_error
    assert mutation.call_count == 1


async def test_producer_masks_summary_but_retains_exact_inputs(
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, _, tools, state = setup(settings)
    variables = {"password": PRIVATE, "email": PRIVATE, "urgency": "2"}
    state["item"]["variables"] = [
        {"name": "email", "value": PRIVATE, "displayvalue": PRIVATE}
    ]
    route = http_mock.post(f"{SC}/items/{ITEM}/submit_producer").respond(
        200,
        json={"result": {"sys_id": ITEM, "table": "incident", "api_token": PRIVATE}},
    )
    preview = decode_response(
        await tools["service_catalog_preview"](
            action="submit_producer", item_sys_id=ITEM, variables=variables
        ),
    )
    assert PRIVATE not in str(preview)
    assert "fixture script" not in str(preview)
    variables["urgency"] = "3"
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["data"]["api_token"] == MASK_VALUE
    assert json.loads(route.calls.last.request.content) == {
        "variables": {"password": PRIVATE, "email": PRIVATE, "urgency": "2"},
    }
    assert PRIVATE not in caplog.text


async def test_empty_guide_is_documented_success(settings: Settings) -> None:
    _, _, tools, _ = setup(settings)
    http_mock.route(method="PUT", url=f"{SC}/items/{ITEM}/submit_guide").respond(
        200, json={"result": []}
    )
    preview = decode_response(
        await tools["service_catalog_preview"](action="submit_guide", item_sys_id=ITEM)
    )
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "success"
    assert result["data"] == []


async def test_catalog_contract_on_wire(settings: Settings) -> None:
    settings.mcp_tool_package = "service_catalog,service_catalog_write"
    server = create_mcp_server(settings)
    async with (
        create_client_server_memory_streams() as (client_streams, server_streams),
        anyio.create_task_group() as tasks,
    ):
        tasks.start_soon(
            server._lowlevel_server.run,
            *server_streams,
            server._lowlevel_server.create_initialization_options(),
        )
        async with ClientSession(*client_streams) as session:
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert (
                "submit_producer"
                in tools["service_catalog_preview"].input_schema["properties"][
                    "action"
                ]["enum"]
            )
            assert (
                "variable_display_value"
                in tools["service_catalog"].input_schema["properties"]["action"]["enum"]
            )
            for tool, arguments in [
                (
                    "service_catalog_preview",
                    {
                        "action": "submit_producer",
                        "item_sys_id": ITEM,
                        "variables": '{"password":"private-fixture-value"}',
                    },
                ),
                (
                    "service_catalog_preview",
                    {"action": "order_now", "item_sys_id": ITEM, "quantity": True},
                ),
                (
                    "service_catalog",
                    {
                        "action": "get_invalid_delegated_users",
                        "item_sys_id": ITEM,
                        "also_request_for": "[]",
                    },
                ),
            ]:
                result = await session.call_tool(tool, arguments)
                assert result.is_error
                assert PRIVATE not in str(result)
                await session.validate_tool_result(tool, result)
        tasks.cancel_scope.cancel()
    assert not http_mock.calls


def test_command_does_not_include_values_in_repr() -> None:
    command = CatalogCommand(
        action="submit_producer", item_sys_id=ITEM, variables={"password": PRIVATE}
    )
    assert PRIVATE not in repr(command)


def test_new_diagnostic_inputs_are_redacted() -> None:
    arguments = dict.fromkeys(
        (
            "items",
            "variables",
            "also_request_for",
            "requested_for",
            "user_sys_id",
            "item_type",
        ),
        PRIVATE,
    )
    assert PRIVATE not in str(_redact_args(arguments))


@pytest.mark.parametrize("action", ["cart_item_update", "cart_item_delete"])
async def test_exact_cart_item_drift_blocks_apply(
    settings: Settings, action: str
) -> None:
    _, _, tools, _ = setup(settings)
    arguments = {"action": action, "cart_item_id": CART_ITEM}
    if action == "cart_item_update":
        arguments["quantity"] = 2
    preview = decode_response(await tools["service_catalog_preview"](**arguments))
    http_mock.get(f"{BASE}/api/now/table/sc_cart_item/{CART_ITEM}").respond(
        200,
        json={"result": {"sys_id": CART_ITEM, "quantity": "2"}},
    )
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["error"]["code"] == "STALE_PREVIEW"
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_legacy_empty_variables_payload_is_unchanged(settings: Settings) -> None:
    _, _, tools, _ = setup(settings)
    route = http_mock.post(f"{SC}/items/{ITEM}/order_now").respond(
        200, json={"result": {"sys_id": ITEM}}
    )
    preview = decode_response(
        await tools["service_catalog_preview"](
            action="order_now", item_sys_id=ITEM, variables={}
        ),
    )
    assert not (
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    ).is_error
    assert json.loads(route.calls.last.request.content) == {}


async def test_producer_concurrent_apply_dispatches_once(settings: Settings) -> None:
    _, _, tools, _ = setup(settings)
    mutation = http_mock.post(f"{SC}/items/{ITEM}/submit_producer").respond(
        200,
        json={"result": {"sys_id": ITEM, "table": "incident"}},
    )
    preview = decode_response(
        await tools["service_catalog_preview"](
            action="submit_producer", item_sys_id=ITEM
        )
    )
    results = await asyncio.gather(
        *(
            tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
            for _ in range(10)
        ),
    )
    assert sum(not result.is_error for result in results) == 1
    assert mutation.call_count == 1


@pytest.mark.parametrize(
    "failure", ["timeout", "incomplete", "http_401", "http_403", "http_429", "http_500"]
)
async def test_put_failure_never_replays(settings: Settings, failure: str) -> None:
    _, coordinator, tools, _ = setup(settings)

    def respond(request):
        if failure == "timeout":
            raise httpx2.ReadTimeout(PRIVATE, request=request)
        status = (
            int(failure.removeprefix("http_")) if failure.startswith("http_") else 200
        )
        return httpx2.Response(
            status, json={"error": {"message": PRIVATE}} if status != 200 else {}
        )

    mutation = http_mock.route(method="PUT", url=f"{SC}/cart/{CART_ITEM}").mock(
        side_effect=respond
    )
    preview = decode_response(
        await tools["service_catalog_preview"](
            action="cart_item_update", cart_item_id=CART_ITEM, quantity=2
        ),
    )
    token = preview["data"]["preview_token"]
    result = await tools["mutation_apply"](preview_token=token)
    assert result.is_error
    assert PRIVATE not in str(result)
    if failure == "timeout":
        assert "unknown" in str(result).lower()
    assert coordinator.store.retained_payload_bytes == 0
    assert (await tools["mutation_apply"](preview_token=token)).is_error
    assert mutation.call_count == 1


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"table_name": ""},
        {"table_name": "../private"},
        {"table_name": "sys_credentials"},
    ],
)
async def test_producer_invalid_target_never_stages(
    settings: Settings, metadata
) -> None:
    _, coordinator, tools, state = setup(settings)
    state["producer"] = metadata
    result = await tools["service_catalog_preview"](
        action="submit_producer", item_sys_id=ITEM
    )
    assert result.is_error
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_producer_metadata_error_does_not_disclose_response(
    settings: Settings,
) -> None:
    _, coordinator, tools, _ = setup(settings)
    http_mock.get(PRODUCER).respond(403, json={"error": {"message": PRIVATE}})
    result = await tools["service_catalog_preview"](
        action="submit_producer", item_sys_id=ITEM
    )
    assert result.is_error
    assert PRIVATE not in str(result)
    assert len(coordinator.store) == 0


async def test_producer_policy_change_during_reads_prevents_write(
    settings: Settings,
) -> None:
    _, _, tools, state = setup(settings)
    preview = decode_response(
        await tools["service_catalog_preview"](
            action="submit_producer", item_sys_id=ITEM
        )
    )

    def change_policy(request):
        DENIED_TABLES.add("incident")
        return httpx2.Response(200, json={"result": state["producer"]})

    http_mock.get(PRODUCER).mock(side_effect=change_policy)
    mutation = http_mock.post(f"{SC}/items/{ITEM}/submit_producer").respond(
        200, json={"result": {"sys_id": ITEM}}
    )
    try:
        assert (
            await tools["mutation_apply"](
                preview_token=preview["data"]["preview_token"]
            )
        ).is_error
        assert not mutation.called
    finally:
        DENIED_TABLES.discard("incident")


async def test_producer_version_retained_for_read_and_write(settings: Settings) -> None:
    _, _, tools, state = setup(settings)
    versioned = f"{BASE}/api/sn_sc/v2/servicecatalog"
    http_mock.get(f"{versioned}/items/{ITEM}").respond(
        200, json={"result": state["item"]}
    )
    http_mock.get(f"{versioned}/items/{ITEM}/variables").respond(
        200, json={"result": state["definitions"]}
    )
    mutation = http_mock.post(f"{versioned}/items/{ITEM}/submit_producer").respond(
        200,
        json={"result": {"sys_id": ITEM, "table": "incident"}},
    )
    preview = decode_response(
        await tools["service_catalog_preview"](
            action="submit_producer", item_sys_id=ITEM, api_version="v2"
        ),
    )
    assert not mutation.called
    assert not (
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    ).is_error
    assert mutation.call_count == 1
    assert all(
        "/api/sn_sc/v2/" in str(call.request.url)
        for call in http_mock.calls
        if "/api/sn_sc/" in str(call.request.url)
    )


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"number": "INC0000001"},
        {"sys_id": ITEM},
        {"sys_id": ITEM, "table": None},
    ],
)
async def test_incomplete_producer_response_is_unknown(
    settings: Settings, response
) -> None:
    _, _, tools, _ = setup(settings)
    mutation = http_mock.post(f"{SC}/items/{ITEM}/submit_producer").respond(
        200, json={"result": response}
    )
    preview = decode_response(
        await tools["service_catalog_preview"](
            action="submit_producer", item_sys_id=ITEM
        )
    )
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["error"]["code"] == "UNKNOWN_OUTCOME"
    assert mutation.call_count == 1


async def test_large_cart_rejected_without_mutation(settings: Settings) -> None:
    _, coordinator, tools, _ = setup(settings)
    http_mock.get(f"{BASE}/api/now/table/sc_cart_item").respond(
        200, json={"result": [{"sys_id": ITEM}] * 1000}
    )
    assert (
        await tools["service_catalog_preview"](action="cart_empty", cart_sys_id=CART)
    ).is_error
    assert len(coordinator.store) == 0
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_unexpected_delete_response_is_unknown(settings: Settings) -> None:
    _, _, tools, _ = setup(settings)
    mutation = http_mock.delete(f"{SC}/cart/{CART_ITEM}").respond(
        200, json={"result": {"sys_id": ITEM}}
    )
    preview = decode_response(
        await tools["service_catalog_preview"](
            action="cart_item_delete", cart_item_id=CART_ITEM
        )
    )
    result = await tools["mutation_apply"](
        preview_token=preview["data"]["preview_token"]
    )
    assert result.is_error
    assert "unknown" in str(result).lower()
    assert mutation.call_count == 1


@pytest.mark.parametrize(
    "action", ["get_invalid_delegated_users", "variable_display_value"]
)
async def test_read_post_errors_are_private(settings: Settings, action: str) -> None:
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    args = (
        {"action": action, "item_sys_id": ITEM, "also_request_for": [USER]}
        if action == "get_invalid_delegated_users"
        else {"action": action, "sys_id": ITEM, "value": USER}
    )
    path = (
        f"items/{ITEM}/get_invalid_delegated_users"
        if action == "get_invalid_delegated_users"
        else f"variables/{ITEM}/display_value"
    )
    http_mock.post(f"{SC}/{path}").respond(
        200, json={"result": {"errCode": "1", "errMsg": PRIVATE}}
    )
    result = await tools["service_catalog"](**args)
    assert result.is_error
    assert PRIVATE not in str(result)


@pytest.mark.parametrize(
    "label", ["Password", "API token", "Email address", *SECRET_KEY_LABELS]
)
def test_variable_label_masks_all_answer_fields(label: str) -> None:
    variable = {
        "name": "u_field_1",
        "label": label,
        "value": PRIVATE,
        "displayvalue": PRIVATE,
        "display_value": PRIVATE,
        "default_value": PRIVATE,
    }
    masked = mask_catalog({"variables": [variable]})["variables"][0]
    assert masked == {
        **variable,
        **dict.fromkeys(
            ("value", "displayvalue", "display_value", "default_value"), MASK_VALUE
        ),
    }
    assert variable["value"] == PRIVATE


@pytest.mark.parametrize("label", ["Password", *SECRET_KEY_LABELS])
async def test_variable_labels_mask_preview_and_guide_result(
    settings: Settings, label: str
) -> None:
    _, coordinator, tools, state = setup(settings)
    variable = {
        "name": "u_field_1",
        "label": label,
        "value": PRIVATE,
        "displayvalue": PRIVATE,
    }
    state["definitions"] = [variable]
    response = [{"sys_id": ITEM, "variables": [variable]}]
    mutation = http_mock.route(
        method="PUT", url=f"{SC}/items/{ITEM}/submit_guide"
    ).respond(
        200,
        json={"result": response},
    )
    preview = decode_response(
        await tools["service_catalog_preview"](action="submit_guide", item_sys_id=ITEM)
    )
    assert preview["status"] == "success"
    assert PRIVATE not in str(preview)
    assert not mutation.called
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "success"
    assert PRIVATE not in str(result)
    assert result["data"][0]["variables"][0]["value"] == MASK_VALUE
    assert coordinator.store.retained_payload_bytes == 0


@pytest.mark.parametrize(
    "action",
    ["submit_producer", "submit_guide", "order_now", "add_to_cart", "cart_item_update"],
)
@pytest.mark.parametrize("label", ["Password", *SECRET_KEY_LABELS])
async def test_submitted_map_uses_definition_labels_without_changing_apply_payload(
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
    action: str,
    label: str,
) -> None:
    caplog.set_level(logging.INFO)
    _, coordinator, tools, state = setup(settings)
    definitions = [
        {"name": "u_field_1", "label": label, "value": ""},
        {"name": "public_option", "label": "Public option", "value": ""},
    ]
    original_definitions = deepcopy(definitions)
    state["definitions"] = definitions
    variables = {"u_field_1": PRIVATE, "public_option": {"enabled": False}}
    original = deepcopy(variables)
    arguments = {"action": action, "variables": variables}
    if action == "cart_item_update":
        arguments.update(cart_item_id=CART_ITEM, quantity=2)
        http_mock.get(f"{BASE}/api/now/table/sc_cart_item/{CART_ITEM}").respond(
            200,
            json={"result": {"sys_id": CART_ITEM, "quantity": "1", "cat_item": ITEM}},
        )
        method, path = "PUT", f"cart/{CART_ITEM}"
    else:
        arguments["item_sys_id"] = ITEM
        method, path = (
            ("PUT" if action == "submit_guide" else "POST"),
            f"items/{ITEM}/{action}",
        )
    response = {"sys_id": ITEM, "table": "incident", "variables": original}
    if action == "cart_item_update":
        response["catalog_item_id"] = ITEM
    mutation = http_mock.route(method=method, url=f"{SC}/{path}").respond(
        200, json={"result": response}
    )
    with patch("servicenow_mcp.mcp.handlers.set_sentry_context") as diagnostics:
        preview = decode_response(await tools["service_catalog_preview"](**arguments))
        assert preview["status"] == "success"
        assert preview["data"]["summary"]["variables"] == {
            **original,
            "u_field_1": MASK_VALUE,
        }
        assert PRIVATE not in str(preview)
        assert not mutation.called
        assert variables == original
        assert definitions == original_definitions
        applied = decode_response(
            await tools["mutation_apply"](
                preview_token=preview["data"]["preview_token"]
            )
        )
        assert applied["status"] == "success"
        assert applied["data"]["variables"] == {**original, "u_field_1": MASK_VALUE}
        assert PRIVATE not in str(applied)
    expected_body = {"variables": original}
    if action in {"order_now", "add_to_cart", "cart_item_update"}:
        expected_body["sysparm_quantity"] = "2" if action == "cart_item_update" else "1"
    assert json.loads(mutation.calls.last.request.content) == expected_body
    assert variables == original
    assert response["variables"] == original
    assert PRIVATE not in str(diagnostics.call_args_list)
    assert PRIVATE not in caplog.text
    assert coordinator.store.retained_payload_bytes == 0


@pytest.mark.parametrize("label", ["Password", *SECRET_KEY_LABELS])
async def test_guide_submitted_maps_use_each_items_definitions_and_preserve_apply_payload(
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
    label: str,
) -> None:
    caplog.set_level(logging.INFO)
    _, coordinator, tools, state = setup(settings)
    other_item = "e" * 32
    state["definitions"] = [
        {
            "name": "section",
            "children": [
                {"name": "u_field_1", "label": label, "value": ""},
                {"name": "public_option", "label": "Public option", "value": ""},
            ],
        },
    ]
    http_mock.get(f"{SC}/items/{other_item}").respond(
        200, json={"result": {"sys_id": other_item}}
    )
    http_mock.get(f"{SC}/items/{other_item}/variables").respond(
        200,
        json={"result": [{"name": "u_field_1", "label": "Public choice", "value": ""}]},
    )
    items: list[dict[str, Any]] = [
        {
            "sys_id": ITEM,
            "sysparm_quantity": "2",
            "variables": {"u_field_1": PRIVATE, "public_option": False},
        },
        {"sys_id": other_item, "variables": {"u_field_1": "public choice"}},
    ]
    original = deepcopy(items)
    response: dict[str, Any] = {
        "cart_id": CART,
        "items": [
            {"catalog_item_id": item["sys_id"], "variables": item["variables"]}
            for item in original
        ],
    }
    mutation = http_mock.post(f"{SC}/items/{ITEM}/checkout_guide").respond(
        200, json={"result": response}
    )
    with patch("servicenow_mcp.mcp.handlers.set_sentry_context") as diagnostics:
        preview = decode_response(
            await tools["service_catalog_preview"](
                action="checkout_guide", item_sys_id=ITEM, items=items
            ),
        )
        assert preview["status"] == "success"
        masked_items = preview["data"]["summary"]["items"]
        assert masked_items[0]["variables"] == {
            "u_field_1": MASK_VALUE,
            "public_option": False,
        }
        assert masked_items[1]["variables"] == original[1]["variables"]
        assert PRIVATE not in str(preview)
        assert items == original
        assert not mutation.called
        applied = decode_response(
            await tools["mutation_apply"](
                preview_token=preview["data"]["preview_token"]
            )
        )
        assert applied["status"] == "success"
        assert applied["data"]["items"][0]["variables"]["u_field_1"] == MASK_VALUE
        assert applied["data"]["items"][1]["variables"] == original[1]["variables"]
        assert PRIVATE not in str(applied)
    assert json.loads(mutation.calls.last.request.content) == {"items": original}
    assert items == original
    assert response["items"][0]["variables"]["u_field_1"] == PRIVATE
    assert PRIVATE not in str(diagnostics.call_args_list)
    assert PRIVATE not in caplog.text
    assert coordinator.store.retained_payload_bytes == 0


def test_variable_map_metadata_is_nested_scoped_and_copy_only() -> None:
    definitions = [
        {
            "name": "section",
            "children": [
                {"name": "u_field_1", "label": "Password", "value": ""},
                {"name": "public_option", "label": "Public choice", "value": ""},
                {"name": "u_field_1", "label": "Public choice", "value": ""},
            ],
        },
    ]
    value = {
        "variables": {
            "u_field_1": PRIVATE,
            "public_option": {"enabled": False, "api_token": PRIVATE},
            "missing_metadata": PRIVATE,
        },
    }
    original = deepcopy(value)
    masked = mask_catalog(value, definitions=definitions)
    assert masked["variables"] == {
        "u_field_1": MASK_VALUE,
        "public_option": {"enabled": False, "api_token": MASK_VALUE},
        "missing_metadata": MASK_VALUE,
    }
    masked["variables"]["public_option"]["enabled"] = True
    assert value == original
    assert definitions[0]["children"][0]["value"] == ""


@pytest.mark.parametrize(
    "definitions",
    [None, [], [{"name": "u_field_1"}], [{"name": "u_field_1", "label": " "}]],
)
def test_variable_maps_without_usable_metadata_are_masked(definitions) -> None:
    assert mask_catalog(
        {"variables": {"u_field_1": PRIVATE}}, definitions=definitions
    ) == {
        "variables": {"u_field_1": MASK_VALUE},
    }


def test_response_definitions_cannot_downgrade_observed_sensitive_labels() -> None:
    sensitive = [{"name": "u_field_1", "label": "Password", "value": ""}]
    response = {
        "definitions": [{"name": "u_field_1", "label": "Public choice"}],
        "variables": {"u_field_1": PRIVATE},
    }
    assert (
        mask_catalog(response, definitions=sensitive)["variables"]["u_field_1"]
        == MASK_VALUE
    )
    nested = {"items": [{"sys_id": "f" * 32, "variables": {"u_field_1": PRIVATE}}]}
    public = [{"name": "u_field_1", "label": "Public choice"}]
    assert (
        mask_catalog(nested, definitions=public)["items"][0]["variables"]["u_field_1"]
        == MASK_VALUE
    )
    unidentified = {"items": [{"variables": {"u_field_1": PRIVATE}}]}
    assert (
        mask_catalog(unidentified, definitions=public)["items"][0]["variables"][
            "u_field_1"
        ]
        == MASK_VALUE
    )
    injected_metadata = {
        "variables": {
            "unknown": PRIVATE,
            "public_option": {
                "definitions": [{"name": "unknown", "label": "Public choice"}]
            },
        },
    }
    assert (
        mask_catalog(injected_metadata, definitions=public)["variables"]["unknown"]
        == MASK_VALUE
    )


async def test_guide_duplicate_items_cannot_downgrade_sensitive_metadata(
    settings: Settings,
) -> None:
    _, _, tools, _ = setup(settings)
    # Parent and duplicate metadata cannot downgrade the sensitive included occurrence.
    public = [{"name": "u_field_1", "label": "Public choice", "value": ""}]
    private = [{"name": "u_field_1", "label": "Password", "value": ""}]
    http_mock.get(f"{SC}/items/{ITEM}/variables").mock(
        side_effect=[
            httpx2.Response(200, json={"result": definitions})
            for definitions in (public, private, public)
        ],
    )
    items = [{"sys_id": ITEM, "variables": {"u_field_1": PRIVATE}}] * 2
    preview = decode_response(
        await tools["service_catalog_preview"](
            action="checkout_guide", item_sys_id=ITEM, items=items
        ),
    )
    assert preview["status"] == "success"
    assert PRIVATE not in str(preview)
    assert all(
        item["variables"]["u_field_1"] == MASK_VALUE
        for item in preview["data"]["summary"]["items"]
    )
    assert all(call.request.method == "GET" for call in http_mock.calls)


async def test_item_form_labels_also_mask_submitted_maps(settings: Settings) -> None:
    _, _, tools, state = setup(settings)
    state["definitions"] = []
    state["item"]["variables"] = [
        {"name": "u_field_1", "label": "Password", "value": ""}
    ]
    mutation = http_mock.post(f"{SC}/items/{ITEM}/submit_producer").respond(
        200,
        json={
            "result": {
                "sys_id": ITEM,
                "table": "incident",
                "variables": {"u_field_1": PRIVATE},
            },
        },
    )
    preview = decode_response(
        await tools["service_catalog_preview"](
            action="submit_producer",
            item_sys_id=ITEM,
            variables={"u_field_1": PRIVATE},
        ),
    )
    assert preview["status"] == "success"
    assert PRIVATE not in str(preview)
    result = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert result["status"] == "success"
    assert PRIVATE not in str(result)
    assert json.loads(mutation.calls.last.request.content) == {
        "variables": {"u_field_1": PRIVATE}
    }


@pytest.mark.parametrize("action", ["submit_producer", "checkout_guide"])
async def test_submitted_map_failure_does_not_disclose_or_replay(
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
    action: str,
) -> None:
    caplog.set_level(logging.INFO)
    _, coordinator, tools, state = setup(settings)
    state["definitions"] = [{"name": "u_field_1", "label": "Password", "value": ""}]
    arguments = {"action": action, "item_sys_id": ITEM}
    if action == "checkout_guide":
        arguments["items"] = [{"sys_id": ITEM, "variables": {"u_field_1": PRIVATE}}]
    else:
        arguments["variables"] = {"u_field_1": PRIVATE}
    mutation = http_mock.post(f"{SC}/items/{ITEM}/{action}").respond(
        500, json={"error": {"message": PRIVATE}}
    )
    with patch("servicenow_mcp.mcp.handlers.set_sentry_context") as diagnostics:
        preview = decode_response(await tools["service_catalog_preview"](**arguments))
        assert preview["status"] == "success"
        assert PRIVATE not in str(preview)
        token = preview["data"]["preview_token"]
        result = await tools["mutation_apply"](preview_token=token)
        assert result.is_error
        assert PRIVATE not in str(result)
        assert (await tools["mutation_apply"](preview_token=token)).is_error
    assert mutation.call_count == 1
    assert PRIVATE not in str(diagnostics.call_args_list)
    assert PRIVATE not in caplog.text
    assert coordinator.store.retained_payload_bytes == 0


@pytest.mark.parametrize("action", ["cart_get", "wishlist_get", "wishlist_item_get"])
async def test_catalog_read_maps_without_labels_do_not_disclose_answers(
    settings: Settings, action: str
) -> None:
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    path = {
        "cart_get": "cart",
        "wishlist_get": "wishlist",
        "wishlist_item_get": f"wishlist/{CART_ITEM}",
    }[action]
    http_mock.get(f"{SC}/{path}").respond(
        200,
        json={
            "result": {
                "items": [
                    {"catalog_item_id": ITEM, "variables": {"u_field_1": PRIVATE}}
                ]
            }
        },
    )
    arguments = {
        "action": action,
        **({"cart_item_id": CART_ITEM} if action == "wishlist_item_get" else {}),
    }
    result = decode_response(await tools["service_catalog"](**arguments))
    assert result["status"] == "success"
    assert result["data"]["items"][0]["variables"]["u_field_1"] == MASK_VALUE
    assert PRIVATE not in str(result)


@pytest.mark.parametrize("has_item_reference", [False, True])
async def test_cart_update_masks_metadata_gaps_and_detects_label_drift(
    settings: Settings,
    has_item_reference: bool,
) -> None:
    _, coordinator, tools, state = setup(settings)
    state["definitions"] = [
        {"name": "u_field_1", "label": "Public choice", "value": ""}
    ]
    reference = {"value": ITEM, "link": "fixture-link"} if has_item_reference else ""
    http_mock.get(f"{BASE}/api/now/table/sc_cart_item/{CART_ITEM}").respond(
        200,
        json={"result": {"sys_id": CART_ITEM, "cat_item": reference}},
    )
    mutation = http_mock.route(method="PUT", url=f"{SC}/cart/{CART_ITEM}").respond(
        200,
        json={"result": {"sys_id": CART_ITEM}},
    )
    preview = decode_response(
        await tools["service_catalog_preview"](
            action="cart_item_update",
            cart_item_id=CART_ITEM,
            quantity=1,
            variables={"u_field_1": PRIVATE},
        ),
    )
    assert preview["status"] == "success"
    expected = PRIVATE if has_item_reference else MASK_VALUE
    assert preview["data"]["summary"]["variables"]["u_field_1"] == expected
    state["definitions"][0]["label"] = "Password"
    applied = decode_response(
        await tools["mutation_apply"](preview_token=preview["data"]["preview_token"])
    )
    assert coordinator.store.retained_payload_bytes == 0
    if has_item_reference:
        assert applied["error"]["code"] == "STALE_PREVIEW"
        assert not mutation.called
    else:
        assert applied["status"] == "success"
        assert (
            json.loads(mutation.calls.last.request.content)["variables"]["u_field_1"]
            == PRIVATE
        )


@pytest.mark.parametrize("invalid_users", [[], [PRIVATE], [PRIVATE, PRIVATE]])
async def test_delegated_user_validation_preserves_outcome(
    settings: Settings, invalid_users: list[str]
) -> None:
    tools = _register_and_get_tools(settings, OAuthPKCEProvider(settings))
    http_mock.post(f"{SC}/items/{ITEM}/get_invalid_delegated_users").respond(
        200,
        json={"result": {"invalidUsers": invalid_users}},
    )
    result = decode_response(
        await tools["service_catalog"](
            action="get_invalid_delegated_users",
            item_sys_id=ITEM,
            also_request_for=[USER],
        ),
    )
    assert result["status"] == "success"
    assert result["data"]["invalidUsers"] == [MASK_VALUE] * len(invalid_users)
    assert PRIVATE not in str(result)


def test_delegated_user_masking_recurses_and_fails_closed_for_malformed_values() -> (
    None
):
    assert mask_catalog({"result": {"INVALIDUSERS": [PRIVATE]}}) == {
        "result": {"INVALIDUSERS": [MASK_VALUE]}
    }
    assert mask_catalog({"invalidUsers": PRIVATE}) == {"invalidUsers": MASK_VALUE}


@pytest.mark.parametrize("denied_table", ["sc_request", "sc_cart_item"])
async def test_guide_checkout_checks_both_policies_before_preview(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    denied_table: str,
) -> None:
    _, coordinator, tools, _ = setup(settings)
    monkeypatch.setattr(
        "servicenow_mcp.policy.DENIED_TABLES", DENIED_TABLES | {denied_table}
    )
    result = await tools["service_catalog_preview"](
        action="checkout_guide", item_sys_id=ITEM, items=[{"sys_id": ITEM}]
    )
    assert result.is_error
    assert decode_response(result)["error"]["code"] == "POLICYERROR"
    assert not http_mock.calls
    assert len(coordinator.store) == 0


@pytest.mark.parametrize("denied_table", ["sc_request", "sc_cart_item"])
@pytest.mark.parametrize("change_at", ["before_apply", "last_precondition_read"])
async def test_guide_checkout_rechecks_both_policies_before_dispatch(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    denied_table: str,
    change_at: str,
) -> None:
    _, coordinator, tools, state = setup(settings)
    mutation = http_mock.post(f"{SC}/items/{ITEM}/checkout_guide").respond(
        200, json={"result": {"cart_id": CART}}
    )
    preview = decode_response(
        await tools["service_catalog_preview"](
            action="checkout_guide", item_sys_id=ITEM, items=[{"sys_id": ITEM}]
        ),
    )
    assert preview["status"] == "success"
    assert not mutation.called
    if change_at == "before_apply":
        monkeypatch.setattr(
            "servicenow_mcp.policy.DENIED_TABLES", DENIED_TABLES | {denied_table}
        )
    else:

        def deny_after_read(request):
            monkeypatch.setattr(
                "servicenow_mcp.policy.DENIED_TABLES", DENIED_TABLES | {denied_table}
            )
            return httpx2.Response(200, json={"result": state["definitions"]})

        # Each guide item reads its definitions after the other preconditions.
        http_mock.get(f"{SC}/items/{ITEM}/variables").mock(
            side_effect=[
                httpx2.Response(200, json={"result": state["definitions"]}),
                deny_after_read,
            ],
        )
    token = preview["data"]["preview_token"]
    result = await tools["mutation_apply"](preview_token=token)
    assert result.is_error
    assert not mutation.called
    if change_at == "last_precondition_read":
        assert decode_response(result)["error"]["code"] == "POLICYERROR"
        definition_reads = [
            call
            for call in http_mock.calls
            if call.request.url.path
            == f"/api/sn_sc/servicecatalog/items/{ITEM}/variables"
        ]
        assert len(definition_reads) == 4  # Two preview reads, then two apply reads.
    assert all(call.request.method == "GET" for call in http_mock.calls)
    assert coordinator.store.retained_payload_bytes == 0
    assert (await tools["mutation_apply"](preview_token=token)).is_error
    assert not mutation.called


def test_coverage_matrix_accounts_for_every_exported_operation() -> None:
    root = Path(__file__).parents[1]
    spec = (
        root / "openapi-specs/sn_sc_service_catalog_api_latest_spec.yaml"
    ).read_text()
    operations = set()
    path = ""
    for line in spec.splitlines():
        if line.startswith("  /api/"):
            path = (
                line.strip()
                .removesuffix(":")
                .removeprefix("/api/sn_sc/servicecatalog/")
            )
        elif re.fullmatch(r"    (get|post|put|delete):", line):
            operations.add((line.strip().removesuffix(":").upper(), path))
    doc = (root / "docs/service-catalog-api.md").read_text()
    implemented = set(
        re.findall(
            r"^\|[ \t]+(GET|POST|PUT|DELETE) `([^`]+)`[ \t]+\|", doc, re.MULTILINE
        )
    )
    unsupported = set(
        re.findall(
            r"^\|[ \t]+(GET|POST|PUT|DELETE)[ \t]+\| `([^`]+)`[ \t]+\|",
            doc,
            re.MULTILINE,
        )
    )
    assert len(implemented) == 25
    assert len(unsupported) == 26
    assert not implemented & unsupported
    assert implemented | unsupported == operations
    rows = [
        line
        for line in doc.splitlines()
        if re.match(r"^\| (GET|POST|PUT|DELETE) `", line)
    ]
    public = [row for row in rows if "| Public," in row]
    preserved = [row for row in rows if ", preserved." in row]
    new = [row for row in rows if ", new." in row]
    assert len(public) == 24
    assert len(preserved) == 12
    assert len(new) == 13
    assert all("| Public, new." in row for row in new)
    assert [row for row in rows if "| OpenAPI-only," in row] == [
        row for row in preserved if "GET `items/{sys_id}/variables`" in row
    ]
    assert any("POST `variables/{sys_id}/display_value`" in row for row in public)
    assert ("POST", "variables/display_value") in unsupported
