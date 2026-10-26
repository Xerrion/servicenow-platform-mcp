"""Real stdio retains Flow reads while excluding the removed Flow write group."""

import os
import sys
from pathlib import Path

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

CHILD = r"""
import socket, webbrowser
import httpx2
from servicenow_mcp.config import Settings

def denied(*args, **kwargs):
    raise AssertionError('External network and browser access forbidden in fixture')
socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.socket.sendto = denied
webbrowser.open = denied
async def transport(self, request):
    raise AssertionError('Flow discovery and rejected writes must not contact ServiceNow')
httpx2.AsyncHTTPTransport.handle_async_request = transport
Settings.model_config = {**Settings.model_config, 'env_file': None}
from servicenow_mcp.server import main
main()
"""


async def test_full_stdio_retains_flow_reads_and_rejects_flow_preview(tmp_path):
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
    errors_path = tmp_path / "flow-tools-stderr"
    with errors_path.open("w") as errors, anyio.fail_after(30):
        async with (
            stdio_client(params, errlog=errors) as streams,
            ClientSession(*streams) as session,
        ):
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert len(names) == 17
            assert "flow_preview" not in names
            assert {
                "flow",
                "record_preview",
                "attachment_preview",
                "service_catalog_preview",
                "apply_change",
            } <= names
            description = await session.call_tool("flow", {"action": "describe"})
            await session.validate_tool_result("flow", description)
            assert not description.is_error
            assert {"inspect", "contract", "validate", "status"} <= set(
                description.structured_content["data"]["actions"]
            )
            rejected = await session.call_tool(
                "flow_preview",
                {"action": "create", "name": "Unavailable draft", "scope": "global"},
            )
            assert rejected.is_error
    assert "Traceback" not in errors_path.read_text()
