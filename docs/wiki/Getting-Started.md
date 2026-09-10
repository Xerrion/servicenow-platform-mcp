# Getting Started

This guide walks you through installing and configuring the ServiceNow Platform MCP server for use with your AI client.

---

## Prerequisites

- **Python 3.12 or later** - The server requires Python 3.12+ (3.12, 3.13, and 3.14 are supported)
- **A ServiceNow instance** - Developer, test, or production. The local write block depends on `SERVICENOW_ENV=prod` or `production`; it does not detect the instance type.
- **ServiceNow authentication** - A public OAuth authorization-code PKCE S256 client. Set Public Client=true, enable `useraccount`, and register the exact loopback redirect URI. Use a user with the required roles. The browser and stdio process must run on the same machine.
- **An MCP-compatible AI client** - [OpenCode](https://opencode.ai), [Claude Desktop](https://claude.ai/download), [VS Code Copilot](https://code.visualstudio.com/), [Cursor](https://cursor.sh/), or any client supporting the [Model Context Protocol](https://modelcontextprotocol.io/)

---

## Installation

### Local source checkout

```bash
git clone https://github.com/Xerrion/servicenow-platform-mcp.git
cd servicenow-platform-mcp
uv sync --group dev
```

Configure authentication below, then let the MCP client launch
`uv run servicenow-platform-mcp` from the checkout.

### Managed published package

Confirm that your package index has a release with the public PKCE behavior
described here before using a published package. A package version number alone
does not establish that it includes changes on an unreleased branch.

```bash
uvx servicenow-platform-mcp
```

This downloads and runs the server in an isolated environment. No permanent installation needed.

> **Note:** The server communicates via stdio transport - it is launched by your MCP client as a subprocess, not run as a standalone service. You do not need to start it manually.

---

## Environment Variables

### ServiceNow Application Registry

In **System OAuth > Application Registry**, create or select the application:

1. Set **Public Client** to `true`.
2. Enable authorization-code PKCE with **S256**.
3. Enable the `useraccount` scope.
4. Register the exact redirect URL `http://127.0.0.1:8765/oauth/callback`.
5. Save the application and copy its client ID.

### Local `.env.local`

Create `.env.local` in the working directory used to start the MCP server:

```dotenv
SERVICENOW_INSTANCE_URL=https://your-instance.service-now.com
SERVICENOW_OAUTH_CLIENT_ID=your-public-client-id
SERVICENOW_OAUTH_SCOPE=useraccount
SERVICENOW_OAUTH_REDIRECT_URI=http://127.0.0.1:8765/oauth/callback
SERVICENOW_OAUTH_TIMEOUT_SECONDS=180
MCP_TOOL_PACKAGE=readonly
SERVICENOW_ENV=dev
```

Use an HTTPS instance origin without credentials, path, query, or fragment.
The scope must be exactly `useraccount`. The redirect URI must match the
registered URL and the format `http://127.0.0.1:<port>/oauth/callback`, with port
`1024`-`65535`. `localhost`, other paths, query strings, and fragments are not
accepted. Register the complete new URL if you change the port.

The server loads `.env`, then `.env.local`, from its working directory. Process
environment variables override both files. Restart the full server after
changing settings. Never commit these files.

Remove `SERVICENOW_API_KEY`, `SERVICENOW_USERNAME`, and `SERVICENOW_PASSWORD`.
Non-empty values fail startup; there is no Basic Auth or API-key fallback.
A stale `SERVICENOW_OAUTH_CLIENT_SECRET` is ignored, not used or rejected.
Remove it rather than configuring a secret. See [[Configuration]] for the
complete settings reference.

---

## MCP Client Configuration

Configure the MCP client to launch the server over stdio with the required
environment. This generic example uses a prepared source checkout:

```json
{
  "command": "uv",
  "args": ["run", "servicenow-platform-mcp"],
  "cwd": "/path/to/servicenow-platform-mcp",
  "env": {
    "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
    "SERVICENOW_OAUTH_CLIENT_ID": "your-public-client-id",
    "SERVICENOW_OAUTH_SCOPE": "useraccount",
    "SERVICENOW_OAUTH_REDIRECT_URI": "http://127.0.0.1:8765/oauth/callback",
    "MCP_TOOL_PACKAGE": "readonly"
  }
}
```

The enclosing JSON structure and working-directory field depend on the client;
`cwd` is not an MCP protocol field. Use the equivalent fields documented by
your client. Do not put tokens, authorization codes, PKCE verifiers, callback
query strings, API keys, Basic credentials, or client secrets in this file.

---

## First Steps

Call `list_tool_packages` to confirm that the process responds. It lists presets
and groups without contacting ServiceNow; it does not report the active package.
Then try `query(table="incident", fields="sys_id,number", limit=1)` if the
user has access to that table.

The first tool call that needs ServiceNow opens the default browser. The browser
and MCP process must run on the same machine. Authorize as the ServiceNow user
whose roles and ACLs should apply. The public client ID identifies the application,
not a separate service account.

Only the access token and its expiry stay in memory. Restart or expiry requires
browser authorization on the next outbound call. REST requests use Bearer headers,
never tokens in URLs. A REST 401 invalidates only the matching token and does
not replay the request. The next outbound call authorizes again unless a newer
concurrent grant exists.

Once configured, try these example prompts with your AI agent:

- **"Describe the incident table schema"** - Explores table structure, field types, and metadata
- **"List open incidents"** - Queries records with a small field projection and choice label resolution
- **"Show me what business rules run on the incident table"** - Inspects platform artifacts
- **"Trace the debug log for incident INC0010001"** - Builds an event timeline from system logs
- **"What update sets were created this week?"** - Change intelligence across your instance
- **"Run a table health investigation on cmdb_ci"** - Automated analysis with findings and recommendations

---

## AI Agent Setup

See [INSTALL.md](../../INSTALL.md) for operator setup, package selection,
permissions, and policy migration.

---

## Troubleshooting

### Connection refused or timeout

- Verify `SERVICENOW_INSTANCE_URL` starts with `https://` (HTTP is not supported)
- Check that your ServiceNow instance is reachable from your network
- Trailing slashes are stripped automatically - `https://dev12345.service-now.com/` works fine

### Authentication errors

| Observed error or event | Safe action |
| --- | --- |
| `Cannot bind OAuth loopback port` | Close only a known conflicting listener, or configure and register another allowed port. |
| `ServiceNow authorization timed out` | Complete authorization on the same machine before the timeout. Check the exact callback URL and retry. |
| Missing or invalid scope | Set exactly `useraccount` locally and enable it on the application. |
| `Cannot open the local browser` | Check the local browser setup. This is distinct from a ServiceNow authorization-page error. |
| Error on the ServiceNow authorization page | Check **Public Client=true**, PKCE S256, client ID, scope, and exact redirect URL. |
| `OAuth token exchange rejected (HTTP ...)` | Check the public application and OAuth settings. Token exchange failure is separate from a later REST 401. |
| REST 401 or `User Not Authenticated` | The request was not replayed. Authorize on the next outbound call. If a new token also fails, check REST policy, scopes, and user access with the administrator. |
| REST 401 plus administrator-observed `WWW-Authenticate: API_KEY` | Check for an API-key-only policy. Migrate only the affected policy; the server omits this scheme from sanitized evidence. |
| HTTP 403 | Check REST-resource permissions, user roles, and table and field ACLs. Not every 403 identifies a table ACL denial. |
| Token expiry or server restart | Complete browser authorization again on the next outbound call. |

An API-key-only REST API access policy can reject OAuth Bearer even after token
issuance. Ask the administrator to adjust only the policy for the failed
resource and method. Preserve unrelated policies and test a small read-only
request with the intended user. Do not disable global protection or add an API
key to the configuration. See [[Configuration]] for the migration procedure.

Never log or persist tokens, authorization codes, PKCE verifiers, or callback
URLs and query strings. Report only sanitized error evidence and an allowed
transaction ID when available.

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
