# Architecture

Runtime map for contributors. The server is an async Python MCP server that
uses ServiceNow REST APIs over a local MCP stdio process.

## Runtime shape

`server.py` creates the application in this order:

1. Load and validate `Settings`.
2. Create one shared `OAuthPKCEProvider`.
3. Set up optional Sentry context.
4. Create one telemetry-aware shared `httpx.AsyncClient` and a
   `ServiceNowClientFactory`.
5. Create the `ChoiceRegistry` and `DictionaryRegistry`.
6. Register `list_tool_packages`.
7. Load the selected tool groups and inject dependencies by parameter name.
8. Run `MCPServer("servicenow-platform-mcp")` over stdio.

The server-lifetime HTTP client closes in the MCP lifespan. A directly created
`ServiceNowClient` owns and closes its own transport.

## Authentication

`OAuthPKCEProvider` implements public authorization-code PKCE S256:

- Construction does not open a browser.
- The first outbound ServiceNow call starts authorization.
- The callback binds the configured `127.0.0.1` port and validates state, path, and Host.
- The authorization request sends `response_type`, client ID, redirect URI, S256 challenge, configured scope, and state.
- The token request sends grant type, code, redirect URI, client ID, and PKCE verifier.
- The refresh request sends grant type, refresh token, and public client ID.
- It does not send a client secret, state, or HTTP Basic authentication to the token endpoint.
- The access token, refresh token, and monotonic access-token expiry stay in memory.
- Refresh-token rotation is supported; an omitted replacement retains the previous refresh token.
- Concurrent calls share one valid token.
- A REST 401 expires only the matching access token. The request is not replayed.

The configured OAuth scope defaults to `useraccount`. The setting accepts one or
more printable ASCII scope-token values separated by single spaces. ServiceNow
must enable each configured scope.

## Tool registration

Tool groups live under `servicenow_mcp.tools`. Bootstrap injects dependencies
only when a registration function declares them. Available dependency names are:

```text
mcp
settings
auth_provider
choices
dictionary
client_factory
```

`record_write.py` registers both `record_write` and `record_apply`.
`list_tool_packages` is registered outside the package loader, so it remains
available for every package, including `none`.

Tools use `@mcp.tool()` and `@tool_handler`. The handler adds redacted Sentry
context and converts tool exceptions into JSON error envelopes.

## Data and state

Operational tools serialize standard JSON envelopes with `status`, `data`, and
optional pagination, selection, and warnings metadata. `list_tool_packages`
returns its registry directly.

In-memory state includes:

- **OAuth provider:** access token, refresh token, and access-token expiry only.
- **PreviewTokenStore:** single-use mutation payloads, with a five-minute TTL.
- **Metadata caches:** choices, dictionary chains and fields, script-field discovery, and audit configuration.

The metadata cache has a configurable TTL and a 1,000-entry LRU bound. It does
not cache records, query results, flows, attachments, preview tokens, or audit
row counts. Encoded queries pass directly to `query`.

## Shared policy boundaries

`policy.py` applies denied tables, sensitive-field masking, query limits, date
requirements, identifier validation, and production write gating. Dictionary
lookups resolve inherited fields child-first. XML validation applies only to
supplied fields whose dictionary type is `xml`.

ServiceNow still controls REST policy, roles, ACLs, and final validation.

## Error flow

`@tool_handler` records the tool name and redacted arguments, then calls
`safe_tool_call()`. Expected policy, ACL, and other tool failures return
`status: "error"` JSON. Sentry receives exception data only when a DSN is
configured. Sensitive argument names are redacted before tool context is set.

## Source layout

```text
src/servicenow_mcp/
    server.py              # bootstrap, package loading, stdio entry point
    auth.py                # public OAuth PKCE provider
    oauth_callback.py      # one-shot loopback receiver
    client.py              # ServiceNow client factory and facade
    config.py              # environment settings and validation
    policy.py              # safety guardrails and write gating
    state.py               # preview token store
    telemetry.py           # bounded HTTP and cache counters
    sentry.py              # optional error tracking
    tools/                 # MCP tool groups and focused helpers
```

Read [[Development]] before changing registration or test patterns. Read
[[Safety-and-Policy]] before changing policy behavior.
