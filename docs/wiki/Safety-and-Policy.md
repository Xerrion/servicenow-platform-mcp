# Safety and Policy

The server enforces multiple layers of safety guardrails to prevent accidental data exposure, unbounded queries, and unintended modifications. These policies are applied automatically - tool functions do not need to implement them manually.

For the complete list of tools affected by these policies, see [[Tool-Reference]]. For environment variable configuration, see [[Configuration]].

---

## Overview

| Layer | Purpose |
|---|---|
| Table access control | Blocks access to security-sensitive tables |
| Sensitive field masking | Masks password, token, and credential fields in responses |
| Query safety | Enforces row limits and date-bounded filters on large tables |
| Write gating | Blocks all write operations in production environments |
| Input validation | Validates table names, field names, and sys_id formats |
| Error handling | Catches all exceptions and returns serialized error envelopes |
| Attachment safety | Enforces size limits on attachment transfers |

---

## Table Access Control

Eight security-sensitive tables are permanently blocked from all operations (read and write). Any attempt to query, describe, or write to these tables raises a `PolicyError`.

### Denied Tables

| Table | Reason |
|---|---|
| `sys_user_has_password` | Password hashes |
| `oauth_credential` | OAuth credentials |
| `oauth_entity` | OAuth entity configuration |
| `sys_certificate` | SSL/TLS certificates |
| `sys_ssh_key` | SSH private keys |
| `sys_credentials` | Stored credentials |
| `discovery_credentials` | Discovery credentials |
| `sys_user_token` | User authentication tokens |

These tables are blocked regardless of environment, tool package, or user role. There is no override mechanism.

### How It Works

The `check_table_access()` function is called before any ServiceNow API request. It compares the requested table name (lowercased) against the deny list and raises `PolicyError` if matched. The `@tool_handler` decorator catches this exception and returns a serialized error envelope - the tool never reaches the ServiceNow API.

---

## Sensitive Field Masking

Fields matching sensitive name patterns are automatically masked in all tool responses. The original values are never returned to the AI agent.

### Masked Patterns

Six regex patterns (case-insensitive) trigger masking:

| Pattern | Matches |
|---|---|
| `password` | Any field containing "password" (e.g., `user_password`, `password_hash`) |
| `token` | Any field containing "token" (e.g., `access_token`, `token_value`) |
| `secret` | Any field containing "secret" (e.g., `client_secret`, `secret_key`) |
| `credential` | Any field containing "credential" (e.g., `credential_id`, `credentials`) |
| `api_key` | Any field containing "api_key" (e.g., `rest_api_key`) |
| `private_key` | Any field containing "private_key" (e.g., `ssh_private_key`) |

Masked fields display as `***MASKED***` in the response.

### Audit Entry Masking

Audit records (`sys_audit`) receive special treatment. In audit entries, the actual field name is stored in a metadata key (`fieldname` or `field`), while values are in `oldvalue`/`newvalue` (or `old_value`/`new_value`). The `mask_audit_entry()` function inspects the field name value and masks the corresponding value fields when it matches a sensitive pattern.

---

## Query Safety

Query safety prevents unbounded queries that could overload the ServiceNow instance or return excessive data.

### Row Limits

- User-supplied `limit` parameters are capped at `MAX_ROW_LIMIT` (default: 100, configurable up to 10000)
- When no limit is specified, `MAX_ROW_LIMIT` is used as the default
- The limit is floored at 1 to prevent zero or negative values
- Internal metadata queries use a separate `INTERNAL_QUERY_LIMIT` of 1000

### Large Table Protection

Tables listed in `LARGE_TABLE_NAMES_CSV` require date-bounded filters in their queries. The default large tables are:

- `syslog`
- `sys_audit`
- `sys_log_transaction`
- `sys_email_log`

When querying a large table, the query must contain a recognized date field with a comparison operator. Recognized date fields are:

- `sys_created_on`
- `sys_updated_on`
- `opened_at`
- `closed_at`
- `sys_recorded_at`

Accepted comparison operators include `>`, `>=`, `<`, `<=`, `BETWEEN`, and `javascript:gs.*Ago` functions (e.g., `gs.hoursAgo`, `gs.daysAgo`).

If the query does not contain a date-bounded filter, a `QuerySafetyError` is raised with a descriptive message explaining the requirement.

### Configuring Large Tables

Override the default large table list via `LARGE_TABLE_NAMES_CSV`:

```bash
LARGE_TABLE_NAMES_CSV="syslog,sys_audit,sys_log_transaction,sys_email_log,my_custom_log_table"
```

---

## Write Gating

All write operations are blocked when the server is running in production mode. This is a deliberate safety guardrail with no override mechanism.

### What Triggers Production Mode

The `is_production` property returns `True` when `SERVICENOW_ENV` is set to `"prod"` or `"production"` (case-insensitive). Write operations are also blocked for any table on the denied tables list, regardless of environment.

### What Gets Blocked

When write gating is active, the following operations return an error envelope instead of executing:

