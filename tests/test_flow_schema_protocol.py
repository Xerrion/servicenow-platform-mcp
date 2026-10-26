"""Consumer contracts for explicit schema effects and one-use schema evidence.

The family double deliberately owns opaque evidence. It isolates coordinator/store
behaviour from native provider-plan validation, which has separate helper tests.
All effect assertions observe the real client transport, not helper call counts.
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import httpx2
import pytest
from mcp.server import MCPServer
from pydantic import JsonValue, ValidationError

from servicenow_mcp.auth import OAuthPKCEProvider
from servicenow_mcp.capabilities.contracts import (
    MAX_JSON_PAYLOAD_BYTES,
    CapabilityError,
)
from servicenow_mcp.capabilities.flows import mutations as flows
from servicenow_mcp.capabilities.mutations.models import (
    FlowCommand,
    RecordCommand,
    StagedMutation,
)
from servicenow_mcp.capabilities.mutations.store import PreviewTokenStore
from servicenow_mcp.errors import ServiceNowMCPError
from servicenow_mcp.platform.client import ServiceNowClient
from servicenow_mcp.platform.metadata._dictionary import DictionaryRegistry
from tests._mock_transport import http_mock
from tests.helpers import (
    decode_response,
    get_registered_tools,
    get_tool_functions,
    register_mutations,
)

if TYPE_CHECKING:
    from servicenow_mcp.capabilities.mutations.coordinator import MutationCoordinator
    from servicenow_mcp.config import Settings


BASE = "https://test.service-now.com"
ACTION = "a" * 32
FLOW = "b" * 32
TEMPLATE = "c" * 32
PROVIDER = f"{BASE}/api/now/action_execution/start"
METADATA = f"{BASE}/api/now/table/sys_hub_action_type_snapshot/{ACTION}"
SAVE = f"{BASE}/api/now/processflow/flow"
SIBLINGS: dict[str, JsonValue] = {
    "table": "incident",
    "options": {"names": ["number"], "enabled": False},
}
EDITS: list[dict[str, JsonValue]] = [
    {"op": "update_action", "step": "selected-step", "inputs": {"payload": {}}}
]
CONNECTION_OVERRIDES: dict[str, str | None] = {"d" * 32: "e" * 32, "f" * 32: None}


def opaque_evidence(label: str = "payload") -> dict[str, Any]:
    return {
        "protocol_fixture": label,
        "private_evidence": {
            "children": [{"name": "internal-only-field", "type": "string"}]
        },
    }


class SchemaFamily:
    """Stateful family boundary with independently observable provider/save effects."""

    def __init__(self) -> None:
        self.evidence = opaque_evidence()
        self.authoring_commands: list[FlowCommand] = []
        self.resolution_commands: list[FlowCommand] = []
        self.saved_commands: list[FlowCommand] = []
        self.reject_authoring = False
        self.provider = http_mock.post(PROVIDER).respond(
            200,
            json={"result": {"output": {"native_result": "private-provider-output"}}},
        )
        self.save = http_mock.post(SAVE).respond(
            200, json={"result": {"data": {"id": FLOW}}}
        )
        http_mock.route(method="PUT", url=SAVE).respond(
            200, json={"result": {"data": {"id": FLOW}}}
        )
        http_mock.get(METADATA).respond(
            200, json={"result": {"sys_id": ACTION, "sys_mod_count": "1"}}
        )

    def validate_evidence(self, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value.get("protocol_fixture"), str):
            raise CapabilityError(
                "Invalid protocol fixture evidence.", "SCHEMA_TOKEN_INVALID"
            )
        return copy.deepcopy(value)

    async def preview(self, command: FlowCommand, client: ServiceNowClient):
        await client.get_record("sys_hub_action_type_snapshot", ACTION)
        if command.action == "resolve_schema":
            self.resolution_commands.append(command.model_copy(deep=True))
            return {
                "input_name": command.input_name,
                "provider_effect": True,
            }, "resolution-plan"
        self.authoring_commands.append(command.model_copy(deep=True))
        if self.reject_authoring:
            raise CapabilityError(
                "The proposed authoring plan is invalid.", "INVALID_AUTHORING"
            )
        return {
            "action": command.action,
            "resolved_schema_count": len(command.schema_evidence),
        }, "authoring-plan"

    async def apply(
        self, command: FlowCommand, expected: str | None, client: ServiceNowClient
    ):
        if command.action == "resolve_schema":
            assert expected == "resolution-plan"
            await client.studio_schema_provider(
                "global.schema_provider", command.inputs or {}
            )
            return flows.SchemaResolution(
                evidence=copy.deepcopy(self.evidence),
                summary={"input_name": command.input_name, "fields": ["answer"]},
            )
        assert expected == "authoring-plan"
        self.saved_commands.append(command.model_copy(deep=True))
        saved = await client.studio_request(
            "flow",
            data={"id": FLOW, "scope": "global", "name": "Schema consumer"},
            create=command.action == "create",
        )
        return {"action": command.action, "sys_id": saved["id"]}


@dataclass
class Protocol:
    settings: Settings
    auth: OAuthPKCEProvider
    coordinator: MutationCoordinator
    server: MCPServer
    tools: dict[str, Any]
    family: SchemaFamily


@pytest.fixture()
def protocol(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Protocol:
    from unittest.mock import AsyncMock

    family = SchemaFamily()
    monkeypatch.setattr(flows, "preview", family.preview)
    monkeypatch.setattr(flows, "apply", family.apply)
    monkeypatch.setattr(flows, "validate_schema_evidence", family.validate_evidence)
    auth = OAuthPKCEProvider(settings)
    server = MCPServer("schema-protocol")
    coordinator = register_mutations(
        server,
        settings,
        auth,
        frozenset({"flows"}),
        dictionary=AsyncMock(spec=DictionaryRegistry),
    )
    return Protocol(
        settings, auth, coordinator, server, get_tool_functions(server), family
    )


async def resolve_preview(protocol: Protocol) -> dict[str, Any]:
    result = decode_response(
        await protocol.tools["flow_preview"](
            action="resolve_schema",
            action_type_sys_id=ACTION,
            input_name="payload",
            inputs=copy.deepcopy(SIBLINGS),
        )
    )
    assert result["status"] == "success", result
    return result["data"]


async def resolve(protocol: Protocol) -> tuple[dict[str, Any], dict[str, Any]]:
    preview = await resolve_preview(protocol)
    result = decode_response(
        await protocol.tools["mutation_apply"](preview_token=preview["preview_token"])
    )
    assert result["status"] == "success", result
    return preview, result["data"]


async def author_preview(protocol: Protocol, tokens: list[str], action: str = "create"):
    arguments = (
        {"name": "Schema consumer", "scope": "global"}
        if action == "create"
        else {"sys_id": FLOW, "edits": copy.deepcopy(EDITS)}
    )
    return await protocol.tools["flow_preview"](
        action=action, schema_tokens=tokens, **arguments
    )


def test_resolve_command_preserves_native_siblings_without_repr_disclosure() -> None:
    command = FlowCommand(
        action="resolve_schema",
        action_type_sys_id=ACTION,
        input_name="payload",
        inputs=copy.deepcopy(SIBLINGS),
        expected_scope="global",
    )
    assert command.inputs == SIBLINGS
    assert command.input_name == "payload"
    assert "incident" not in repr(command)
    assert (
        FlowCommand(
            action="resolve_schema", action_type_sys_id=ACTION, input_name="payload"
        ).inputs
        is None
    )
    assert command.connection_overrides is None


@pytest.mark.parametrize(
    "overrides",
    [None, {}, CONNECTION_OVERRIDES, {"": ""}, {"": "", **CONNECTION_OVERRIDES}],
)
async def test_resolve_preserves_native_connection_overrides_at_model_and_tool_boundaries(
    protocol: Protocol, overrides: dict[str, str | None] | None
) -> None:
    command = FlowCommand(
        action="resolve_schema",
        action_type_sys_id=ACTION,
        input_name="payload",
        connection_overrides=copy.deepcopy(overrides),
    )
    assert command.connection_overrides == overrides
    assert "connection_overrides" not in repr(command)
    assert "e" * 32 not in repr(command)
    result = decode_response(
        await protocol.tools["flow_preview"](
            action="resolve_schema",
            action_type_sys_id=ACTION,
            input_name="payload",
            connection_overrides=copy.deepcopy(overrides),
        )
    )
    assert result["status"] == "success", result
    captured = protocol.family.resolution_commands[-1]
    assert captured.connection_overrides == overrides
    assert not protocol.family.provider.called
    assert not protocol.family.save.called
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "overrides",
    [
        "{}",
        [],
        {"not-a-source-alias-id": "e" * 32},
        {"d" * 32: "not-a-child-alias-id"},
        {"D" * 32: "e" * 32},
        {"d" * 32: "E" * 32},
        {"d" * 32: ""},
        {"": None},
        {"": "e" * 32},
        {"": "", "d" * 32: ""},
        {"d" * 32: False},
        {"d" * 32: 0},
        {"d" * 32: {"sys_id": "e" * 32}},
        {"d" * 32: ["e" * 32]},
    ],
)
async def test_resolve_rejects_non_native_or_invalid_connection_overrides(
    protocol: Protocol, overrides
) -> None:
    with pytest.raises((CapabilityError, ValidationError, ValueError)):
        FlowCommand.model_validate(
            {
                "action": "resolve_schema",
                "action_type_sys_id": ACTION,
                "input_name": "payload",
                "connection_overrides": copy.deepcopy(overrides),
            }
        )
    result = await protocol.tools["flow_preview"](
        action="resolve_schema",
        action_type_sys_id=ACTION,
        input_name="payload",
        connection_overrides=copy.deepcopy(overrides),
    )
    assert result.is_error
    assert not http_mock.calls
    assert not protocol.family.resolution_commands
    assert len(protocol.coordinator.store) == 0


async def test_connection_override_payload_is_bounded_before_transport(
    protocol: Protocol,
) -> None:
    overrides: dict[str, str | None] = {
        f"{index:032x}": None for index in range(1, 8193)
    }
    assert len(json.dumps(overrides).encode()) > MAX_JSON_PAYLOAD_BYTES
    with pytest.raises((CapabilityError, ValidationError, ValueError)):
        FlowCommand(
            action="resolve_schema",
            action_type_sys_id=ACTION,
            input_name="payload",
            connection_overrides=overrides,
        )
    result = await protocol.tools["flow_preview"](
        action="resolve_schema",
        action_type_sys_id=ACTION,
        input_name="payload",
        connection_overrides=overrides,
    )
    assert result.is_error
    assert not http_mock.calls
    assert not protocol.family.resolution_commands
    assert len(protocol.coordinator.store) == 0


@pytest.mark.parametrize("action", ["run", "validate_remote", "unknown"])
@pytest.mark.parametrize("explicit_context", [False, True])
async def test_unknown_flow_actions_fail_argument_validation_before_backend(
    protocol: Protocol, action: str, explicit_context: bool
) -> None:
    arguments = {"action": action, "sys_id": FLOW}
    if explicit_context:
        arguments.update(expected_scope="global", expected_revision="1")
    with pytest.raises(ValidationError) as rejected:
        FlowCommand.model_validate(arguments)
    assert [(error["type"], error["loc"]) for error in rejected.value.errors()] == [
        ("literal_error", ("action",))
    ]
    schema = (await get_registered_tools(protocol.server))["flow_preview"].input_schema
    assert action not in schema["properties"]["action"]["enum"]
    result = decode_response(await protocol.tools["flow_preview"](**arguments))
    assert result["status"] == "error"
    assert result["error"]["code"] == "INVALID_REQUEST"
    assert not http_mock.calls
    assert not protocol.family.resolution_commands
    assert not protocol.family.authoring_commands
    assert not protocol.family.saved_commands
    assert len(protocol.coordinator.store) == 0


@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "create", "name": "Consumer", "scope": "global"},
        {"action": "edit", "sys_id": FLOW, "edits": copy.deepcopy(EDITS)},
        {"action": "add_action", "sys_id": FLOW, "action_type_sys_id": ACTION},
        {"action": "add_subflow", "sys_id": FLOW, "subflow_sys_id": ACTION},
        *(
            {
                "action": action,
                "sys_id": FLOW,
                "expected_scope": "global",
                "expected_revision": "1",
            }
            for action in ("activate", "publish", "deactivate", "test")
        ),
    ],
)
@pytest.mark.parametrize(
    "overrides", [{}, CONNECTION_OVERRIDES, {"": ""}, {"": "", **CONNECTION_OVERRIDES}]
)
async def test_connection_overrides_are_resolution_only_at_model_and_tool_boundaries(
    protocol: Protocol, arguments, overrides: dict[str, str | None]
) -> None:
    with pytest.raises(
        (CapabilityError, ValidationError), match="connection_overrides"
    ):
        FlowCommand.model_validate(
            {
                **copy.deepcopy(arguments),
                "connection_overrides": copy.deepcopy(overrides),
            }
        )
    result = await protocol.tools["flow_preview"](
        **copy.deepcopy(arguments), connection_overrides=copy.deepcopy(overrides)
    )
    assert result.is_error
    assert not http_mock.calls
    assert not protocol.family.resolution_commands
    assert not protocol.family.authoring_commands
    assert len(protocol.coordinator.store) == 0


@pytest.mark.parametrize(
    "arguments",
    [
        {"input_name": "payload"},
        {"action_type_sys_id": ACTION},
        {"action_type_sys_id": "not-an-id", "input_name": "payload"},
        {"action_type_sys_id": ACTION, "input_name": ""},
        {"action_type_sys_id": ACTION, "input_name": "payload", "inputs": "{}"},
        {"action_type_sys_id": ACTION, "input_name": "payload", "inputs": []},
    ],
)
def test_resolve_command_rejects_missing_or_non_native_arguments(arguments) -> None:
    with pytest.raises((CapabilityError, ValidationError, ValueError)):
        FlowCommand(action="resolve_schema", **arguments)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sys_id", FLOW),
        ("name", "Unrelated flow"),
        ("scope", "global"),
        ("description", "Unrelated description"),
        ("flow_type", "subflow"),
        ("subflow_sys_id", FLOW),
        ("edits", copy.deepcopy(EDITS)),
        ("expected_revision", "1"),
        ("schema_tokens", [str(uuid4())]),
    ],
)
def test_resolve_command_rejects_unrelated_authoring_fields(field, value) -> None:
    with pytest.raises((CapabilityError, ValidationError, ValueError)):
        FlowCommand(
            action="resolve_schema",
            action_type_sys_id=ACTION,
            input_name="payload",
            **{field: copy.deepcopy(value)},
        )


@pytest.mark.parametrize("evidence", [[], [opaque_evidence()]])
def test_callers_cannot_construct_internal_schema_evidence(evidence) -> None:
    with pytest.raises((CapabilityError, ValidationError)):
        FlowCommand(
            action="create",
            name="Consumer",
            scope="global",
            schema_evidence=copy.deepcopy(evidence),
        )


@pytest.mark.parametrize(
    "tokens",
    [
        ["not-a-token"],
        [uuid4().hex],
        ["11111111-1111-4111-8111-111111111111"] * 2,
        [str(uuid4()) for _ in range(201)],
    ],
)
def test_schema_tokens_require_bounded_unique_canonical_uuids(tokens) -> None:
    with pytest.raises((CapabilityError, ValidationError)):
        FlowCommand(
            action="create", name="Consumer", scope="global", schema_tokens=tokens
        )


def test_schema_token_limit_accepts_two_hundred_without_repr_disclosure() -> None:
    tokens = [str(uuid4()) for _ in range(200)]
    command = FlowCommand(
        action="create", name="Consumer", scope="global", schema_tokens=tokens
    )
    assert command.schema_tokens == tokens
    assert tokens[0] not in repr(command)


@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "add_action", "sys_id": FLOW, "action_type_sys_id": ACTION},
        {"action": "add_subflow", "sys_id": FLOW, "subflow_sys_id": ACTION},
        *(
            {
                "action": action,
                "sys_id": FLOW,
                "expected_scope": "global",
                "expected_revision": "1",
            }
            for action in ("activate", "publish", "deactivate", "test")
        ),
    ],
)
def test_schema_tokens_are_not_accepted_by_append_or_lifecycle_actions(
    arguments,
) -> None:
    with pytest.raises(CapabilityError, match="schema_tokens"):
        FlowCommand(**arguments, schema_tokens=[str(uuid4())])


async def test_public_preview_publishes_tokens_but_never_raw_evidence(
    protocol: Protocol,
) -> None:
    schema = (await get_registered_tools(protocol.server))["flow_preview"].input_schema
    assert "schema_tokens" in schema["properties"]
    assert "input_name" in schema["properties"]
    assert "connection_overrides" in schema["properties"]
    assert "schema_evidence" not in schema["properties"]
    result = await protocol.tools["flow_preview"](
        action="create", name="Consumer", scope="global", schema_evidence=[]
    )
    assert result.is_error
    assert not http_mock.calls
    assert len(protocol.coordinator.store) == 0


@pytest.mark.parametrize(
    "extra",
    [
        {"inputs": "{}"},
        {"schema_tokens": [str(uuid4())]},
        {"name": "Unrelated flow"},
        {"schema_evidence": [opaque_evidence()]},
    ],
)
async def test_public_resolve_rejects_invalid_arguments_before_transport(
    protocol: Protocol, extra
) -> None:
    arguments = {
        "action_type_sys_id": ACTION,
        "input_name": "payload",
        **copy.deepcopy(extra),
    }
    result = await protocol.tools["flow_preview"](action="resolve_schema", **arguments)
    assert result.is_error
    assert not http_mock.calls
    assert len(protocol.coordinator.store) == 0


@pytest.mark.parametrize(
    "tokens",
    ["[]", ["not-a-token"], [str(uuid4()) for _ in range(201)]],
)
async def test_public_authoring_requires_bounded_native_token_array(
    protocol: Protocol, tokens
) -> None:
    result = await protocol.tools["flow_preview"](
        action="create", name="Consumer", scope="global", schema_tokens=tokens
    )
    assert result.is_error
    assert not http_mock.calls
    assert len(protocol.coordinator.store) == 0


async def test_resolve_preview_is_read_only_then_apply_produces_distinct_schema_token(
    protocol: Protocol,
) -> None:
    preview = await resolve_preview(protocol)
    preview_token = preview["preview_token"]
    assert str(UUID(preview_token)) == preview_token
    assert preview["expires_in_seconds"] == 300
    assert not protocol.family.provider.called
    assert not protocol.family.save.called
    assert all(call.request.method == "GET" for call in http_mock.calls)

    applied = decode_response(
        await protocol.tools["mutation_apply"](preview_token=preview_token)
    )
    assert applied["status"] == "success"
    data = applied["data"]
    assert data["action"] == "resolve_schema"
    assert data["provider_executed"] is True
    assert data["expires_in_seconds"] == 300
    assert data["schema_token"] != preview_token
    assert str(UUID(data["schema_token"])) == data["schema_token"]
    assert data["summary"] == {"input_name": "payload", "fields": ["answer"]}
    assert "internal-only-field" not in str(applied)
    assert "private-provider-output" not in str(applied)
    assert protocol.family.provider.call_count == 1
    assert not protocol.family.save.called
    assert len(protocol.coordinator.store) == 1
    assert (
        await protocol.tools["mutation_apply"](preview_token=preview_token)
    ).is_error
    assert protocol.family.provider.call_count == 1


async def test_concurrent_resolution_apply_executes_provider_once(
    protocol: Protocol,
) -> None:
    preview = await resolve_preview(protocol)
    results = await asyncio.gather(
        *(
            protocol.tools["mutation_apply"](preview_token=preview["preview_token"])
            for _ in range(2)
        )
    )
    assert sum(not result.is_error for result in results) == 1
    assert protocol.family.provider.call_count == 1
    assert not protocol.family.save.called
    assert len(protocol.coordinator.store) == 1


async def test_mutation_apply_consumes_and_rejects_schema_evidence_tokens(
    protocol: Protocol,
) -> None:
    _, applied = await resolve(protocol)
    token = applied["schema_token"]
    wrong_kind = decode_response(
        await protocol.tools["mutation_apply"](preview_token=token)
    )
    assert wrong_kind["error"]["code"] == "PREVIEW_TOKEN_KIND"
    assert (await author_preview(protocol, [token])).is_error
    assert (await protocol.tools["mutation_apply"](preview_token=token)).is_error
    assert protocol.family.provider.call_count == 1
    assert not protocol.family.save.called
    assert (
        len(protocol.coordinator.store)
        == protocol.coordinator.store.retained_payload_bytes
        == 0
    )


@pytest.mark.parametrize("action", ["create", "edit"])
async def test_authoring_requires_a_new_preview_and_hydrates_owned_evidence(
    protocol: Protocol, action: str
) -> None:
    expected = copy.deepcopy(protocol.family.evidence)
    _, applied = await resolve(protocol)
    schema_token = applied["schema_token"]
    protocol.family.evidence["private_evidence"]["children"][0]["name"] = (
        "mutated-after-apply"
    )
    authoring = decode_response(await author_preview(protocol, [schema_token], action))
    assert authoring["status"] == "success", authoring
    assert not protocol.family.save.called
    assert protocol.family.provider.call_count == 1
    command = protocol.family.authoring_commands[-1]
    assert command.schema_tokens is None
    assert command.schema_evidence == [expected]
    assert "internal-only-field" not in repr(command)
    assert (await author_preview(protocol, [schema_token], action)).is_error
    saved = decode_response(
        await protocol.tools["mutation_apply"](
            preview_token=authoring["data"]["preview_token"]
        )
    )
    assert saved["status"] == "success"
    assert saved["data"]["sys_id"] == FLOW
    assert protocol.family.saved_commands[-1].schema_evidence == [expected]
    assert protocol.family.provider.call_count == 1
    assert (
        len(protocol.coordinator.store)
        == protocol.coordinator.store.retained_payload_bytes
        == 0
    )


async def test_authoring_hydrates_every_evidence_token_in_submitted_order(
    protocol: Protocol,
) -> None:
    first_evidence = copy.deepcopy(protocol.family.evidence)
    _, first = await resolve(protocol)
    protocol.family.evidence = opaque_evidence("second-payload")
    second_evidence = copy.deepcopy(protocol.family.evidence)
    _, second = await resolve(protocol)
    tokens = [second["schema_token"], first["schema_token"]]
    result = decode_response(await author_preview(protocol, tokens))
    assert result["status"] == "success", result
    assert protocol.family.authoring_commands[-1].schema_evidence == [
        second_evidence,
        first_evidence,
    ]
    assert protocol.family.authoring_commands[-1].schema_tokens is None
    assert (await author_preview(protocol, tokens)).is_error
    assert protocol.family.provider.call_count == 2
    assert not protocol.family.save.called


async def test_concurrent_authoring_previews_cannot_reuse_one_schema_token(
    protocol: Protocol,
) -> None:
    _, resolved = await resolve(protocol)
    results = await asyncio.gather(
        *(author_preview(protocol, [resolved["schema_token"]]) for _ in range(2))
    )
    assert sum(not result.is_error for result in results) == 1
    assert protocol.family.provider.call_count == 1
    assert not protocol.family.save.called
    assert len(protocol.coordinator.store) == 1


async def test_failed_authoring_preview_still_consumes_schema_evidence(
    protocol: Protocol,
) -> None:
    _, applied = await resolve(protocol)
    protocol.family.reject_authoring = True
    failure = decode_response(await author_preview(protocol, [applied["schema_token"]]))
    assert failure["error"]["code"] == "INVALID_AUTHORING"
    protocol.family.reject_authoring = False
    assert (await author_preview(protocol, [applied["schema_token"]])).is_error
    assert not protocol.family.save.called
    assert protocol.family.provider.call_count == 1
    assert (
        len(protocol.coordinator.store)
        == protocol.coordinator.store.retained_payload_bytes
        == 0
    )


@pytest.mark.parametrize("invalid", ["missing", "wrong_kind"])
async def test_entire_schema_token_batch_is_consumed_on_invalid_member(
    protocol: Protocol, invalid: str
) -> None:
    _, first = await resolve(protocol)
    _, second = await resolve(protocol)
    if invalid == "missing":
        invalid_token = str(uuid4())
    else:
        invalid_token = (await resolve_preview(protocol))["preview_token"]
    tokens = [first["schema_token"], invalid_token, second["schema_token"]]
    assert (await author_preview(protocol, tokens)).is_error
    for token in tokens:
        assert (await protocol.tools["mutation_apply"](preview_token=token)).is_error
    assert not protocol.family.authoring_commands
    assert not protocol.family.save.called
    assert protocol.family.provider.call_count == 2
    assert (
        len(protocol.coordinator.store)
        == protocol.coordinator.store.retained_payload_bytes
        == 0
    )


@pytest.mark.parametrize(
    "change", ["instance", "authorization", "production", "family"]
)
async def test_authoring_scope_rejection_consumes_evidence_without_any_effect(
    protocol: Protocol, change: str
) -> None:
    _, applied = await resolve(protocol)
    if change == "instance":
        protocol.settings.servicenow_instance_url = "https://other.service-now.com"
    elif change == "authorization":
        protocol.auth._token = None
        await protocol.auth.get_headers()
    elif change == "production":
        protocol.settings.servicenow_env = "prod"
    else:
        protocol.coordinator.families = frozenset()
    failure = decode_response(await author_preview(protocol, [applied["schema_token"]]))
    assert failure["status"] == "error"
    if change == "instance":
        assert failure["error"]["code"] == "PREVIEW_CONNECTION_CHANGED"
    elif change == "authorization":
        assert failure["error"]["code"] == "SCHEMA_AUTHORIZATION_CHANGED"
    protocol.settings.servicenow_instance_url = BASE
    protocol.settings.servicenow_env = "dev"
    protocol.coordinator.families = frozenset({"flows"})
    assert (await author_preview(protocol, [applied["schema_token"]])).is_error
    assert not protocol.family.authoring_commands
    assert not protocol.family.save.called
    assert protocol.family.provider.call_count == 1
    assert (
        len(protocol.coordinator.store)
        == protocol.coordinator.store.retained_payload_bytes
        == 0
    )


@pytest.mark.parametrize("change", ["instance", "authorization"])
async def test_resolve_apply_rejects_changed_connection_or_authorization(
    protocol: Protocol, change: str
) -> None:
    preview = await resolve_preview(protocol)
    if change == "instance":
        protocol.settings.servicenow_instance_url = "https://other.service-now.com"
    else:
        protocol.auth._token = None
        await protocol.auth.get_headers()
    assert (
        await protocol.tools["mutation_apply"](preview_token=preview["preview_token"])
    ).is_error
    protocol.settings.servicenow_instance_url = BASE
    assert (
        await protocol.tools["mutation_apply"](preview_token=preview["preview_token"])
    ).is_error
    assert not protocol.family.provider.called
    assert not protocol.family.save.called
    assert len(protocol.coordinator.store) == 0


@pytest.mark.parametrize("kind", ["mutation", "schema_evidence"])
async def test_resolution_and_evidence_tokens_expire_after_three_hundred_seconds(
    protocol: Protocol, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    clock = [time.monotonic()]
    monkeypatch.setattr(
        "servicenow_mcp.capabilities.mutations.store.time.monotonic", lambda: clock[0]
    )
    if kind == "mutation":
        token = (await resolve_preview(protocol))["preview_token"]
    else:
        _, applied = await resolve(protocol)
        token = applied["schema_token"]
    clock[0] += 301
    result = (
        await protocol.tools["mutation_apply"](preview_token=token)
        if kind == "mutation"
        else await author_preview(protocol, [token])
    )
    assert result.is_error
    assert not protocol.family.save.called
    assert protocol.family.provider.call_count == int(kind == "schema_evidence")
    assert (
        len(protocol.coordinator.store)
        == protocol.coordinator.store.retained_payload_bytes
        == 0
    )


@pytest.mark.parametrize("capacity", ["entries", "bytes"])
async def test_capacity_after_provider_effect_reports_truthful_non_replayable_outcome(
    protocol: Protocol, capacity: str
) -> None:
    protocol.coordinator.store = PreviewTokenStore(max_size=1)
    if capacity == "bytes":
        protocol.family.evidence["large_native_schema"] = "x" * 8192
    preview = await resolve_preview(protocol)
    blocker = None
    if capacity == "bytes":
        protocol.coordinator.store.max_bytes = (
            protocol.coordinator.store.retained_payload_bytes
        )
    else:

        async def occupy_remaining_capacity(request):
            nonlocal blocker
            blocker = await protocol.coordinator.store.create(
                StagedMutation(
                    command=RecordCommand(action="create", table="incident", data={}),
                    instance=BASE,
                    authorization_generation=protocol.auth.authorization_generation,
                )
            )
            return httpx2.Response(
                200, json={"result": {"output": {"completed": True}}}, request=request
            )

        protocol.family.provider.mock(side_effect=occupy_remaining_capacity)
    failure = decode_response(
        await protocol.tools["mutation_apply"](preview_token=preview["preview_token"])
    )
    assert failure["error"]["code"] == "SCHEMA_EVIDENCE_NOT_RETAINED"
    assert failure["error"]["outcome"] == {
        "provider_executed": True,
        "schema_retained": False,
    }
    assert "schema_token" not in str(failure)
    assert protocol.family.provider.call_count == 1
    assert not protocol.family.save.called
    assert (
        await protocol.tools["mutation_apply"](preview_token=preview["preview_token"])
    ).is_error
    assert protocol.family.provider.call_count == 1
    if blocker is not None:
        await protocol.coordinator.store.consume(blocker)
    assert (
        len(protocol.coordinator.store)
        == protocol.coordinator.store.retained_payload_bytes
        == 0
    )


@pytest.mark.parametrize("failure", ["malformed_output", "timeout"])
async def test_failed_provider_response_never_retains_evidence_or_replays(
    protocol: Protocol, failure: str
) -> None:
    if failure == "malformed_output":
        protocol.family.provider.respond(200, json={"result": {"output": []}})
    else:
        protocol.family.provider.mock(
            side_effect=httpx2.ReadTimeout("private-provider-marker")
        )
    preview = await resolve_preview(protocol)
    result = decode_response(
        await protocol.tools["mutation_apply"](preview_token=preview["preview_token"])
    )
    assert result["status"] == "error"
    assert "private-provider-marker" not in str(result)
    assert "schema_token" not in str(result)
    assert protocol.family.provider.call_count == 1
    assert (
        await protocol.tools["mutation_apply"](preview_token=preview["preview_token"])
    ).is_error
    assert protocol.family.provider.call_count == 1
    assert (
        len(protocol.coordinator.store)
        == protocol.coordinator.store.retained_payload_bytes
        == 0
    )


async def test_store_owns_evidence_and_consume_many_releases_it_once() -> None:
    evidence = opaque_evidence()
    staged = StagedMutation(
        kind="schema_evidence",
        command=FlowCommand(
            action="resolve_schema", action_type_sys_id=ACTION, input_name="payload"
        ),
        instance=BASE,
        authorization_generation=1,
        evidence=evidence,
    )
    store = PreviewTokenStore()
    token = await store.create(staged)
    assert store.retained_payload_bytes > 0
    assert staged.evidence is not None
    private = staged.evidence["private_evidence"]
    assert isinstance(private, dict)
    children = private["children"]
    assert isinstance(children, list)
    child = children[0]
    assert isinstance(child, dict)
    child["name"] = "caller-mutated"
    consumed = await store.consume_many([token])
    assert consumed[0].kind == "schema_evidence"
    assert consumed[0].evidence == opaque_evidence()
    assert "internal-only-field" not in repr(consumed[0])
    with pytest.raises(CapabilityError):
        await store.consume_many([token])
    assert len(store) == store.retained_payload_bytes == 0


async def test_client_starts_provider_only_through_explicit_effect_method(
    settings: Settings,
) -> None:
    route = http_mock.post(PROVIDER).respond(
        200,
        json={
            "result": {"output": {"children": [{"name": "answer", "type": "string"}]}}
        },
    )
    auth = OAuthPKCEProvider(settings)
    async with ServiceNowClient(settings, auth) as client:
        with pytest.raises(ValueError):
            await client.studio_request("action_execution/start")
        with pytest.raises(ValueError):
            await client.studio_request(
                "action_execution/start", data={"name": "global.schema_provider"}
            )
        with pytest.raises(ValueError):
            await client.studio_lifecycle(FLOW, "schema_provider", {})
        assert not http_mock.calls
        output = await client.studio_schema_provider(
            "global.schema_provider", copy.deepcopy(SIBLINGS)
        )
    assert output == {"children": [{"name": "answer", "type": "string"}]}
    assert route.call_count == 1
    request = route.calls.last.request
    assert request.method == "POST"
    assert not request.url.query
    assert json.loads(request.content) == {
        "name": "global.schema_provider",
        "inputs": SIBLINGS,
    }


@pytest.mark.parametrize(
    ("status", "result"),
    [
        (200, None),
        (200, []),
        (200, {}),
        (200, {"output": None}),
        (200, {"output": []}),
        (200, {"output": '{"children":[]}'}),
        (200, {"output": {}, "errorCode": True}),
        (200, {"output": {}, "errorCode": "0"}),
        (200, {"output": {}, "errorCode": 1}),
        (200, {"output": {}, "errorMessage": "private-provider-error"}),
        (200, {"output": {}, "error": "private-provider-error"}),
        (202, {"output": {}}),
    ],
)
async def test_client_requires_completed_native_provider_output_without_replay(
    settings: Settings, status, result
) -> None:
    route = http_mock.post(PROVIDER).respond(
        status, json={"result": copy.deepcopy(result)}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(ServiceNowMCPError) as failure:
            await client.studio_schema_provider("global.schema_provider", {})
    assert "private-provider-error" not in str(failure.value)
    assert route.call_count == 1


@pytest.mark.parametrize(
    "name", ["", "unscoped", "global.name/other", "global.name?query=true"]
)
async def test_client_rejects_non_native_provider_name_before_request(
    settings: Settings, name: str
) -> None:
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(ValueError):
            await client.studio_schema_provider(name, {})
    assert not http_mock.calls


async def test_client_unwraps_saved_trigger_get_without_provider_effect(
    settings: Settings,
) -> None:
    template = {
        "id": TEMPLATE,
        "name": "Saved trigger",
        "inputs": [{"name": "table", "value": "incident"}],
        "outputs": [{"name": "current", "type": "reference", "reference": "incident"}],
        "opaque": {"preserve": [False, 0, ""]},
    }
    route = http_mock.get(f"{BASE}/api/now/hub/triggerpicker/{TEMPLATE}").respond(
        200, json={"result": {"data": copy.deepcopy(template), "errorCode": 0}}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        result = await client.studio_saved_trigger(TEMPLATE)
    assert result == template
    assert route.call_count == 1
    assert route.calls.last.request.method == "GET"
    assert not route.calls.last.request.url.query
    assert all(call.request.method == "GET" for call in http_mock.calls)


@pytest.mark.parametrize(
    "result",
    [
        {"data": {"id": FLOW, "inputs": [], "outputs": []}},
        {"data": {"id": TEMPLATE, "outputs": []}},
        {"data": {"id": TEMPLATE, "inputs": [], "outputs": {}}},
        {"data": {"id": TEMPLATE, "inputs": [], "outputs": []}, "errorCode": False},
        {
            "data": {"id": TEMPLATE, "inputs": [], "outputs": []},
            "errorMessage": "private-template-error",
        },
        {"data": []},
    ],
)
async def test_client_rejects_incomplete_or_mismatched_saved_trigger(
    settings: Settings, result
) -> None:
    route = http_mock.get(f"{BASE}/api/now/hub/triggerpicker/{TEMPLATE}").respond(
        200, json={"result": copy.deepcopy(result)}
    )
    async with ServiceNowClient(settings, OAuthPKCEProvider(settings)) as client:
        with pytest.raises(ServiceNowMCPError) as failure:
            await client.studio_saved_trigger(TEMPLATE)
    assert "private-template-error" not in str(failure.value)
    assert route.call_count == 1
    assert all(call.request.method == "GET" for call in http_mock.calls)
