# Getting Started

This guide walks you through installing and configuring the ServiceNow Platform MCP server for use with your AI client.

---

## Prerequisites

- **Python 3.12 or later** - The server requires Python 3.12+ (3.12, 3.13, and 3.14 are supported)
- **A ServiceNow instance** - Developer, test, or production (note: write operations are blocked on production instances)
- **ServiceNow credentials** - A user account with appropriate roles. Admin is recommended for full access to all tools
- **An MCP-compatible AI client** - [OpenCode](https://opencode.ai), [Claude Desktop](https://claude.ai/download), [VS Code Copilot](https://code.visualstudio.com/), [Cursor](https://cursor.sh/), or any client supporting the [Model Context Protocol](https://modelcontextprotocol.io/)

---

## Installation

### Recommended: Run with uvx (no install required)

```bash
uvx servicenow-platform-mcp
```

This downloads and runs the server in an isolated environment. No permanent installation needed.

### Alternative: Install with pip or uv

```bash
pip install servicenow-platform-mcp
```

```bash
uv add servicenow-platform-mcp
```

> **Note:** The server communicates via stdio transport - it is launched by your MCP client as a subprocess, not run as a standalone service. You do not need to start it manually.

---

## Environment Variables

Three environment variables are required. These are passed to the server by your MCP client configuration.

| Variable | Required | Description |
|---|---|---|
| `SERVICENOW_INSTANCE_URL` | Yes | Full instance URL, must start with `https://` |
| `SERVICENOW_USERNAME` | Yes | ServiceNow username for Basic Auth |
| `SERVICENOW_PASSWORD` | Yes | ServiceNow password |
| `MCP_TOOL_PACKAGE` | No | Tool package to load (default: `"full"`). See [[Tool-Packages]] |
| `SERVICENOW_ENV` | No | Environment label (default: `"dev"`). Write ops blocked on `"prod"` / `"production"` |

The server also loads variables from `.env` and `.env.local` files in the working directory (`.env.local` takes precedence).

See [[Configuration]] for the full reference of all environment variables.

---

## MCP Client Configuration

Configure your MCP client to launch the server with the required environment variables. Below are configuration examples for popular clients.

### OpenCode

File: `~/.config/opencode/opencode.json`

```json
{
  "mcp": {
    "servicenow": {
      "type": "local",
      "command": ["uvx", "servicenow-platform-mcp"],
      "environment": {
        "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
        "SERVICENOW_USERNAME": "admin",
        "SERVICENOW_PASSWORD": "your-password"
      }
    }
  }
}
```

### Claude Desktop

File: `claude_desktop_config.json`

```json
{
  "mcpServers": {
    "servicenow": {
      "command": "uvx",
      "args": ["servicenow-platform-mcp"],
      "env": {
        "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
        "SERVICENOW_USERNAME": "admin",
        "SERVICENOW_PASSWORD": "your-password"
      }
    }
  }
}
```

### VS Code / Cursor

File: `.vscode/mcp.json`

```json
{
  "servers": {
    "servicenow": {
      "command": "uvx",
      "args": ["servicenow-platform-mcp"],
      "env": {
        "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
        "SERVICENOW_USERNAME": "admin",
        "SERVICENOW_PASSWORD": "your-password"
      }
    }
  }
}
```

### Generic stdio

For any client that supports stdio transport, launch the server with inline environment variables:

```bash
SERVICENOW_INSTANCE_URL=https://your-instance.service-now.com \
SERVICENOW_USERNAME=admin \
SERVICENOW_PASSWORD=your-password \
uvx servicenow-platform-mcp
```

---

## First Steps

Once configured, try these example prompts with your AI agent:

- **"Describe the incident table schema"** - Explores table structure, field types, and metadata
- **"List open incidents"** - Queries records using domain tools with choice label resolution
- **"Show me what business rules run on the incident table"** - Inspects platform artifacts
- **"Trace the debug log for incident INC0010001"** - Builds an event timeline from system logs
- **"What update sets were created this week?"** - Change intelligence across your instance
- **"Run a table health investigation on cmdb_ci"** - Automated analysis with findings and recommendations

---

## AI Agent Setup

For copy-paste installation instructions optimized for AI agents, see [INSTALL.md](https://github.com/Xerrion/servicenow-platform-mcp/blob/main/INSTALL.md) in the repository root. This file is designed to be fed directly to an AI agent for self-configuration.

---

## Troubleshooting

### Connection refused or timeout

- Verify `SERVICENOW_INSTANCE_URL` starts with `https://` (HTTP is not supported)
- Check that your ServiceNow instance is reachable from your network
- Trailing slashes are stripped automatically - `https://dev12345.service-now.com/` works fine

### Authentication errors

- Confirm `SERVICENOW_USERNAME` and `SERVICENOW_PASSWORD` are correct
- The user account needs appropriate ServiceNow roles. Admin is recommended for full tool access
- Check if your instance requires MFA or SSO - Basic Auth must be enabled for the user

### No tools appearing in your AI client

- Verify `MCP_TOOL_PACKAGE` is set to a valid package name (default is `"full"`)
- Use the `list_tool_packages` tool (always available) to see what packages and groups exist
- Check your MCP client logs for startup errors

### Write operations blocked

- Write operations are blocked when `SERVICENOW_ENV` is set to `"prod"` or `"production"`
- This is a safety guardrail with no override - use a sub-production instance for write operations
- Read operations work normally regardless of environment setting

### Server not starting

- Ensure Python 3.12 or later is installed: `python --version`
- Try running directly: `uvx servicenow-platform-mcp` to see error output
- Check that `uvx` is installed: `uv --version` (install from [astral.sh/uv](https://astral.sh/uv))

---

## Next Steps

- [[Configuration]] - Full environment variable reference and validation rules
- [[Tool-Reference]] - Complete list of available tools with descriptions
- [[Tool-Packages]] - Choose the right tool package for your use case
- [[Safety-and-Policy]] - Understand the security guardrails