- **Record operations** - `record_create`, `record_update`, `record_delete`, and their preview variants
- **Artifact operations** - `artifact_create`, `artifact_update`
- **Attachment operations** - `attachment_upload`, `attachment_delete`
- **Domain write operations** - `incident_create`, `incident_update`, `incident_resolve`, `change_create`, `change_update`, `problem_create`, `problem_update`, `problem_root_cause`, `request_item_update`, `knowledge_create`, `knowledge_update`, `sc_order_now`, `sc_add_to_cart`, `sc_cart_submit`, `sc_cart_checkout`

### What Still Works

All read operations work normally in production mode:

- Table schema description and querying
- Record fetching and reference discovery
- Attachment listing and downloading
- Change intelligence (update set inspection, diffing, audit trails)
- Debug and trace operations
- Investigations and documentation generation
- Workflow and flow analysis
- Listing and fetching domain records (incidents, changes, etc.)

### How It Works

Write tool functions call `write_gate(table, settings, correlation_id)` before performing any mutation. If writes are blocked, the function returns a pre-formatted error envelope immediately - the ServiceNow API is never contacted. The `write_blocked_reason()` function checks two conditions:

1. Is the table on the denied tables list?
2. Is the environment set to production?

If either condition is true, a human-readable reason string is returned.

---

## Input Validation

All table names, field names, and sys_id values are validated before use.

### Identifier Validation

Table and field names must match the pattern `^[a-z0-9_]+(\.[a-z0-9_]+)*$` - lowercase alphanumeric characters and underscores, with optional dot-walked segments (e.g., `change_request.number`, `child.sys_id`).

Invalid identifiers raise a `ValueError` with a descriptive message. This prevents injection of malicious characters into ServiceNow API URLs and query parameters.

### sys_id Validation

sys_id values must be exactly 32 lowercase hexadecimal characters. Invalid sys_id values raise a `ValueError`.

### Artifact Payload Validation

The `artifact_create` and `artifact_update` tools validate their JSON payloads:

- The payload must be a dictionary (not a list or primitive)
- All dictionary keys are validated with `validate_identifier()` before submission
- Artifact types are validated against the known `WRITABLE_ARTIFACT_TABLES` mapping

### Script Path Security

The `artifact_create` and `artifact_update` tools accept an optional `script_path` parameter for local script file injection:

- The path is resolved via `Path.resolve(strict=True)` to prevent symlink and traversal attacks
- When `SCRIPT_ALLOWED_ROOT` is configured, the resolved path must be under that root directory
- Files are limited to 1 MB (`MAX_SCRIPT_FILE_BYTES = 1,048,576`)
- Files are read as UTF-8

---

## Error Handling

Tool functions never raise exceptions to the MCP transport. All errors are caught and returned as serialized error envelopes.

### The Error Boundary

The `@tool_handler` decorator wraps every tool invocation in `safe_tool_call()`, which catches all exceptions:

- `ForbiddenError` (HTTP 403 / ACL denials) - returned as `"Access denied by ServiceNow ACL: ..."` error envelope
- All other exceptions - returned as error envelope with the exception message

Both paths capture the exception to Sentry (when configured) for visibility.

### Exception Hierarchy

| Exception | HTTP Status | Trigger |
|---|---|---|
| `AuthError` | 401 | Invalid credentials |
| `ForbiddenError` | 403 | Insufficient permissions (ServiceNow ACL) |
| `NotFoundError` | 404 | Record or resource not found |
| `ServerError` | 5xx | ServiceNow server error |
| `PolicyError` | 403 | Denied table access |
| `QuerySafetyError` | 403 | Unsafe query (missing date filter on large table) |
| `ServiceNowMCPError` | 400+ | Other HTTP client errors |

### Error Envelope Format

Error responses use the same TOON-serialized envelope as success responses:

```
status: "error"
correlation_id: "<uuid>"
data: null
error:
  message: "<descriptive error message>"
```

---

## Attachment Safety

Attachment transfers are limited to prevent excessive memory usage and transfer sizes.

- **Maximum size:** 10 MB (`10 * 1024 * 1024` bytes)
- **Upload format:** `content_base64` - base64-encoded file content
- **Download format:** `content_base64` - base64-encoded file content returned in the response
- **Write gating applies:** `attachment_upload` and `attachment_delete` are blocked in production mode

---

## Summary of Protections

| Protection | Always Active | Production Only |
|---|---|---|
| Denied table list (8 tables) | Yes | - |
| Sensitive field masking (6 patterns) | Yes | - |
| Row limit capping | Yes | - |
| Large table date-bounded filter requirement | Yes | - |
| Input validation (identifiers, sys_ids) | Yes | - |
| Error envelope wrapping | Yes | - |
| Attachment size limit (10 MB) | Yes | - |
| Write operation blocking | - | Yes |

---

## Next Steps

- [[Configuration]] - Environment variables that control safety behavior (`SERVICENOW_ENV`, `MAX_ROW_LIMIT`, `LARGE_TABLE_NAMES_CSV`, `SCRIPT_ALLOWED_ROOT`)
- [[Tool-Reference]] - Complete tool reference with write/read classification
- [[Tool-Packages]] - Read-only packages for safe production exploration
- [[Architecture]] - Server internals and error handling flow
