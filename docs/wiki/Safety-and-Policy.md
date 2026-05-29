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

---

## Script Security

When writing platform artifacts (e.g., Business Rules, Widgets, UI Pages, ACLs) using the `script_path` parameter in `record_write`:

- **Root Constraint:** Paths must resolve within the directory defined by `SCRIPT_ALLOWED_ROOT`.
- **Resolution:** Paths are resolved strictly to prevent symlink or traversal attacks.
- **Limits:** Files are capped at 1 MB and must be UTF-8 encoded.
- **Mapping:** Content is automatically routed to the first script-bearing field returned by `DictionaryRegistry.get_script_fields(table)` (resolved at runtime from `sys_dictionary`). Tables with multiple script fields (e.g. `sys_ui_policy.script_true`/`script_false`, `sp_widget.client_script`/`template`/`css`, `sys_ui_page.html`/`processing_script`) accept an optional `script_field` parameter to override the default target.
- **XML Validation:** When the resolved field has `internal_type == 'xml'` (e.g. `sys_ui_macro.xml`), the file contents are parsed with `xml.etree.ElementTree.fromstring` before any platform call; malformed XML is rejected with a structured error.

---

## Error Handling

All safety violations result in a JSON error envelope. The `@tool_handler` decorator ensures that `PolicyError`, `QuerySafetyError`, and `ForbiddenError` (ACL denials) are caught and returned gracefully with a descriptive message.

For more details on the implementation of these policies, see [[Architecture]].
