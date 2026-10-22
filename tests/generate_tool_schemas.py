"""Regenerate offline protocol fixtures from registered tools. No ServiceNow requests."""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from servicenow_mcp.bootstrap.stdio import create_mcp_server
from servicenow_mcp.config import Settings


async def generate() -> None:
    with patch.dict(
        "os.environ",
        {
            "SERVICENOW_INSTANCE_URL": "https://test.service-now.com",
            "SERVICENOW_OAUTH_CLIENT_ID": "test-client",
            "SERVICENOW_ENV": "dev",
            "MCP_TOOL_PACKAGE": "full",
        },
        clear=True,
    ):
        server = create_mcp_server(Settings(_env_file=None))
        tools = await server.list_tools()
    schemas = {
        tool.name: {
            "input": tool.input_schema,
            "output": tool.output_schema,
            "annotations": tool.annotations.model_dump(),
        }
        for tool in tools
    }
    flow = schemas.pop("flow_preview")
    destination = Path(__file__).parent / "fixtures"
    (destination / "native_tool_schemas.json").write_text(
        json.dumps(schemas, indent=2) + "\n"
    )
    (destination / "flow_preview_schema.json").write_text(
        json.dumps(
            {"input": flow["input"], "shared_output_and_annotations": "record_preview"},
            indent=2,
        )
        + "\n",
    )


if __name__ == "__main__":
    asyncio.run(generate())
