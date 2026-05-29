# Installation

How to install, configure, and run the servicenow-platform-mcp server.

## Prerequisites

- **Python 3.12 or later** (3.13 and 3.14 are also supported).
- **[uv](https://docs.astral.sh/uv/)** package manager.
- **A ServiceNow instance** with REST API access and a user account that can authenticate via HTTP Basic Auth.

## Install

```bash
git clone https://github.com/lassenn/servicenow-platform-mcp.git
cd servicenow-platform-mcp
uv sync --group dev
```

No build step is needed. The server runs directly from source.

## Credentials

```bash
cp .env.example .env.local
```

Open `.env.local` and fill in the three required variables:

```bash
SERVICENOW_INSTANCE_URL=https://your-instance.service-now.com
SERVICENOW_USERNAME=your_user
SERVICENOW_PASSWORD=your_password
```

`SERVICENOW_INSTANCE_URL` must use HTTPS. A trailing slash is stripped automatically.

> **Note:** `.env.example` may list preset package names that no longer exist in the registry. Refer to the [Tool Packages](docs/wiki/Tool-Packages.md) page for current presets.

## Choosing a tool package

The `MCP_TOOL_PACKAGE` environment variable controls which tools the server exposes. Four presets exist:

| Preset | Description |
|---|---|
| `full` | All 14 tools (read, write, investigate, flow, audit, catalog, build_query) |
| `readonly` | 10 tools - read and inspect, no create/update/delete |
| `core_readonly` | 5 tools - query, describe, attachment |
| `none` | Only `list_tool_packages` (1 tool) |

You can also pass a comma-separated list of tool group names for a custom surface (e.g. `MCP_TOOL_PACKAGE=query,describe,flow`).

See [Tool Packages](docs/wiki/Tool-Packages.md) for the full group list and tool counts.

## Production mode

Set `SERVICENOW_ENV=production` when pointing at a production instance.

This blocks **all** write operations regardless of table. The write gate returns an error envelope with the message `"Write operations are blocked in production environments"` for every call to `record_write`, `record_apply`, `attachment_write` (upload and delete), and catalog ordering actions. The gate is function-based and never raises - it returns a serialized error response to the caller.

The eight permanently denied tables (`sys_user_has_password`, `oauth_credential`, `oauth_entity`, `sys_certificate`, `sys_ssh_key`, `sys_credentials`, `discovery_credentials`, `sys_user_token`) are blocked in every environment, including dev.

## Running the server

The entry point is `servicenow_mcp.server:main`. It uses stdio transport - no HTTP listener is started.

```bash
uv run python -m servicenow_mcp.server
```

In practice, an MCP client spawns the process and communicates over stdin/stdout.

## Wiring an MCP client

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "servicenow": {
      "command": "uv",
      "args": ["--directory", "/path/to/servicenow-platform-mcp", "run", "python", "-m", "servicenow_mcp.server"],
      "env": {
        "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
        "SERVICENOW_USERNAME": "your_user",
        "SERVICENOW_PASSWORD": "your_password"
      }
    }
  }
}
```

### OpenCode

Add to your `opencode.json`:

```json
{
  "mcpServers": {
    "servicenow": {
      "command": "uv",
      "args": ["--directory", "/path/to/servicenow-platform-mcp", "run", "python", "-m", "servicenow_mcp.server"],
      "env": {
        "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
        "SERVICENOW_USERNAME": "your_user",
        "SERVICENOW_PASSWORD": "your_password"
      }
    }
  }
}
```

## Smoke test

Once your client connects, call `list_tool_packages` with no arguments. This tool:

- Does not contact ServiceNow (no auth required).
- Returns the package registry as a raw JSON payload (no correlation_id, no error envelope).
- Confirms the server process is running and the MCP transport is working.

If that succeeds, try `query` with `table="incident"` and `limit=1` to verify credentials and network connectivity.

## Troubleshooting

**Auth failure (401)**

- Verify `SERVICENOW_INSTANCE_URL` is the base URL with no path suffix.
- Confirm the username and password in `.env.local` match an active ServiceNow user.
- Check that Basic Auth is enabled on the instance (some organizations disable it).

**ForbiddenError on writes**

Two independent checks can block writes:

1. The eight denied tables are blocked unconditionally in every environment.
2. `SERVICENOW_ENV=production` (or `prod`) blocks all writes regardless of table.

Both return a serialized error envelope - the server never raises to the MCP client.

**script_path returns an opaque error**

The message `"script_path is not readable or is outside the allowed root"` collapses four distinct causes into one response for security:

1. The file does not exist.
2. The path is not a regular file.
3. The resolved path is outside `SCRIPT_ALLOWED_ROOT`.
4. A symlink escapes the allowed root after resolution.

Configuration errors (`SCRIPT_ALLOWED_ROOT` not set or not a directory) produce distinct, verbose messages.

## Next steps

- [Getting Started](docs/wiki/Getting-Started.md) - first workflows and patterns.
- [Tool Reference](docs/wiki/Tool-Reference.md) - per-tool parameters and behavior.
- [Safety and Policy](docs/wiki/Safety-and-Policy.md) - denied tables, masking, query safety, write gating.
