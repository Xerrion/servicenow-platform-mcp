"""Real stdio lifecycle requests with mocked ServiceNow and forbidden update-set reads."""

import os
import sys
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

CHILD = r"""
import copy, os, socket, time, webbrowser
import httpx2
from pydantic import SecretStr
from servicenow_mcp.auth import AccessToken, OAuthPKCEProvider
from servicenow_mcp.config import Settings
from tests._mock_transport import http_mock
from tests.test_flow_completion import effect_routes
from tests.test_flow_editing import BASE, FLOW, persist

def denied(*args, **kwargs):
    raise AssertionError('No external network or browser access in fixture')
socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.socket.sendto = denied
webbrowser.open = denied
Settings.model_config = {**Settings.model_config, 'env_file': None}
async def authorize(self):
    return AccessToken(value=SecretStr('your-api-key-here'), expires_at=time.monotonic() + 3600)
OAuthPKCEProvider._authorize = authorize
settings = Settings(_env_file=None)
action = os.environ['TEST_FLOW_EFFECT'].removesuffix('_pending')
pending = os.environ['TEST_FLOW_EFFECT'].endswith('_pending')
_, _, flow, header, args = effect_routes(settings, action)
def effect(request):
    if action == 'test':
        http_mock.get(BASE + '/api/now/table/sys_flow_context/' + 'd'*32).respond(200, json={'result': {'sys_id':'d'*32, 'flow':'' if pending else FLOW, 'state':'QUEUED'}})
        return httpx2.Response(200, json={'result': {'data':'d'*32}})
    saved = copy.deepcopy(flow)
    saved['active'] = action != 'deactivate'
    if action != 'deactivate':
        saved.update(status='published', isPublished=True, masterSnapshotId='b'*32)
        http_mock.get(BASE + '/api/now/processflow/flow/' + 'b'*32).respond(200, json={'result': {'data': {'id':'b'*32, 'parentFlow':FLOW, 'type':flow['type'], 'snapshot':True, 'masterSnapshot':True}}})
    persist(saved)
    header.update(active=str(saved['active']).lower(), status=saved['status'], master_snapshot=saved['masterSnapshotId'], latest_snapshot=saved['masterSnapshotId'], sys_mod_count='8')
    return httpx2.Response(200, json={'result': {'data':saved}})
suffix = {'activate':'snapshot', 'publish':'subflow'}.get(action, action)
write = http_mock.route(method='GET' if action=='deactivate' else 'POST', url=BASE + '/api/now/processflow/flow/' + FLOW + '/' + suffix).mock(side_effect=effect)
async def transport(self, request):
    assert request.url.host == 'test.service-now.com'
    assert 'sys_update_' not in request.url.path and 'concoursepicker' not in request.url.path
    return await http_mock.handle(request)
httpx2.AsyncHTTPTransport.handle_async_request = transport
from servicenow_mcp.server import main
main()
assert write.call_count == 1
"""


@pytest.mark.parametrize(
    "action", ["activate", "publish", "deactivate", "test", "test_pending"]
)
async def test_lifecycle_stdio_without_update_sets(tmp_path, action):
    root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(
        command=sys.executable,
        args=["-B", "-c", CHILD],
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
            "TEST_FLOW_EFFECT": action,
        },
    )
    errors_path = tmp_path / "lifecycle-stderr"
    with errors_path.open("w") as errors, anyio.fail_after(30):
        async with (
            stdio_client(params, errlog=errors) as streams,
            ClientSession(*streams) as session,
        ):
            await session.initialize()
            listed = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert len(listed) == 18
            assert (
                "expected_update_set"
                not in listed["flow_preview"].input_schema["properties"]
            )
            args = {
                "action": action.removesuffix("_pending"),
                "sys_id": "a" * 32,
                "expected_scope": "global",
                "expected_revision": "7",
            }
            if action.startswith("test"):
                args["inputs"] = {"request_item": "f" * 32}
            preview = await session.call_tool("flow_preview", args)
            await session.validate_tool_result("flow_preview", preview)
            assert not preview.is_error, preview
            result = await session.call_tool(
                "mutation_apply",
                {"preview_token": preview.structured_content["data"]["preview_token"]},
            )
            await session.validate_tool_result("mutation_apply", result)
            assert not result.is_error, result
            data = result.structured_content["data"]
            assert (
                data["action"] == action.removesuffix("_pending")
                and "capture" not in data
            )
            if action.startswith("test"):
                assert (
                    data["execution_sys_id"] == "d" * 32 and data["state"] == "QUEUED"
                )
                assert data["context_owner_verified"] is (action == "test")
                if action == "test_pending":
                    assert (
                        data["terminal"] is None and data["execution_succeeded"] is None
                    )
            else:
                assert data["revision"] == "8"
    assert "Traceback" not in errors_path.read_text()
