# Architecture

Deep technical architecture of the `servicenow-platform-mcp` server - an async Python MCP server for ServiceNow platform introspection and management.

## Overview

The server is built on:

- **MCPServer** - MCP SDK v2 server class, imported from `mcp.server`, providing tool registration and transport handling. The project requires `mcp>=2.1.1`.
- **httpx** - Async HTTP client for ServiceNow REST API communication.
- **JSON** - Standard JSON serialization for all tool responses.
- **pydantic-settings** - Configuration management via environment variables.
- **sentry-sdk** - Error tracking for invisible child-process environments.

Repeated tool calls share one server-lifetime `httpx.AsyncClient` connection pool. This reduces connection setup overhead and records bounded telemetry for HTTP request count, duration, response bytes, and shared-pool usage. A directly constructed `ServiceNowClient` remains responsible for its own transport and closes it when its context ends.

The application keeps `httpx` as a direct dependency for ServiceNow REST API communication. MCP SDK v2's `httpx2` dependency is transitive.

Communication happens over **stdio transport**. The server runs as a child process of an MCP client. Operational tools return a standardized JSON envelope; `list_tool_packages` returns the registry directly.

## Unified Tool Surface

The current `full` preset exposes 15 tools, including the always-available `list_tool_packages` tool. Most tools are implemented as **dispatchers** that take an `action` parameter, reducing the total tool count while increasing flexibility.

### Key Implementation Patterns

- **Action Dispatchers:** Tools like `attachment`, `investigate`, and `service_catalog` use an `action` parameter to route requests to internal logic.
- **Encoded Queries:** The `query` tool accepts ServiceNow encoded query strings directly. Callers can copy filter breadcrumbs from ServiceNow or construct encoded query strings directly. Query safety remains enforced by `query`.
- **Two-Stage Writes:** `record_write` (stage) and `record_apply` (commit) implement the default safety flow for record mutations. `record_write(preview=false)` explicitly requests an immediate write.
- **Helper Modules:** Shared logic is extracted into specialized helpers:
  - `_artifact.py`: Checks XML well-formedness for inline writes. `DictionaryRegistry.get_fields` resolves types for supplied fields only, with child-first inheritance and narrow queries. It does not load the full script-field list.
  - `_dictionary.py`: `DictionaryRegistry` — runtime discovery of script-bearing fields per table by walking `sys_db_object.super_class` and filtering `sys_dictionary` rows. Replaces the previous hardcoded artifact catalog.
  - `_describe_helpers.py`: Manages slim vs. verbose schema building.
  - `_record_helpers.py`: Handles mandatory field validation and diff generation.

## Server Bootstrap

The server entry point is `server.py`.

### Authentication

Bootstrap creates one `OAuthPKCEProvider` shared by all ServiceNow clients.
The provider implements public authorization-code PKCE S256. Construction does
not open a browser. The first outbound ServiceNow call starts authorization.

`GET /oauth_auth.do` sends exactly:

- `response_type=code`
- `client_id`
- `redirect_uri`
- `code_challenge`
- `code_challenge_method=S256`
- `scope=useraccount`
- random `state`

`POST /oauth_token.do` sends exactly these form fields:

- `grant_type=authorization_code`
- `code`
- `redirect_uri`
- `client_id`
- `code_verifier`

Both endpoints use the configured HTTPS instance. The redirect URI is identical
in both requests. No state, client secret, or HTTP Basic authentication is sent
to the token endpoint. The callback code is not a REST credential.

`oauth_callback.py` binds the configured `127.0.0.1` port before opening the
browser. It validates callback state, path, and Host. The listener and accepted
connections close before exchange and on denial, timeout, or cancellation.
The browser and process must run on the same machine. MCP remains stdio;
the temporary OAuth callback is not an MCP HTTP transport.

The REST identity is the ServiceNow user who completes browser authorization.
The client ID identifies the application. User roles, table and field ACLs,
and REST-resource policies continue to control access.

Only the access token and its monotonic expiry stay in memory. Tokens require
a positive `expires_in`; expiry subtracts the smaller of 30 seconds or 10% of
the lifetime. REST requests use `Authorization: Bearer <access_token>`, preserving
opaque token values. Tokens are never persisted or sent in URLs.

Concurrent calls share one successful authorization and reuse the valid token
within the process. Restart or expiry requires browser authorization on the next
outbound call. A REST 401 discards only the matching token and does not replay
the request; it cannot invalidate a newer concurrent grant. The next outbound
call authorizes again if no valid token remains.

