"""Real local stdio contract read with captured native Table API responses."""

import os
import sys
from pathlib import Path

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

CHILD = r"""
import socket,time,webbrowser
import httpx2
from pydantic import SecretStr
from servicenow_mcp.auth import AccessToken,OAuthPKCEProvider
from servicenow_mcp.config import Settings
from tests.test_flow_trigger_activation import ActivationReadModel
def denied(*a,**kw):raise AssertionError('External network/browser access forbidden')
socket.socket.connect=denied
socket.socket.connect_ex=denied
socket.socket.sendto=denied
webbrowser.open=denied
Settings.model_config={**Settings.model_config,'env_file':None}
async def authorize(self):return AccessToken(value=SecretStr('your-api-key-here'),expires_at=time.monotonic()+3600)
OAuthPKCEProvider._authorize=authorize
model=ActivationReadModel()
async def transport(self,request):return model.handle(request)
httpx2.AsyncHTTPTransport.handle_async_request=transport
from servicenow_mcp.server import main
main()
"""


async def test_stdio_contract_distinguishes_flow_enablement_from_instance_flag(
    tmp_path,
):
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
        },
    )
    with (tmp_path / "stdio-errors").open("w") as errors, anyio.fail_after(30):
        async with (
            stdio_client(params, errlog=errors) as streams,
            ClientSession(*streams) as session,
        ):
            await session.initialize()
            result = await session.call_tool(
                "flow",
                {
                    "action": "contract",
                    "sys_id": "a" * 32,
                    "sections": ["flow", "triggers", "structural_summary", "warnings"],
                },
            )
            await session.validate_tool_result("flow", result)
            assert not result.is_error, result
            payload = result.structured_content
            assert payload["status"] == "success" and "truncation" not in payload
            trigger = payload["data"]["triggers"][0]
            assert trigger["active"] is None and trigger["active_source"] is None
            assert trigger["activation"] == {
                "mode": "flow_managed",
                "instance_active_applicable": False,
                "flow_sys_id": "a" * 32,
                "flow_active": False,
                "source": "flow.active",
            }
            assert trigger["metadata_complete"] is True and trigger["warnings"] == []
            assert payload["data"]["flow"]["active"] is False
            assert payload["data"]["structural_summary"]["complete"] is True
    assert "Traceback" not in (tmp_path / "stdio-errors").read_text()
