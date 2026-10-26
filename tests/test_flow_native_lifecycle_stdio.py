"""Real stdio with captured native DTOs, master versions and lifecycle echoes."""

import os
import sys
from pathlib import Path

import anyio
from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from tests._native_full_fixture import FLOW, create_request, edit_request
from tests.test_flow_creation_stdio import CHILD


async def test_native_master_lifecycle_stdio(tmp_path):
    root = Path(__file__).resolve().parents[1]
    child = (
        CHILD.replace(
            "from tests._complete_flow_fixture import StudioModel",
            "from tests.test_flow_native_lifecycle import NativeLifecycleModel as StudioModel",
        )
        .replace(
            "model = StudioModel()",
            "model = StudioModel(); model.native_master = True; model.unset_echo_engine = True",
        )
        .replace(
            "assert model.revision == 15 and len(model.bodies) == 3",
            "assert len(model.lifecycle_writes) == 2 and len(model.bodies) == 3",
        )
        .replace(
            "assert request.url.host == 'test.service-now.com'",
            "assert request.url.host == 'test.service-now.com'\n    assert 'sys_update_' not in request.url.path and 'concoursepicker' not in request.url.path and 'designer-bundle' not in request.url.path",
        )
    )
    params = StdioServerParameters(
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
    errors_path = tmp_path / "native-lifecycle-stderr"
    with errors_path.open("w") as errors, anyio.fail_after(30):
        async with (
            stdio_client(params, errlog=errors) as streams,
            ClientSession(*streams) as session,
        ):
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert (
                "expected_update_set"
                not in tools["flow_preview"].input_schema["properties"]
            )

            async def unit(args):
                Draft202012Validator(tools["flow_preview"].input_schema).validate(args)
                preview = await session.call_tool("flow_preview", args)
                await session.validate_tool_result("flow_preview", preview)
                assert not preview.is_error, preview
                token = preview.structured_content["data"]["preview_token"]
                result = await session.call_tool(
                    "mutation_apply", {"preview_token": token}
                )
                await session.validate_tool_result("mutation_apply", result)
                assert not result.is_error, result
                replay = await session.call_tool(
                    "mutation_apply", {"preview_token": token}
                )
                assert replay.is_error
                return result.structured_content["data"]

            created = await unit(create_request())
            published = await unit(
                {
                    "action": "activate",
                    "sys_id": FLOW,
                    "expected_scope": "global",
                    "expected_revision": created["revision"],
                }
            )
            assert (
                published["active"] is True
                and published["native_echo_engine_unset"] is True
            )
            edited = await unit(
                edit_request({**created, "revision": published["revision"]})
            )
            assert edited["status"] == "draft" and edited["active"] is True
            assert edited["runtime_preservation"] == "verified"
            stopped = await unit(
                {
                    "action": "deactivate",
                    "sys_id": FLOW,
                    "expected_scope": "global",
                    "expected_revision": edited["revision"],
                }
            )
            assert stopped["active"] is False
    assert "Traceback" not in errors_path.read_text()
