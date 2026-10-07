# How the MCP server works

Use this page when changing the MCP server or investigating its behavior.
For changes to scripts or configuration on an instance, use [[Instance-Development]].
For connection setup, use [[Getting-Started]].

The supported live connection runs as a local Python process. The AI app sends
MCP requests over stdio. The server then calls ServiceNow REST APIs with the
signed-in user's authorization.

```text
AI app
  | local MCP connection (stdio)
  v
Registered tool
  | input checks and server policy
  v
ServiceNow client
  | HTTPS request with the user's OAuth token
  v
ServiceNow REST API
  | ServiceNow roles, API policies, and ACLs
  v
Record or error response
```

An ACL is an access control rule. ServiceNow decides which records and fields
the account can read or change. Selecting tools in the MCP server does not
grant additional ServiceNow permissions.

## MCP connection and OAuth callback

The AI app communicates with the server over stdio.
The temporary local HTTP callback handles browser authorization.
It does not accept MCP tool requests.

## Local startup and cleanup

`server.py` prepares the stdio application in this order:

1. Load and check `Settings`, then create the shared `OAuthPKCEProvider`.
2. Initialize optional Sentry diagnostics and a shared HTTP client.
3. Create the client factory, choice registry, and dictionary registry.
4. Register `list_tool_packages` and the selected tool groups.
5. Run `MCPServer("servicenow-platform-mcp")` over stdio.

The choice registry resolves choice values and labels. The dictionary registry
reads field definitions, including fields inherited from parent tables.
The client factory gives tools clients that use the shared HTTP connection pool.
A pool lets repeated requests reuse network connections.

The MCP lifespan closes the shared HTTP client when the application stops.
A directly created `ServiceNowClient` owns its own transport and closes it
when its context ends. The stdio entry point also shuts down Sentry.

## Browser authorization and token renewal

`OAuthPKCEProvider` uses authorization-code OAuth with PKCE S256. PKCE binds the
browser authorization to a temporary verifier held by the local server.
This public-client flow does not use a client secret.

Constructing the provider does not open a browser. The first outbound ServiceNow
call starts authorization. The one-time callback receiver listens on the configured
`127.0.0.1` port and checks the callback state, path, and Host header.

| Request | Values sent |
| --- | --- |
| Browser authorization | Response type, public client ID, redirect URL, S256 challenge, scope, and state. |
| Token exchange | Grant type, authorization code, redirect URL, public client ID, and PKCE verifier. |
| Token renewal | Grant type, refresh token, and public client ID. |

The token endpoint receives no client secret, state, or HTTP Basic credentials.
The configured scope defaults to `useraccount`. Custom values contain printable
ASCII scope tokens separated by single spaces. ServiceNow must enable each scope.

Access and refresh tokens stay in memory. The provider measures access-token
expiry with a monotonic clock, which does not change with wall-clock adjustments.
Concurrent calls share authorization and one valid token.

The provider renews an expired token when a refresh token is available.
It accepts a rotated refresh token and retains the previous one if ServiceNow
omits a replacement. A rejected refresh can require browser authorization again.

A REST 401 invalidates only the matching access token. It cannot invalidate a
newer token from another call. The failed REST request is not replayed.
Restarting the server clears its tokens and requires authorization again.

## Tool registration and responses

Groups live under `servicenow_mcp.tools`. Bootstrap calls each selected group's
`register_tools` function. It supplies only the dependencies declared in that
function's signature:

```text
mcp
settings
auth_provider
choices
dictionary
client_factory
telemetry
```

`record_write.py` registers both `record_write` and `record_apply`.
`list_tool_packages` registers outside the package loader. It remains available
with every stdio package, including `none`. It lists the package registry, not
the active package.

Operational tools return JSON strings with `status` and `data`.
Responses can also contain pagination, truncation details, and warnings.
`list_tool_packages` returns the registry directly.

Tools use `@mcp.tool()` and `@tool_handler`. The handler records the tool name,
adds redacted diagnostic context, and calls `safe_tool_call()`.
Expected policy, access, and tool failures return `status: "error"`.
Unclassified exceptions return a generic internal error.
See [[Telemetry]] for diagnostic evidence and its limits.

## State held by the local server

| State | Lifetime and purpose |
| --- | --- |
| OAuth tokens | Memory only, until expiry, renewal, rejection, or process exit. |
| Record previews | Single-use payloads in `PreviewTokenStore`. Tokens expire after five minutes and disappear on restart. |
| Metadata | Cached choice values, dictionary fields and inheritance, script-field discovery, and audit configuration. |

Each metadata cache has a configurable time to live (TTL), defaulting to
300 seconds. It holds up to 1,000 entries and removes the least recently used
entry when full. Concurrent callers can share one metadata load.

Metadata caches do not store records, query results, flows, attachments, preview
tokens, or audit row counts. Encoded query text passes to the query tool's
ServiceNow request. Advisory dictionary checks do not cover every part of that text.

Record previews default to inspection, but callers can request an immediate write.
The server does not enforce human approval. Some other mutations apply directly.
See [[Safety-and-Policy]] for the complete change controls.

## Where policy applies

`policy.py` controls denied tables, sensitive-field masking, query limits, date
requirements, identifier checks, and production write protection.
Dictionary lookups resolve inherited fields child-first.
XML checks apply only to supplied fields with dictionary type `xml`.

These controls supplement ServiceNow's REST policies, roles, ACLs, and final
validation. `SERVICENOW_ENV` controls local write protection. It does not detect
the environment of the configured instance.

## Find the source

Paths below are relative to `src/servicenow_mcp/`.

| File or folder | Responsibility |
| --- | --- |
| `server.py`, `packages.py` | Local startup, tool selection, and command entry point. |
| `auth.py`, `oauth_callback.py` | Browser authorization, token renewal, and local callback checks. |
| `client.py`, `_client_*.py` | ServiceNow client factory, REST calls, and specialized API operations. |
| `config.py`, `policy.py` | Settings, validation, masking, and write protection. |
| `state.py`, `metadata_cache.py` | Record previews and metadata cache behavior. |
| `choices.py`, `tools/_dictionary.py` | Choice labels, table fields, and inheritance. |
| `tools/`, `decorators.py`, `tool_errors.py`, `response.py` | Tool implementations, diagnostic wrappers, and JSON responses. |
| `telemetry.py`, `_http_diagnostics.py`, `sentry.py` | Local timing, bounded request evidence, and optional Sentry reporting. |

Use [[Development]] for the contributor workflow. Check [[Safety-and-Policy]]
before changing controls that affect record access or writes.
