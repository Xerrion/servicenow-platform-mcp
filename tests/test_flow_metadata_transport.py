"""Source-backed native metadata reads use isolated, read-only transports.

Decision metadata: Studio bundle:450018-450125.
Workflow config: Studio bundle:161301-161317,333448-333459.
Transform definitions: Studio bundle:1074,161301-161317,499757-499763.
Catalog selection: Studio bundle:51619-51638,130876-130895,217830-217850.
Table schema: Studio bundle:226655-226662,226745-226768.
Field types: Studio bundle:232783-232795.
Fixtures are synthetic native response shapes, not captured instance responses.
"""

import json
from typing import TYPE_CHECKING, cast

import httpx2
import pytest

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.errors import ServiceNowMCPError
from servicenow_mcp.platform.client import ServiceNowClient
from tests._mock_transport import http_mock

if TYPE_CHECKING:
    from pydantic import JsonValue

    from servicenow_mcp.config import Settings


BASE = "https://test.service-now.com"
SYS_ID = "a" * 32
OTHER_ID = "b" * 32
DECISION_URL = f"{BASE}/api/now/decisiontable/decisions/{SYS_ID}"
WORKFLOW_URL = f"{BASE}/api/now/v1/workflow/{SYS_ID}/config"
TRANSFORM_URL = f"{BASE}/api/now/transformdefinitions"
CATALOG_URL = f"{BASE}/api/now/processflow/flow/{SYS_ID}/service_get_catalog_variables"
TABLE_SCHEMA_URL = f"{BASE}/api/now/processflow/table/incident/schema"
CATALOG_VARIABLES_URL = (
    f"{BASE}/api/now/processflow/flow/{SYS_ID}/service_catalog_variables"
)
FIELD_TYPES_URL = f"{BASE}/api/now/processflow/field_types"
PRIVATE = "private-native-metadata-body"


@pytest.fixture()
def auth_provider(settings: Settings) -> OAuthPKCEProvider:
    return OAuthPKCEProvider(settings)


def _assert_single_metadata_get(url: str) -> None:
    assert http_mock.calls.call_count == 1
    request = http_mock.calls.last.request
    assert request.method == "GET"
    assert str(request.url) == url
    assert not request.url.query
    assert request.content == b""


def _assert_single_catalog_metadata_post(body: dict[str, JsonValue]) -> None:
    assert http_mock.calls.call_count == 1
    request = http_mock.calls.last.request
    assert request.method == "POST"
    assert str(request.url) == CATALOG_URL
    assert not request.url.query
    assert json.loads(request.content) == body


def _assert_single_table_schema_get() -> None:
    assert http_mock.calls.call_count == 1
    request = http_mock.calls.last.request
    assert request.method == "GET"
    assert str(request.url) == f"{TABLE_SCHEMA_URL}?get_choices=true"
    assert dict(request.url.params) == {"get_choices": "true"}
    assert request.content == b""


@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param(
            {
                "name": "Routing decision",
                "inputs": [
                    {"name": "u_priority", "type": "integer", "mandatory": True}
                ],
                "answerTable": {"value": "sys_user_group", "displayValue": "Group"},
                "distinctAnswers": [{"value": OTHER_ID, "displayValue": "Operations"}],
                "vendorMetadata": {"nested": [False, 0, "", None, {"keep": True}]},
            },
            id="native-metadata-without-id",
        ),
        pytest.param({}, id="empty-object"),
        pytest.param(
            {
                "id": OTHER_ID,
                "inputs": "consumer-validates-inputs",
                "answerTable": None,
                "distinctAnswers": {"opaque": True},
            },
            id="no-identity-or-deep-dto-assumptions",
        ),
    ],
)
@pytest.mark.parametrize(
    "envelope_fields",
    [
        pytest.param({}, id="no-error-fields"),
        pytest.param({"errorCode": 0}, id="integer-zero"),
        pytest.param(
            {"errorCode": 0, "error": None, "errorMessage": ""},
            id="falsy-error-fields",
        ),
        pytest.param({"error": [], "errorMessage": False}, id="other-falsy-errors"),
    ],
)
async def test_decision_table_unwraps_and_preserves_native_metadata(
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    metadata: dict[str, JsonValue],
    envelope_fields: dict[str, JsonValue],
) -> None:
    _ = http_mock.get(DECISION_URL).respond(
        200,
        json={"result": {"data": metadata, **envelope_fields}},
    )

    async with ServiceNowClient(settings, auth_provider) as client:
        result = await client.studio_decision_table(SYS_ID)

    assert result == metadata
    _assert_single_metadata_get(DECISION_URL)


@pytest.mark.parametrize(
    "result",
    [
        None,
        [],
        False,
        0,
        PRIVATE,
        {},
        {"inputs": [], "answerTable": {}},
        {"data": None},
        {"data": []},
        {"data": False},
        {"data": 0},
        {"data": PRIVATE},
        {"data": '{"inputs": []}'},
    ],
)
async def test_decision_table_rejects_missing_or_non_object_data(
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    result: JsonValue,
) -> None:
    _ = http_mock.get(DECISION_URL).respond(200, json={"result": result})

    async with ServiceNowClient(settings, auth_provider) as client:
        with pytest.raises(ServiceNowMCPError) as failure:
            _ = await client.studio_decision_table(SYS_ID)

    assert PRIVATE not in str(failure.value)
    _assert_single_metadata_get(DECISION_URL)


@pytest.mark.parametrize("error_code", [False, True, "0", 0.0, None, 1, -1, {}, []])
async def test_decision_table_accepts_only_exact_integer_zero_error_code(
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    error_code: JsonValue,
) -> None:
    _ = http_mock.get(DECISION_URL).respond(
        200,
        json={"result": {"data": {"opaque": PRIVATE}, "errorCode": error_code}},
    )

    async with ServiceNowClient(settings, auth_provider) as client:
        with pytest.raises(ServiceNowMCPError) as failure:
            _ = await client.studio_decision_table(SYS_ID)

    assert PRIVATE not in str(failure.value)
    _assert_single_metadata_get(DECISION_URL)