Non-empty `SERVICENOW_API_KEY`, `SERVICENOW_USERNAME`, and `SERVICENOW_PASSWORD`
values fail startup. `SERVICENOW_OAUTH_CLIENT_SECRET` is not a settings field;
stale environment or dotenv values are ignored. There is no legacy fallback.

REST 401 evidence is bounded and allowlisted by `_rest_auth_evidence.py`.
Unknown authentication schemes, including `API_KEY`, are omitted. An
administrator may observe `WWW-Authenticate: API_KEY` outside the tool response;
that is a reason to inspect the applicable REST policy, not to change PKCE or
disable global protection. See [[Configuration]] for policy migration.

### Registration Pattern

The loader uses one `register_tools()` signature for all tool groups:

```python
from mcp.server import MCPServer


def register_tools(
    mcp: MCPServer,
    settings: Settings,
    auth_provider: OAuthPKCEProvider,
    choices: ChoiceRegistry | None = None,
    dictionary: DictionaryRegistry | None = None,
    client_factory: ServiceNowClientProvider | None = None,
) -> None: ...
```

The bootstrap process dynamically imports modules from `servicenow_mcp.tools` and registers them.

The server is constructed as `MCPServer("servicenow-platform-mcp")`. Tool decorators remain `@mcp.tool()` and `@tool_handler`. The entry point runs `mcp.run(transport="stdio")`.

MCP protocol model fields use snake_case Python names, such as `input_schema` and `structured_content`.

## Wire Format

All tool outputs are serialized using standard JSON. The TOON format from previous versions has been removed. Read tools can add top-level `selection` metadata that identifies selected fields or sections, effective limits, and truncation. Use the supplied continuation metadata to complete bounded reads.

### `format_response()` Envelope

Operational tools return this envelope shape. `list_tool_packages` returns
the registry directly. Optional metadata appears only when supplied, and an
empty warnings list is omitted:

```json
{
  "status": "success",
  "data": {},
  "pagination": { "offset": 0, "limit": 100, "total": 500 },
  "selection": { "mode": "explicit", "returned_fields": ["sys_id", "number"] }
}
```

## State Management

The server keeps state in memory:

- **OAuthPKCEProvider (`auth.py`):** Holds the access token and expiry for outbound ServiceNow calls. It does not persist tokens.
- **PreviewTokenStore (`state.py`):** Mediates between `record_write` and `record_apply`. When `record_write` is called with `preview=true` (default), it stores the proposed mutation and returns a UUID token. `record_apply` consumes it before the write attempt. Tokens expire after 5 minutes.
- **Metadata cache:** Caches choices, dictionary chain and field metadata, script-field discovery, and audit configuration with a configurable TTL. Entries use a 1,000-entry LRU bound and explicit invalidation. Mutable records, query results, flows, attachments, previews, and audit row counts are not cached.

The `QueryTokenStore` from previous versions has been deleted as agents now pass encoded queries directly.

## Error Handling Flow

The `@tool_handler` decorator (in `decorators.py`) wraps operational tools,
excluding the bootstrap `list_tool_packages` tool:

1. **Sentry Context:** Attaches tool names and redacted arguments to the Sentry scope.
2. **Safe Execution:** Wraps the tool in `safe_tool_call()`, which catches all exceptions (including `ForbiddenError` and `PolicyError`) and returns them as `status: "error"` JSON envelopes.

The decorator preserves the tool signature without injecting internal arguments.

## Source Layout

```text
src/servicenow_mcp/
    server.py              # Entry point, bootstrap
    client.py              # ServiceNow HTTP client (httpx)
    policy.py              # Safety guardrails & write gating
    state.py               # PreviewTokenStore
    tools/
        query.py
        describe.py
        record_write.py # Registers both record_write and record_apply
        attachment.py       # Registers attachment reads
        attachment_write.py # Registers gated attachment writes
        investigate.py
        resolve_choice.py
        service_catalog.py
        audit.py
        flow.py
        _artifact.py       # Artifact script security
        _audit.py          # AuditRegistry: super_class walk + verdict resolution
        _dictionary.py     # DictionaryRegistry: script-bearing field discovery
        _flow_values.py    # gzip+base64+JSON values blob decoder
        _describe_helpers.py
        _record_helpers.py
```

## Client Retentions

Per ADR §2.3, the core `ServiceNowClient` retains several specialized methods to support the unified dispatchers:

- `list_reports` and `get_email`
- `get_import_set_record`
- Full `sc_*` method suite for Service Catalog
- Legacy investigation methods used by the `investigate` tool
