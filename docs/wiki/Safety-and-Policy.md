# Safety and Policy

The server enforces multiple layers of safety guardrails to prevent accidental data exposure, unbounded queries, and unintended modifications. These policies are applied automatically by the platform layer.

---

## Overview

| Layer | Purpose |
|---|---|
| Table access control | Blocks access to security-sensitive tables (e.g., `sys_credentials`) |
| Sensitive field masking | Masks passwords, tokens, and secrets in responses |
| Query safety | Enforces row limits and date-bounded filters on large tables |
| Write gating | Blocks all mutations in production environments |
| Input validation | Validates identifiers and sys_ids to prevent injection |
| Script security | Constrains local script file reads for artifact writes |

---

## Table Access Control

The following security-sensitive tables are permanently blocked. Any attempt to use them via `query`, `describe`, or `record_write` will raise a `PolicyError`.

- `sys_user_has_password`
- `oauth_credential`
- `oauth_entity`
- `sys_certificate`
- `sys_ssh_key`
- `sys_credentials`
- `discovery_credentials`
- `sys_user_token`

---

## Sensitive Field Masking

The `mask_record` (used by `query`) and `mask_sensitive_fields` (used by `record_write`) functions automatically replace sensitive values with `***MASKED***`.

### Masked Patterns
Any field name matching these regex patterns is masked:
- `password`, `token`, `secret`, `credential`, `api_key`, `private_key`.

---

## Query Safety

Query safety prevents performance degradation on the ServiceNow instance.

### Row Limits
- All queries are capped at `MAX_ROW_LIMIT` (default 100, max 10000).
- If no limit is provided, the default is applied automatically.

### Large Table Protection
The following tables require a date-bounded filter (e.g., `sys_created_on>=javascript:gs.daysAgo(1)`):
- `syslog`, `sys_audit`, `sys_log_transaction`, `sys_email_log`.

Failure to provide a date filter on these tables results in a `QuerySafetyError`.

---

## Write Gating

Mutations are controlled by the `write_gate` function.

### Production Blocking
All write operations are blocked when `SERVICENOW_ENV` is set to `"prod"` or `"production"`. This affects:
- `record_write` and `record_apply`
- `attachment_write` (upload and delete)
- `service_catalog` (order and cart mutations)

### Preview Pattern
The system uses a mandatory preview/apply flow mediated by the `PreviewTokenStore`. You must first call `record_write` with `preview=true` (the default) to stage the change and receive a `preview_token`. The change is only committed when this token is passed to `record_apply`.

### Payload Size Cap
The `data` parameter on `record_write` (used for both `create.data` and `update.changes`) is capped at `MAX_PAYLOAD_BYTES = 1 MiB`. The check runs in `_validate_action_args` before payload parsing, before preview-token creation - oversized payloads are rejected immediately. As defence in depth, `parse_payload_json` enforces a stricter 256 KiB inner cap downstream; the 1 MiB outer cap is the documented entry-point contract.

---

## Script Security

When writing platform artifacts (e.g., Business Rules, Widgets, UI Pages, ACLs) using the `script_path` parameter in `record_write`:

- **Root Validation First:** `script_allowed_root` is resolved and validated as an existing directory before any user-controlled path is touched.
- **Opaque Errors:** All user-path rejections - file does not exist, not a regular file, outside root, symlink escape - return a single opaque message: `"script_path is not readable or is outside the allowed root"`. This closes the differential-error filesystem-enumeration channel.
- **Verbose Configuration Errors:** Operator-facing errors (`script_allowed_root` unset or not a directory) and the >1 MiB file-size error remain verbose because the user does not control them.
- **Limits:** Files are capped at 1 MB and must be UTF-8 encoded.
- **Mapping:** Content is automatically routed to the first script-bearing field returned by `DictionaryRegistry.get_script_fields(table)` (resolved at runtime from `sys_dictionary`). Tables with multiple script fields (e.g. `sys_ui_policy.script_true`/`script_false`, `sp_widget.client_script`/`template`/`css`, `sys_ui_page.html`/`processing_script`) accept an optional `script_field` parameter to override the default target.
- **XML Validation:** When the resolved field has `internal_type == 'xml'` (e.g. `sys_ui_macro.xml`), the file contents are parsed with `xml.etree.ElementTree.fromstring` before any platform call; malformed XML is rejected with a structured error.

---

## Decompression Caps

The Flow Designer `decode_values` action decompresses gzip+base64+JSON blobs. To prevent zip-bomb and resource-exhaustion attacks:

- **Wire cap:** `MAX_COMPRESSED_BYTES = 1 MiB` - checked before allocating the decompressor.
- **Output cap:** `MAX_DECOMPRESSED_BYTES = 4 MiB` - enforced during streaming decompression.
- Truncated streams (incomplete gzip) and trailing garbage after the gzip footer are rejected.
- All rejections raise `ValueError`, which `safe_tool_call` surfaces as a verbose error (see Error Handling below).

---

## Error Handling

The `@tool_handler` decorator ensures that exceptions are caught and returned as JSON error envelopes. The handler distinguishes verbose and opaque arms:

- **Verbose arms:** `ACLError`, `ForbiddenError`, `ServiceNowMCPError`, and `ValueError` preserve their curated, caller-actionable messages in the error envelope. `ValueError` is the rejected-user-input signal raised by validators like `validate_identifier` and the decompression caps above.
- **Opaque arm:** All other exceptions return `"Internal error (correlation_id=<uuid>)"`. Full exception detail is logged locally via `logger.exception` and captured to Sentry. The `correlation_id` in the envelope lets operators pivot to the detailed record.

For more details on the implementation of these policies, see [[Architecture]].