@pytest.mark.parametrize("field", ["error", "errorMessage"])
@pytest.mark.parametrize("error", [PRIVATE, {"message": PRIVATE}, [PRIVATE], True])
async def test_decision_table_rejects_native_errors_without_disclosing_them(
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    field: str,
    error: JsonValue,
) -> None:
    _ = http_mock.get(DECISION_URL).respond(
        200,
        json={"result": {"data": {}, "errorCode": 0, field: error}},
    )

    async with ServiceNowClient(settings, auth_provider) as client:
        with pytest.raises(ServiceNowMCPError) as failure:
            _ = await client.studio_decision_table(SYS_ID)

    assert PRIVATE not in str(failure.value)
    _assert_single_metadata_get(DECISION_URL)


@pytest.mark.parametrize(
    "config",
    [
        pytest.param(
            {
                "inputConfig": {
                    "inputs": [{"name": "requested_for", "type": "reference"}],
                    "vendorMetadata": {"keep": [False, 0, "", None]},
                },
                "tableConfig": {"table": "incident"},
                "extensionConfig": {"opaque": {"nested": True}},
            },
            id="native-object-collection",
        ),
        pytest.param(
            [
                {"inputs": [{"name": "requested_for", "type": "reference"}]},
                {"table": "incident", "opaque": [False, 0, "", None]},
            ],
            id="native-array-collection",
        ),
        pytest.param({}, id="empty-object-collection"),
        pytest.param([], id="empty-array-collection"),
        pytest.param(
            {"data": {"inputs": [], "table": "incident"}},
            id="data-is-a-collection-key-not-an-envelope",
        ),
        pytest.param(
            {"profile": {"inputs": "consumer-validates-inputs", "table": None}},
            id="deeper-profile-validation-belongs-to-consumer",
        ),
    ],
)
async def test_workflow_config_preserves_direct_result_collection(
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    config: dict[str, JsonValue] | list[dict[str, JsonValue]],
) -> None:
    _ = http_mock.get(WORKFLOW_URL).respond(200, json={"result": config})

    async with ServiceNowClient(settings, auth_provider) as client:
        result = await client.studio_workflow_config(SYS_ID)

    assert type(result) is type(config)
    assert result == config
    _assert_single_metadata_get(WORKFLOW_URL)


@pytest.mark.parametrize(
    "config",
    [
        None,
        False,
        0,
        PRIVATE,
        '[{"inputs": []}]',
        [None],
        [[]],
        [PRIVATE],
        [{"inputs": []}, False],
        {"profile": None},
        {"profile": []},
        {"profile": PRIVATE},
        {"profile": {}, "invalid": 0},
        {"errorCode": 0},
        {"data": {"inputs": []}, "errorCode": 0},
    ],
)
async def test_workflow_config_rejects_non_object_collection_members(
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    config: JsonValue,
) -> None:
    _ = http_mock.get(WORKFLOW_URL).respond(200, json={"result": config})

    async with ServiceNowClient(settings, auth_provider) as client:
        with pytest.raises(ServiceNowMCPError) as failure:
            _ = await client.studio_workflow_config(SYS_ID)

    assert PRIVATE not in str(failure.value)
    _assert_single_metadata_get(WORKFLOW_URL)


@pytest.mark.parametrize("field", ["error", "errorMessage"])
@pytest.mark.parametrize("error", [PRIVATE, {"message": PRIVATE}, [PRIVATE], True])
async def test_workflow_config_rejects_native_errors_without_disclosing_them(
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    field: str,
    error: JsonValue,
) -> None:
    _ = http_mock.get(WORKFLOW_URL).respond(
        200,
        json={"result": {"profile": {"inputs": []}, field: error}},
    )

    async with ServiceNowClient(settings, auth_provider) as client:
        with pytest.raises(ServiceNowMCPError) as failure:
            _ = await client.studio_workflow_config(SYS_ID)

    assert PRIVATE not in str(failure.value)
    _assert_single_metadata_get(WORKFLOW_URL)


