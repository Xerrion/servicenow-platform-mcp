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

Communication happens over **stdio transport**. The server runs as a child process of an AI agent, and all output is captured in a standardized JSON envelope.

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
Its first outbound call opens the local browser for authorization-code flow.
`oauth_callback.py` owns the temporary IPv4 loopback receiver. Tokens stay in
memory with a monotonic expiry; concurrent calls share authorization and renewal.
A configured client secret selects confidential authorization without PKCE and
authenticates both token-endpoint grants. No secret selects public PKCE S256.
Both modes require scope locally and validate authorization state in the callback;
neither token grant sends state. Confidential code exchange matches the Yokohama contract.
Authorization scope/state remain client compatibility behavior, not documented
Yokohama requirements. Public PKCE compatibility with Yokohama is unverified.
Only confidential clients use issued refresh tokens to renew expired or rejected
access tokens on the next call. Public clients authorize again instead.
A REST 401 never replays the API call. Missing refresh tokens or HTTP 400
`invalid_grant` require new authorization. Legacy credentials are rejected.
This changes outbound authentication only; MCP continues to use stdio.

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

All tool outputs are serialized using standard JSON. The TOON format from previous versions has been removed. Read tools can add top-level `selection` metadata that identifies returned and omitted fields or sections, effective limits, and truncation.

### `format_response()` Envelope

Every tool returns a standardized envelope:

```json
{
  "status": "success",
  "correlation_id": "uuid-v4",
  "data": { ... },
  "pagination": { "offset": 0, "limit": 100, "total": 500 },
  "selection": { "mode": "explicit", "returned_fields": ["sys_id", "number"] },
  "warnings": []
}
```

## State Management

The server maintains minimal in-memory state via `state.py`.

- **PreviewTokenStore:** Mediates between `record_write` and `record_apply`. When `record_write` is called with `preview=true` (default), it stores the proposed mutation and returns a UUID token. `record_apply` then consumes this token to finalize the write. Tokens expire after 5 minutes.
- **Metadata cache:** Caches choices, dictionary chain and field metadata, script-field discovery, and audit configuration with a configurable TTL. Entries use a 1,000-entry LRU bound and explicit invalidation. Mutable records, query results, flows, attachments, previews, and audit row counts are not cached.

The `QueryTokenStore` from previous versions has been deleted as agents now pass encoded queries directly.

## Error Handling Flow

The `@tool_handler` decorator (in `decorators.py`) wraps every tool invocation:

1. **Correlation ID:** Generates a unique UUID4 for the request.
2. **Sentry Context:** Attaches tool names and arguments to the Sentry scope.
3. **Safe Execution:** Wraps the tool in `safe_tool_call()`, which catches all exceptions (including `ForbiddenError` and `PolicyError`) and returns them as `status: "error"` JSON envelopes.

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
