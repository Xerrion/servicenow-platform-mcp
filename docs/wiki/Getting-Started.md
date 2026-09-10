# Getting Started

This guide walks you through installing and configuring the ServiceNow Platform MCP server for use with your AI client.

---

## Prerequisites

- **Python 3.12 or later** - The server requires Python 3.12+ (3.12, 3.13, and 3.14 are supported)
- **A ServiceNow instance** - Developer, test, or production (note: write operations are blocked on production instances)
- **ServiceNow authentication** - A public OAuth client with PKCE S256, a registered loopback redirect URI, and a user with the required roles. The browser and stdio process must run on the same machine.
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

Set `SERVICENOW_INSTANCE_URL` and the public OAuth client settings. These variables are passed to the server by your MCP client configuration.

| Variable | Required | Description |
| --- | --- | --- |
| `SERVICENOW_INSTANCE_URL` | Yes | Full instance URL, must start with `https://` |
| `SERVICENOW_OAUTH_CLIENT_ID` | Yes | Public OAuth client ID |
| `SERVICENOW_OAUTH_SCOPE` | Yes | Space-separated scopes configured on the application; no `offline_access` |
| `SERVICENOW_OAUTH_REDIRECT_URI` | No | Default `http://127.0.0.1:8765/oauth/callback`; register this exact URI |
| `SERVICENOW_OAUTH_TIMEOUT_SECONDS` | No | Browser authorization timeout, default 180 seconds (1-600) |
| `MCP_TOOL_PACKAGE` | No | Tool package to load (default: `"full"`). See [[Tool-Packages]] |
| `SERVICENOW_ENV` | No | Environment label (default: `"dev"`). Write ops blocked on `"prod"` / `"production"` |
| `HTTPX_TIMEOUT_SECONDS` | No | ServiceNow HTTP timeout in seconds (default: `30`; valid range: `1-600`) |
| `METADATA_CACHE_TTL_SECONDS` | No | Metadata cache freshness in seconds (default: `300`; valid range: `1-86400`) |

The server also loads variables from `.env` and `.env.local` files in the working directory (`.env.local` takes precedence).

See [[Configuration]] for the full reference of all environment variables.

Remove `SERVICENOW_API_KEY`, `SERVICENOW_USERNAME`, and `SERVICENOW_PASSWORD`.
Non-empty legacy settings are rejected. The first outbound request opens the
local browser. Access tokens stay in memory; expiry requires fresh authorization.
Refresh tokens are not used. See [[Configuration]] for lifecycle and error behavior.

---

## MCP Client Configuration

Configure your MCP client to launch the server with the required OAuth environment variables. Replace the public client and scope placeholders with the ServiceNow application values.

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
        "SERVICENOW_OAUTH_CLIENT_ID": "<your-public-client-id>",
        "SERVICENOW_OAUTH_SCOPE": "<your-configured-scope>"
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
        "SERVICENOW_OAUTH_CLIENT_ID": "<your-public-client-id>",
        "SERVICENOW_OAUTH_SCOPE": "<your-configured-scope>"
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
        "SERVICENOW_OAUTH_CLIENT_ID": "<your-public-client-id>",
        "SERVICENOW_OAUTH_SCOPE": "<your-configured-scope>"
      }
    }
  }
}
```

### Generic stdio

For any client that supports stdio transport, launch the server with inline environment variables:

```bash
SERVICENOW_INSTANCE_URL=https://your-instance.service-now.com \
SERVICENOW_OAUTH_CLIENT_ID=your-public-client-id \
SERVICENOW_OAUTH_SCOPE=your-configured-scope \
uvx servicenow-platform-mcp
```

---

## First Steps

Once configured, try these example prompts with your AI agent:

- **"Describe the incident table schema"** - Explores table structure, field types, and metadata
- **"List open incidents"** - Queries records with a small field projection and choice label resolution
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

- Confirm the public client supports PKCE S256 without a client secret and permits the exact registered HTTP loopback URI.
- Confirm the configured scopes and user roles permit the API call.
- Keep the browser and process on the same machine. Close a conflicting listener or register another loopback port.
- After denial, timeout, or a 401, retry the tool call to authorize again. Allow enough tool-call time for browser interaction.

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