@pytest.mark.parametrize(
    ("method", "url"),
    [
        pytest.param("studio_decision_table", DECISION_URL, id="decision-table"),
        pytest.param("studio_workflow_config", WORKFLOW_URL, id="workflow-config"),
    ],
)
class TestNativeMetadataReadFailures:
    @pytest.mark.parametrize(
        "sys_id",
        [
            "",
            "not-a-sysid",
            "a" * 31,
            "a" * 33,
            "A" * 32,
            f"../{SYS_ID}",
            f"{SYS_ID}?sysparm_query=active=true",
            f"{SYS_ID}/config",
        ],
    )
    async def test_invalid_sys_id_is_rejected_before_http(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        method: str,
        url: str,
        sys_id: str,
    ) -> None:
        route = http_mock.get(url).respond(200, json={"result": {"data": {}}})
        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ValueError):
                await getattr(client, method)(sys_id)

        assert not http_mock.calls
        assert not route.called

    @pytest.mark.parametrize(
        "status", [201, 202, 204, 302, 307, 401, 403, 404, 429, 500, 503]
    )
    async def test_non_200_is_rejected_without_redirect_or_replay(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        method: str,
        url: str,
        status: int,
    ) -> None:
        _ = http_mock.get(url).respond(
            status,
            headers={"Location": f"{BASE}/api/now/action_execution/start"},
            json={
                "result": {"data": {}},
                "error": {"message": PRIVATE, "detail": PRIVATE},
            },
        )
        effect = http_mock.route(url=f"{BASE}/api/now/action_execution/start").respond(
            200, json={"result": {"data": {}}}
        )

        async with (
            httpx2.AsyncClient(follow_redirects=True) as transport,
            ServiceNowClient(settings, auth_provider, http_client=transport) as client,
        ):
            with pytest.raises(ServiceNowMCPError) as failure:
                await getattr(client, method)(SYS_ID)

        assert PRIVATE not in str(failure.value)
        assert not effect.called
        _assert_single_metadata_get(url)

    @pytest.mark.parametrize("error_type", [httpx2.ConnectError, httpx2.ReadTimeout])
    async def test_transport_failure_is_not_retried(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        method: str,
        url: str,
        error_type: type[httpx2.TransportError],
    ) -> None:
        _ = http_mock.get(url).mock(
            side_effect=error_type("Controlled transport failure")
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(httpx2.TransportError):
                await getattr(client, method)(SYS_ID)

        _assert_single_metadata_get(url)

    async def test_invalid_json_is_rejected_without_body_disclosure(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        method: str,
        url: str,
    ) -> None:
        _ = http_mock.get(url).respond(
            200,
            headers={"Content-Type": "application/json"},
            content=f'{{"secret":"{PRIVATE}", invalid json',
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServiceNowMCPError) as failure:
                await getattr(client, method)(SYS_ID)

        assert PRIVATE not in str(failure.value)
        _assert_single_metadata_get(url)

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            [],
            False,
            0,
            PRIVATE,
            {},
            {"data": {}},
            {"error": {"message": PRIVATE}},
        ],
    )
    async def test_missing_result_envelope_is_rejected_without_body_disclosure(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        method: str,
        url: str,
        payload: JsonValue,
    ) -> None:
        _ = http_mock.get(url).respond(
            200,
            headers={"Content-Type": "application/json"},
            content=json.dumps(payload),
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServiceNowMCPError) as failure:
                await getattr(client, method)(SYS_ID)

        assert PRIVATE not in str(failure.value)
        _assert_single_metadata_get(url)


