# Architecture

Deep technical architecture of the `servicenow-platform-mcp` server - an async Python MCP server for ServiceNow platform introspection and management.

## Overview

The server is built on:

- **FastMCP** - MCP server framework providing tool registration and transport handling.
- **httpx** - Async HTTP client for ServiceNow REST API communication.
- **JSON** - Standard JSON serialization for all tool responses.
- **pydantic-settings** - Configuration management via environment variables.
- **sentry-sdk** - Error tracking for invisible child-process environments.

Communication happens over **stdio transport**. The server runs as a child process of an AI agent, and all output is captured in a standardized JSON envelope.

## Unified Tool Surface

Version 0.10.0 introduced a unified 11-tool surface. Most tools are implemented as **dispatchers** that take an `action` parameter, reducing the total tool count while increasing flexibility.

### Key Implementation Patterns

- **Action Dispatchers:** Tools like `attachment`, `investigate`, and `service_catalog` use an `action` parameter to route requests to internal logic.
- **Encoded Queries:** The `query` tool accepts ServiceNow encoded query strings directly. The optional `build_query` helper (in the `full` package) is a stateless transform that compiles a JSON array of condition objects into an encoded query string for callers that prefer structured input.
- **Two-Stage Writes:** `record_write` (stage) and `record_apply` (commit) implement a mandatory safety flow for all mutations.
- **Helper Modules:** Shared logic is extracted into specialized helpers:
  - `_artifact.py`: Handles secure script file reads and `artifact_type` mapping.
  - `_describe_helpers.py`: Manages slim vs. verbose schema building.
  - `_record_helpers.py`: Handles mandatory field validation and diff generation.

## Server Bootstrap

The server entry point is `server.py`.

### Registration Pattern

The loader uses an unconditional 4-argument `register_tools()` pattern for all tool groups:

```python
def register_tools(
    mcp: FastMCP,
    settings: Settings,
    auth_provider: BasicAuthProvider,
    choices: ChoiceRegistry | None = None,
) -> None:
    ...
```

The bootstrap process dynamically imports modules from `servicenow_mcp.tools.unified` and registers them.

## Wire Format

All tool outputs are serialized using standard JSON. The TOON format from previous versions has been removed.

### `format_response()` Envelope

Every tool returns a standardized envelope:

```json
{
  "status": "success",
  "correlation_id": "uuid-v4",
  "data": { ... },
  "pagination": { "offset": 0, "limit": 100, "total": 500 },
  "warnings": []
}
```

## State Management

The server maintains minimal in-memory state via `state.py`.

- **PreviewTokenStore:** Mediates between `record_write` and `record_apply`. When `record_write` is called with `preview=true` (default), it stores the proposed mutation and returns a UUID token. `record_apply` then consumes this token to finalize the write. Tokens expire after 5 minutes.
- **ChoiceRegistry:** Lazy-loads choice labels from the `sys_choice` table on first use and caches them for the lifetime of the process.

The `QueryTokenStore` from previous versions has been deleted as agents now pass encoded queries directly.

## Error Handling Flow

The `@tool_handler` decorator (in `decorators.py`) wraps every tool invocation:

1. **Correlation ID:** Generates a unique UUID4 for the request.
2. **Sentry Context:** Attaches tool names and arguments to the Sentry scope.
3. **Safe Execution:** Wraps the tool in `safe_tool_call()`, which catches all exceptions (including `ForbiddenError` and `PolicyError`) and returns them as `status: "error"` JSON envelopes.

## Source Layout

```
src/servicenow_mcp/
    server.py              # Entry point, bootstrap
    client.py              # ServiceNow HTTP client (httpx)
    policy.py              # Safety guardrails & write gating
    state.py               # PreviewTokenStore
    tools/
        unified/           # The unified tools
            query.py
            describe.py
            record_write.py # Registers both record_write and record_apply
            attachment.py   # Registers both attachment and attachment_write
            investigate.py
            resolve_choice.py
            service_catalog.py
        _artifact.py       # Artifact script security
        _describe_helpers.py
        _record_helpers.py
```

## Client Retentions

Per ADR §2.3, the core `ServiceNowClient` retains several specialized methods to support the unified dispatchers:
- `list_reports` and `get_email`
- `get_import_set_record`
- Full `sc_*` method suite for Service Catalog
- Legacy investigation methods used by the `investigate` tool
