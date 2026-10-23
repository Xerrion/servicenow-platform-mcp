"""Real local stdio authoring through a stateful mock HTTP transport."""

import os
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from tests._complete_flow_fixture import create_request, edit_request

CHILD = r"""
import socket, time, webbrowser
import httpx2
from pydantic import SecretStr
from servicenow_mcp.auth import AccessToken, OAuthPKCEProvider
from servicenow_mcp.config import Settings
from tests._complete_flow_fixture import StudioModel
from tests._flow_live_observer import FlowLiveObserver
from servicenow_mcp.platform.client import ServiceNowClient

observer = FlowLiveObserver()
original_studio_request = ServiceNowClient.studio_request
async def observed_studio_request(self, resource, **kwargs):
    result = await original_studio_request(self, resource, **kwargs)
    observer.capture(resource, kwargs.get('data'), result)
    return result
ServiceNowClient.studio_request = observed_studio_request

def denied(*args, **kwargs):
    raise AssertionError('External network and browser access are forbidden in this fixture')
socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.socket.sendto = denied
webbrowser.open = denied
Settings.model_config = {**Settings.model_config, 'env_file': None}
async def authorize(self):
    return AccessToken(value=SecretStr('your-api-key-here'), expires_at=time.monotonic() + 3600)
OAuthPKCEProvider._authorize = authorize
model = StudioModel()
async def transport(self, request):
    assert request.url.host == 'test.service-now.com'
    assert 'Authorization' in request.headers
    return model.handle(request)
httpx2.AsyncHTTPTransport.handle_async_request = transport
from servicenow_mcp.server import main
main()
assert model.revision == 15 and len(model.bodies) == 3
assert not observer.errors
assert len([row for row in observer.exchanges if row[1] is not None]) == 3
"""


@pytest.mark.parametrize(
    "attribute_identity", [False, True, "native_dto", "native_cache"]
)
async def test_stdio_full_create_and_followup_edit(tmp_path, attribute_identity):
    root = Path(__file__).resolve().parents[1]
    child = CHILD
    if attribute_identity == "native_cache":
        child = child.replace(
            "from tests._complete_flow_fixture import StudioModel",
            "from tests.test_flow_root_cache import NativeCacheModel as StudioModel",
        )
    elif attribute_identity == "native_dto":
        child = child.replace(
            "from tests._complete_flow_fixture import StudioModel",
            "from tests.test_flow_variable_dto import VariableDTOModel as StudioModel",
        )
    elif attribute_identity:
        child = child.replace(
            "from tests._complete_flow_fixture import StudioModel",
            "from tests.test_flow_variable_identity import VariableIdentityModel as StudioModel",
        )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-B", "-c", child],
        cwd=tmp_path,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": os.pathsep.join((str(root / "src"), str(root))),
            "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
            "SERVICENOW_OAUTH_CLIENT_ID": "offline-client",
            "SERVICENOW_ENV": "dev",
            "SERVICENOW_OAUTH_CACHE": "memory",
            "MCP_TOOL_PACKAGE": "full",
            "SENTRY_DSN": "",
        },
    )
    errors_path = tmp_path / "stdio-errors"
    with errors_path.open("w") as errors, anyio.fail_after(30):
        async with (
            stdio_client(parameters, errlog=errors) as streams,
            ClientSession(*streams) as session,
        ):
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert len(tools) == 18
            args = create_request()
            Draft202012Validator(tools["flow_preview"].input_schema).validate(args)
            preview = await session.call_tool("flow_preview", args)
            await session.validate_tool_result("flow_preview", preview)
            assert not preview.is_error, preview
            created = await session.call_tool(
                "mutation_apply",
                {"preview_token": preview.structured_content["data"]["preview_token"]},
            )
            await session.validate_tool_result("mutation_apply", created)
            assert not created.is_error, created
            assert created.structured_content["data"]["revision"] == "14"
            args = edit_request(created.structured_content["data"])
            if attribute_identity in ("native_dto", "native_cache"):
                refs = created.structured_content["data"]["refs"]
                args["edits"] = [
                    {
                        "op": "create_field",
                        "collection": "flowVariables",
                        "name": "roundtrip_probe",
                        "label": "Roundtrip probe",
                        "type": "string",
                    },
                    {
                        "op": "set_variables",
                        "before": refs["source"]["ui_id"],
                        "inputs": {"roundtrip_probe": "Synthetic stdio assignment"},
                    },
                    *args["edits"][:3],
                ]
                inserted: dict[str, Any] = args["edits"][3]
                inserted.pop("after")
                inserted.update(
                    parent=refs["branch"]["ui_id"],
                    inputs={"message": "{{flow_variable.roundtrip_probe}}"},
                )
            Draft202012Validator(tools["flow_preview"].input_schema).validate(args)
            preview = await session.call_tool("flow_preview", args)
            await session.validate_tool_result("flow_preview", preview)
            assert not preview.is_error, preview
            edited = await session.call_tool(
                "mutation_apply",
                {"preview_token": preview.structured_content["data"]["preview_token"]},
            )
            await session.validate_tool_result("mutation_apply", edited)
            assert not edited.is_error, edited
            assert edited.structured_content["data"]["revision"] == "15"
            assert edited.structured_content["data"]["refs"]["inserted"]["sys_id"]
    errors = errors_path.read_text()
    assert "your-api-key-here" not in errors and "Traceback" not in errors