class TestTransformDefinitionMetadata:
    @pytest.mark.parametrize(
        "definitions",
        [
            pytest.param([], id="empty-list"),
            pytest.param([{}], id="no-required-dto-fields"),
            pytest.param(
                [
                    {"name": "Synthetic transform", "opaque": [False, 0, "", None]},
                    {"vendorMetadata": {"nested": [{"preserve": True}]}},
                ],
                id="preserve-all-native-metadata",
            ),
        ],
    )
    @pytest.mark.parametrize(
        "envelope_fields",
        [
            {},
            {"errorCode": 0},
            {"errorCode": 0, "error": None, "errorMessage": ""},
            {"error": [], "errorMessage": False},
        ],
    )
    async def test_unwraps_only_raw_result_data(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        definitions: list[dict[str, JsonValue]],
        envelope_fields: dict[str, JsonValue],
    ) -> None:
        _ = http_mock.get(TRANSFORM_URL).respond(
            200, json={"result": {"data": definitions, **envelope_fields}}
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.studio_transform_definitions()

        assert result == definitions
        _assert_single_metadata_get(TRANSFORM_URL)

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            [],
            PRIVATE,
            {},
            {"data": []},
            {"result": None},
            {"result": []},
            {"result": {}},
            {"result": {"value": {"data": []}}},
            {"result": {"data": None}},
            {"result": {"data": False}},
            {"result": {"data": 0}},
            {"result": {"data": PRIVATE}},
            {"result": {"data": "[{}]"}},
            {"result": {"data": {}}},
            {"result": {"data": [None]}},
            {"result": {"data": [False]}},
            {"result": {"data": [0]}},
            {"result": {"data": [PRIVATE]}},
            {"result": {"data": [[]]}},
            {"result": {"data": [{}, None]}},
        ],
    )
    async def test_rejects_ui_wrappers_and_malformed_definition_lists(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        payload: JsonValue,
    ) -> None:
        _ = http_mock.get(TRANSFORM_URL).respond(
            200,
            headers={"Content-Type": "application/json"},
            content=json.dumps(payload),
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServiceNowMCPError) as failure:
                _ = await client.studio_transform_definitions()

        assert PRIVATE not in str(failure.value)
        _assert_single_metadata_get(TRANSFORM_URL)

    @pytest.mark.parametrize("error_code", [False, True, "0", 0.0, None, 1, -1, {}, []])
    async def test_accepts_only_exact_integer_zero_error_code(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        error_code: JsonValue,
    ) -> None:
        _ = http_mock.get(TRANSFORM_URL).respond(
            200,
            json={"result": {"data": [{"opaque": PRIVATE}], "errorCode": error_code}},
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServiceNowMCPError) as failure:
                _ = await client.studio_transform_definitions()

        assert PRIVATE not in str(failure.value)
        _assert_single_metadata_get(TRANSFORM_URL)

    @pytest.mark.parametrize("field", ["error", "errorMessage"])
    @pytest.mark.parametrize("error", [PRIVATE, {"message": PRIVATE}, [PRIVATE], True])
    async def test_rejects_native_errors_without_disclosing_them(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        field: str,
        error: JsonValue,
    ) -> None:
        _ = http_mock.get(TRANSFORM_URL).respond(
            200, json={"result": {"data": [], "errorCode": 0, field: error}}
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServiceNowMCPError) as failure:
                _ = await client.studio_transform_definitions()

        assert PRIVATE not in str(failure.value)
        _assert_single_metadata_get(TRANSFORM_URL)

    @pytest.mark.parametrize(
        "status", [201, 202, 204, 302, 307, 401, 403, 404, 429, 500, 503]
    )
    async def test_non_200_is_rejected_without_redirect_or_replay(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        status: int,
    ) -> None:
        _ = http_mock.get(TRANSFORM_URL).respond(
            status,
            headers={"Location": f"{BASE}/api/now/action_execution/start"},
            json={
                "result": {"data": []},
                "error": {"message": PRIVATE, "detail": PRIVATE},
            },
        )
        effect = http_mock.route(url=f"{BASE}/api/now/action_execution/start").respond(
            200, json={"result": {"data": []}}
        )

        async with (
            httpx2.AsyncClient(follow_redirects=True) as transport,
            ServiceNowClient(settings, auth_provider, http_client=transport) as client,
        ):
            with pytest.raises(ServiceNowMCPError) as failure:
                _ = await client.studio_transform_definitions()

        assert PRIVATE not in str(failure.value)
        assert not effect.called
        _assert_single_metadata_get(TRANSFORM_URL)

    @pytest.mark.parametrize("error_type", [httpx2.ConnectError, httpx2.ReadTimeout])
    async def test_transport_failure_is_not_retried(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        error_type: type[httpx2.TransportError],
    ) -> None:
        _ = http_mock.get(TRANSFORM_URL).mock(
            side_effect=error_type("Controlled transport failure")
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(httpx2.TransportError):
                _ = await client.studio_transform_definitions()

        _assert_single_metadata_get(TRANSFORM_URL)

    async def test_invalid_json_is_rejected_without_body_disclosure(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
    ) -> None:
        _ = http_mock.get(TRANSFORM_URL).respond(
            200,
            headers={"Content-Type": "application/json"},
            content=f'{{"secret":"{PRIVATE}", invalid json',
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServiceNowMCPError) as failure:
                _ = await client.studio_transform_definitions()

        assert PRIVATE not in str(failure.value)
        _assert_single_metadata_get(TRANSFORM_URL)


class TestCatalogSelectionMetadata:
    @pytest.mark.parametrize("model_id", [None, OTHER_ID])
    @pytest.mark.parametrize("include_multirow", [False, True])
    @pytest.mark.parametrize(
        ("selected_variables", "serialized_selection"),
        [
            pytest.param({}, "", id="empty-selection-is-native-empty-string"),
            pytest.param(
                {
                    "item_option_new": f"{SYS_ID},{OTHER_ID}",
                    "io_set_item": OTHER_ID,
                },
                f'{{"item_option_new":"{SYS_ID},{OTHER_ID}","io_set_item":"{OTHER_ID}"}}',
                id="compact-table-to-comma-joined-sysids-map",
            ),
        ],
    )
    async def test_posts_native_selection_metadata_without_effects(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        model_id: str | None,
        include_multirow: bool,
        selected_variables: dict[str, str],
        serialized_selection: str,
    ) -> None:
        metadata = {
            "availableList": [
                {},
                {"attributes": {"opaque": True}, "extension": [False, 0, "", None]},
            ],
            "activeVariables": [SYS_ID, f"{OTHER_ID}:item_option_new"],
            "vendorMetadata": {"nested": [{"preserve": True}]},
        }
        selection_before = selected_variables.copy()
        _ = http_mock.post(CATALOG_URL).respond(
            200, json={"result": {"data": metadata, "errorCode": 0}}
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.studio_catalog_selection(
                SYS_ID,
                model_id,
                include_multirow=include_multirow,
                selected_variables=selected_variables,
            )

        body: dict[str, JsonValue] = {
            "includeMultiRowVariableSets": include_multirow,
            "selectedVariables": serialized_selection,
        }
        if model_id is not None:
            body["flow_catalog_model"] = model_id
        assert result == metadata
        assert selected_variables == selection_before
        _assert_single_catalog_metadata_post(body)

    @pytest.mark.parametrize(
        "metadata",
        [
            {"availableList": [], "activeVariables": []},
            {"availableList": [{}], "activeVariables": [""]},
            {"availableList": [{}], "activeVariables": [SYS_ID]},
        ],
    )
    @pytest.mark.parametrize(
        "envelope_fields",
        [{}, {"errorCode": 0}, {"error": None, "errorMessage": ""}],
    )
    async def test_preserves_empty_lists_and_native_string_ids_without_dto_assumptions(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        metadata: dict[str, JsonValue],
        envelope_fields: dict[str, JsonValue],
    ) -> None:
        _ = http_mock.post(CATALOG_URL).respond(
            200, json={"result": {"data": metadata, **envelope_fields}}
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.studio_catalog_selection(
                SYS_ID, include_multirow=False, selected_variables={}
            )

        assert result == metadata
        _assert_single_catalog_metadata_post(
            {"includeMultiRowVariableSets": False, "selectedVariables": ""}
        )

    @pytest.mark.parametrize("field", ["catalog_id", "model_id"])
    @pytest.mark.parametrize(
        "invalid_id", ["", "a" * 31, "A" * 32, f"../{SYS_ID}", f"{SYS_ID}?query=true"]
    )
    async def test_rejects_invalid_catalog_and_model_sysids_before_http(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        field: str,
        invalid_id: str,
    ) -> None:
        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ValueError):
                _ = await client.studio_catalog_selection(
                    invalid_id if field == "catalog_id" else SYS_ID,
                    invalid_id if field == "model_id" else None,
                    include_multirow=False,
                    selected_variables={},
                )

        assert not http_mock.calls

    @pytest.mark.parametrize("include_multirow", [None, 0, 1, "false", "true", [], {}])
    async def test_rejects_non_boolean_multirow_flag_before_http(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        include_multirow: object,
    ) -> None:
        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ValueError):
                _ = await client.studio_catalog_selection(
                    SYS_ID,
                    include_multirow=cast("bool", include_multirow),
                    selected_variables={},
                )

        assert not http_mock.calls

    @pytest.mark.parametrize(
        ("selected_variables", "error_type"),
        [
            (None, TypeError),
            ([], TypeError),
            ("", TypeError),
            ({"item_option_new": ""}, ValueError),
            ({"item_option_new": f"{SYS_ID},"}, ValueError),
            ({"item_option_new": f",{SYS_ID}"}, ValueError),
            ({"item_option_new": f"{SYS_ID},invalid"}, ValueError),
            ({"item_option_new": f"{SYS_ID}, {OTHER_ID}"}, ValueError),
            ({"item_option_new": "A" * 32}, ValueError),
            ({"item_option_new": None}, TypeError),
            ({"item_option_new": []}, TypeError),
            ({"item_option_new": 1}, TypeError),
            ({"": SYS_ID}, ValueError),
            ({"item_option_new.name": SYS_ID}, ValueError),
            ({"item_option_new^active=true": SYS_ID}, ValueError),
            ({1: SYS_ID}, TypeError),
        ],
    )
    async def test_rejects_non_native_selected_variable_maps_before_http(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        selected_variables: object,
        error_type: type[Exception],
    ) -> None:
        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(error_type):
                _ = await client.studio_catalog_selection(
                    SYS_ID,
                    include_multirow=False,
                    selected_variables=cast("dict[str, str]", selected_variables),
                )

        assert not http_mock.calls

    @pytest.mark.parametrize(
        "metadata",
        [
            None,
            [],
            PRIVATE,
            {},
            {"availableList": []},
            {"activeVariables": []},
            {"availableList": None, "activeVariables": []},
            {"availableList": {}, "activeVariables": []},
            {"availableList": PRIVATE, "activeVariables": []},
            {"availableList": [None], "activeVariables": []},
            {"availableList": [[]], "activeVariables": []},
            {"availableList": [{}, PRIVATE], "activeVariables": []},
            {"availableList": [], "activeVariables": None},
            {"availableList": [], "activeVariables": {}},
            {"availableList": [], "activeVariables": PRIVATE},
            {"availableList": [], "activeVariables": [None]},
            {"availableList": [], "activeVariables": [False]},
            {"availableList": [], "activeVariables": [0]},
            {"availableList": [], "activeVariables": [{}]},
        ],
    )
    async def test_rejects_malformed_native_lists_without_disclosing_metadata(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        metadata: JsonValue,
    ) -> None:
        _ = http_mock.post(CATALOG_URL).respond(
            200, json={"result": {"data": metadata}}
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServiceNowMCPError) as failure:
                _ = await client.studio_catalog_selection(
                    SYS_ID, include_multirow=False, selected_variables={}
                )

        assert PRIVATE not in str(failure.value)
        _assert_single_catalog_metadata_post(
            {"includeMultiRowVariableSets": False, "selectedVariables": ""}
        )

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            [],
            PRIVATE,
            {},
            {"data": {"availableList": [], "activeVariables": []}},
            {"result": None},
            {"result": []},
            {"result": {}},
            {
                "result": {
                    "value": {"data": {"availableList": [], "activeVariables": []}}
                }
            },
        ],
    )
    async def test_rejects_non_native_envelopes_without_mutation_outcome_warning(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        payload: JsonValue,
    ) -> None:
        _ = http_mock.post(CATALOG_URL).respond(
            200,
            headers={"Content-Type": "application/json"},
            content=json.dumps(payload),
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServiceNowMCPError) as failure:
                _ = await client.studio_catalog_selection(
                    SYS_ID, include_multirow=False, selected_variables={}
                )

        message = str(failure.value)
        assert PRIVATE not in message
        assert "outcome unknown" not in message.lower()
        assert "write may have completed" not in message.lower()
        _assert_single_catalog_metadata_post(
            {"includeMultiRowVariableSets": False, "selectedVariables": ""}
        )

    @pytest.mark.parametrize("error_code", [False, True, "0", 0.0, None, 1, -1, {}, []])
    async def test_accepts_only_exact_integer_zero_error_code(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        error_code: JsonValue,
    ) -> None:
        _ = http_mock.post(CATALOG_URL).respond(
            200,
            json={
                "result": {
                    "data": {
                        "availableList": [],
                        "activeVariables": [],
                        "opaque": PRIVATE,
                    },
                    "errorCode": error_code,
                }
            },
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServiceNowMCPError) as failure:
                _ = await client.studio_catalog_selection(
                    SYS_ID, include_multirow=False, selected_variables={}
                )

        assert PRIVATE not in str(failure.value)
        _assert_single_catalog_metadata_post(
            {"includeMultiRowVariableSets": False, "selectedVariables": ""}
        )

    @pytest.mark.parametrize("field", ["error", "errorMessage"])
    @pytest.mark.parametrize("error", [PRIVATE, {"message": PRIVATE}, [PRIVATE], True])
    async def test_rejects_native_errors_without_disclosing_them(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        field: str,
        error: JsonValue,
    ) -> None:
        _ = http_mock.post(CATALOG_URL).respond(
            200,
            json={
                "result": {
                    "data": {"availableList": [], "activeVariables": []},
                    "errorCode": 0,
                    field: error,
                }
            },
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServiceNowMCPError) as failure:
                _ = await client.studio_catalog_selection(
                    SYS_ID, include_multirow=False, selected_variables={}
                )

        assert PRIVATE not in str(failure.value)
        _assert_single_catalog_metadata_post(
            {"includeMultiRowVariableSets": False, "selectedVariables": ""}
        )

    @pytest.mark.parametrize(
        "status", [201, 202, 204, 302, 307, 401, 403, 404, 429, 500, 503]
    )
    async def test_non_200_is_rejected_without_redirect_or_effect_replay(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        status: int,
    ) -> None:
        _ = http_mock.post(CATALOG_URL).respond(
            status,
            headers={"Location": f"{BASE}/api/now/action_execution/start"},
            json={
                "result": {"data": {"availableList": [], "activeVariables": []}},
                "error": {"message": PRIVATE, "detail": PRIVATE},
            },
        )
        effect = http_mock.route(url=f"{BASE}/api/now/action_execution/start").respond(
            200, json={"result": {"data": {"availableList": [], "activeVariables": []}}}
        )

        async with (
            httpx2.AsyncClient(follow_redirects=True) as transport,
            ServiceNowClient(settings, auth_provider, http_client=transport) as client,
        ):
            with pytest.raises(ServiceNowMCPError) as failure:
                _ = await client.studio_catalog_selection(
                    SYS_ID, include_multirow=False, selected_variables={}
                )

        assert PRIVATE not in str(failure.value)
        assert not effect.called
        _assert_single_catalog_metadata_post(
            {"includeMultiRowVariableSets": False, "selectedVariables": ""}
        )

    @pytest.mark.parametrize("error_type", [httpx2.ConnectError, httpx2.ReadTimeout])
    async def test_transport_failure_is_not_retried(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        error_type: type[httpx2.TransportError],
    ) -> None:
        _ = http_mock.post(CATALOG_URL).mock(
            side_effect=error_type("Controlled transport failure")
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(httpx2.TransportError):
                _ = await client.studio_catalog_selection(
                    SYS_ID, include_multirow=False, selected_variables={}
                )

        _assert_single_catalog_metadata_post(
            {"includeMultiRowVariableSets": False, "selectedVariables": ""}
        )

    async def test_invalid_json_is_read_failure_without_mutation_outcome_warning(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
    ) -> None:
        _ = http_mock.post(CATALOG_URL).respond(
            200,
            headers={"Content-Type": "application/json"},
            content=f'{{"secret":"{PRIVATE}", invalid json',
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServiceNowMCPError) as failure:
                _ = await client.studio_catalog_selection(
                    SYS_ID, include_multirow=False, selected_variables={}
                )

        message = str(failure.value)
        assert PRIVATE not in message
        assert "outcome unknown" not in message.lower()
        assert "write may have completed" not in message.lower()
        _assert_single_catalog_metadata_post(
            {"includeMultiRowVariableSets": False, "selectedVariables": ""}
        )


class TestTableSchemaMetadata:
    @pytest.mark.parametrize(
        "schema",
        [
            pytest.param({"columns": []}, id="empty-columns"),
            pytest.param({"columns": [{"definition": {}}]}, id="minimal-definition"),
            pytest.param(
                {
                    "columns": [
                        {
                            "label": "State",
                            "definition": {
                                "name": "state",
                                "label": "State",
                                "type": "integer",
                                "choices": [{"label": "Open", "value": "1"}],
                                "attributes": {"opaque": True},
                            },
                            "vendorMetadata": [False, 0, "", None],
                        }
                    ],
                    "table": "incident",
                    "vendorSchemaMetadata": {"nested": [{"preserve": True}]},
                },
                id="preserve-native-definition-choices-and-metadata",
            ),
            pytest.param(
                {"columns": [{"definition": {"name": None, "type": 0}}]},
                id="consumer-owns-definition-profile-validation",
            ),
        ],
    )
    @pytest.mark.parametrize(
        "envelope_fields",
        [
            {},
            {"errorCode": 0},
            {"errorCode": 0, "error": None, "errorMessage": ""},
            {"error": [], "errorMessage": False},
        ],
    )
    async def test_gets_choices_and_preserves_full_native_schema(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        schema: dict[str, JsonValue],
        envelope_fields: dict[str, JsonValue],
    ) -> None:
        _ = http_mock.get(f"{TABLE_SCHEMA_URL}?get_choices=true").respond(
            200, json={"result": {"data": schema, **envelope_fields}}
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.studio_table_schema("incident")

        assert result == schema
        _assert_single_table_schema_get()

    @pytest.mark.parametrize(
        "table",
        [
            "",
            "../incident",
            "incident.sys_id",
            "incident?query=true",
            "incident/other",
            "incident^active=true",
            "Incident",
            "incident-table",
        ],
    )
    async def test_rejects_non_single_table_identifiers_before_http(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        table: str,
    ) -> None:
        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ValueError):
                _ = await client.studio_table_schema(table)

        assert not http_mock.calls

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            [],
            PRIVATE,
            {},
            {"data": {"columns": []}},
            {"result": None},
            {"result": []},
            {"result": {}},
            {"result": {"value": {"data": {"columns": []}}}},
            {"result": {"data": None}},
            {"result": {"data": []}},
            {"result": {"data": PRIVATE}},
            {"result": {"data": {}}},
            {"result": {"data": {"columns": None}}},
            {"result": {"data": {"columns": {}}}},
            {"result": {"data": {"columns": PRIVATE}}},
            {"result": {"data": {"columns": [None]}}},
            {"result": {"data": {"columns": [[]]}}},
            {"result": {"data": {"columns": [PRIVATE]}}},
            {"result": {"data": {"columns": [{}]}}},
            {"result": {"data": {"columns": [{"definition": None}]}}},
            {"result": {"data": {"columns": [{"definition": []}]}}},
            {"result": {"data": {"columns": [{"definition": PRIVATE}]}}},
            {"result": {"data": {"columns": [{"definition": {}}, {"definition": []}]}}},
        ],
    )
    async def test_rejects_malformed_native_envelopes_columns_and_definitions(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        payload: JsonValue,
    ) -> None:
        _ = http_mock.get(f"{TABLE_SCHEMA_URL}?get_choices=true").respond(
            200,
            headers={"Content-Type": "application/json"},
            content=json.dumps(payload),
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServiceNowMCPError) as failure:
                _ = await client.studio_table_schema("incident")

        assert PRIVATE not in str(failure.value)
        _assert_single_table_schema_get()

    @pytest.mark.parametrize("error_code", [False, True, "0", 0.0, None, 1, -1, {}, []])
    async def test_accepts_only_exact_integer_zero_error_code(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        error_code: JsonValue,
    ) -> None:
        _ = http_mock.get(f"{TABLE_SCHEMA_URL}?get_choices=true").respond(
            200,
            json={
                "result": {
                    "data": {"columns": [], "opaque": PRIVATE},
                    "errorCode": error_code,
                }
            },
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServiceNowMCPError) as failure:
                _ = await client.studio_table_schema("incident")

        assert PRIVATE not in str(failure.value)
        _assert_single_table_schema_get()

    @pytest.mark.parametrize("field", ["error", "errorMessage"])
    @pytest.mark.parametrize("error", [PRIVATE, {"message": PRIVATE}, [PRIVATE], True])
    async def test_rejects_native_errors_without_disclosing_them(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        field: str,
        error: JsonValue,
    ) -> None:
        _ = http_mock.get(f"{TABLE_SCHEMA_URL}?get_choices=true").respond(
            200,
            json={"result": {"data": {"columns": []}, "errorCode": 0, field: error}},
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServiceNowMCPError) as failure:
                _ = await client.studio_table_schema("incident")

        assert PRIVATE not in str(failure.value)
        _assert_single_table_schema_get()

    @pytest.mark.parametrize(
        "status", [201, 202, 204, 302, 307, 401, 403, 404, 429, 500, 503]
    )
    async def test_non_200_is_rejected_without_redirect_or_effect_replay(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        status: int,
    ) -> None:
        _ = http_mock.get(f"{TABLE_SCHEMA_URL}?get_choices=true").respond(
            status,
            headers={"Location": f"{BASE}/api/now/action_execution/start"},
            json={
                "result": {"data": {"columns": []}},
                "error": {"message": PRIVATE, "detail": PRIVATE},
            },
        )
        effect = http_mock.route(url=f"{BASE}/api/now/action_execution/start").respond(
            200, json={"result": {"data": {"columns": []}}}
        )

        async with (
            httpx2.AsyncClient(follow_redirects=True) as transport,
            ServiceNowClient(settings, auth_provider, http_client=transport) as client,
        ):
            with pytest.raises(ServiceNowMCPError) as failure:
                _ = await client.studio_table_schema("incident")

        assert PRIVATE not in str(failure.value)
        assert not effect.called
        _assert_single_table_schema_get()

    @pytest.mark.parametrize("error_type", [httpx2.ConnectError, httpx2.ReadTimeout])
    async def test_transport_failure_is_not_retried(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        error_type: type[httpx2.TransportError],
    ) -> None:
        _ = http_mock.get(f"{TABLE_SCHEMA_URL}?get_choices=true").mock(
            side_effect=error_type("Controlled transport failure")
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(httpx2.TransportError):
                _ = await client.studio_table_schema("incident")

        _assert_single_table_schema_get()

    async def test_invalid_json_is_rejected_without_body_disclosure(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
    ) -> None:
        _ = http_mock.get(f"{TABLE_SCHEMA_URL}?get_choices=true").respond(
            200,
            headers={"Content-Type": "application/json"},
            content=f'{{"secret":"{PRIVATE}", invalid json',
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServiceNowMCPError) as failure:
                _ = await client.studio_table_schema("incident")

        assert PRIVATE not in str(failure.value)
        _assert_single_table_schema_get()


@pytest.mark.parametrize("model_id", [None, OTHER_ID])
@pytest.mark.parametrize(
    ("include_multirow", "expected_flag"),
    [
        pytest.param(None, "true", id="omitted-preserves-native-true-default"),
        pytest.param(True, "true", id="explicit-true"),
        pytest.param(False, "false", id="explicit-false"),
    ],
)
async def test_catalog_variables_multirow_flag_uses_native_query_strings(
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    model_id: str | None,
    include_multirow: bool | None,
    expected_flag: str,
) -> None:
    variables = [{"id": OTHER_ID, "opaque": [False, 0, "", None]}]
    route = http_mock.get(CATALOG_VARIABLES_URL).respond(
        200, json={"result": {"data": variables}}
    )

    async with ServiceNowClient(settings, auth_provider) as client:
        if include_multirow is None:
            result = await client.studio_catalog_variables(SYS_ID, model_id)
        else:
            result = await client.studio_catalog_variables(
                SYS_ID, model_id, include_multirow=include_multirow
            )

    expected_params = {"includeMultiRowVariableSets": expected_flag}
    if model_id is not None:
        expected_params["flow_catalog_model"] = model_id
    assert result == variables
    assert http_mock.calls.call_count == route.call_count == 1
    request = route.calls.last.request
    assert request.method == "GET"
    assert str(request.url).split("?", 1)[0] == CATALOG_VARIABLES_URL
    assert dict(request.url.params) == expected_params
    assert request.content == b""


@pytest.mark.parametrize("include_multirow", [None, 0, 1, "false", "true", [], {}])
async def test_catalog_variables_rejects_non_boolean_multirow_flag_before_http(
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    include_multirow: object,
) -> None:
    async with ServiceNowClient(settings, auth_provider) as client:
        with pytest.raises(ValueError):
            _ = await client.studio_catalog_variables(
                SYS_ID, include_multirow=cast("bool", include_multirow)
            )

    assert not http_mock.calls


class TestFieldTypeMetadata:
    @pytest.mark.parametrize(
        "field_types",
        [
            pytest.param([], id="empty-list"),
            pytest.param([{"value": "", "label": ""}], id="empty-native-strings"),
            pytest.param(
                [
                    {
                        "value": "opaque_native_type",
                        "label": "Native type",
                        "vendorMetadata": {"nested": [False, 0, "", None]},
                    },
                    {"value": "opaque_native_type", "label": "Same value, another row"},
                ],
                id="preserve-order-duplicates-unknown-types-and-metadata",
            ),
        ],
    )
    @pytest.mark.parametrize(
        "envelope_fields",
        [
            {},
            {"errorCode": 0},
            {"errorCode": 0, "error": None, "errorMessage": ""},
            {"error": [], "errorMessage": False},
        ],
    )
    async def test_preserves_native_type_rows_without_consumer_type_assumptions(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        field_types: list[dict[str, JsonValue]],
        envelope_fields: dict[str, JsonValue],
    ) -> None:
        _ = http_mock.get(FIELD_TYPES_URL).respond(
            200, json={"result": {"data": field_types, **envelope_fields}}
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            result = await client.studio_field_types()

        assert result == field_types
        _assert_single_metadata_get(FIELD_TYPES_URL)

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            [],
            PRIVATE,
            {},
            {"data": []},
            {"result": None},
            {"result": []},
            {"result": {}},
            {"result": {"value": {"data": []}}},
            {"result": {"data": None}},
            {"result": {"data": {}}},
            {"result": {"data": PRIVATE}},
            {"result": {"data": [None]}},
            {"result": {"data": [[]]}},
            {"result": {"data": [PRIVATE]}},
            {"result": {"data": [{}]}},
            {"result": {"data": [{"value": "string"}]}},
            {"result": {"data": [{"label": "String"}]}},
            {"result": {"data": [{"value": None, "label": "String"}]}},
            {"result": {"data": [{"value": 0, "label": "String"}]}},
            {"result": {"data": [{"value": False, "label": "String"}]}},
            {"result": {"data": [{"value": [], "label": "String"}]}},
            {"result": {"data": [{"value": {}, "label": "String"}]}},
            {"result": {"data": [{"value": "string", "label": None}]}},
            {"result": {"data": [{"value": "string", "label": 0}]}},
            {"result": {"data": [{"value": "string", "label": False}]}},
            {"result": {"data": [{"value": "string", "label": []}]}},
            {"result": {"data": [{"value": "string", "label": {}}]}},
            {"result": {"data": [{"value": "string", "label": "String"}, {}]}},
        ],
    )
    async def test_rejects_non_native_envelopes_and_non_string_row_fields(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        payload: JsonValue,
    ) -> None:
        _ = http_mock.get(FIELD_TYPES_URL).respond(
            200,
            headers={"Content-Type": "application/json"},
            content=json.dumps(payload),
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServiceNowMCPError) as failure:
                _ = await client.studio_field_types()

        assert PRIVATE not in str(failure.value)
        _assert_single_metadata_get(FIELD_TYPES_URL)

    @pytest.mark.parametrize("error_code", [False, True, "0", 0.0, None, 1, -1, {}, []])
    async def test_accepts_only_exact_integer_zero_error_code(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        error_code: JsonValue,
    ) -> None:
        _ = http_mock.get(FIELD_TYPES_URL).respond(
            200,
            json={
                "result": {
                    "data": [{"value": "string", "label": PRIVATE}],
                    "errorCode": error_code,
                }
            },
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServiceNowMCPError) as failure:
                _ = await client.studio_field_types()

        assert PRIVATE not in str(failure.value)
        _assert_single_metadata_get(FIELD_TYPES_URL)

    @pytest.mark.parametrize("field", ["error", "errorMessage"])
    @pytest.mark.parametrize("error", [PRIVATE, {"message": PRIVATE}, [PRIVATE], True])
    async def test_rejects_native_errors_without_disclosing_them(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        field: str,
        error: JsonValue,
    ) -> None:
        _ = http_mock.get(FIELD_TYPES_URL).respond(
            200, json={"result": {"data": [], "errorCode": 0, field: error}}
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServiceNowMCPError) as failure:
                _ = await client.studio_field_types()

        assert PRIVATE not in str(failure.value)
        _assert_single_metadata_get(FIELD_TYPES_URL)

    @pytest.mark.parametrize(
        "status", [201, 202, 204, 302, 307, 401, 403, 404, 429, 500, 503]
    )
    async def test_non_200_is_rejected_without_redirect_or_effect_replay(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        status: int,
    ) -> None:
        _ = http_mock.get(FIELD_TYPES_URL).respond(
            status,
            headers={"Location": f"{BASE}/api/now/action_execution/start"},
            json={
                "result": {"data": []},
                "error": {"message": PRIVATE, "detail": PRIVATE},
            },
        )
        effect = http_mock.route(url=f"{BASE}/api/now/action_execution/start").respond(
            200, json={"result": {"data": []}}
        )

        async with (
            httpx2.AsyncClient(follow_redirects=True) as transport,
            ServiceNowClient(settings, auth_provider, http_client=transport) as client,
        ):
            with pytest.raises(ServiceNowMCPError) as failure:
                _ = await client.studio_field_types()

        assert PRIVATE not in str(failure.value)
        assert not effect.called
        _assert_single_metadata_get(FIELD_TYPES_URL)

    @pytest.mark.parametrize("error_type", [httpx2.ConnectError, httpx2.ReadTimeout])
    async def test_transport_failure_is_not_retried(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
        error_type: type[httpx2.TransportError],
    ) -> None:
        _ = http_mock.get(FIELD_TYPES_URL).mock(
            side_effect=error_type("Controlled transport failure")
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(httpx2.TransportError):
                _ = await client.studio_field_types()

        _assert_single_metadata_get(FIELD_TYPES_URL)

    async def test_invalid_json_is_rejected_without_body_disclosure(
        self,
        settings: Settings,
        auth_provider: OAuthPKCEProvider,
    ) -> None:
        _ = http_mock.get(FIELD_TYPES_URL).respond(
            200,
            headers={"Content-Type": "application/json"},
            content=f'{{"secret":"{PRIVATE}", invalid json',
        )

        async with ServiceNowClient(settings, auth_provider) as client:
            with pytest.raises(ServiceNowMCPError) as failure:
                _ = await client.studio_field_types()

        assert PRIVATE not in str(failure.value)
        _assert_single_metadata_get(FIELD_TYPES_URL)
