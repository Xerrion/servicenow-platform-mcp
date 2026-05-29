# Safety and Policy

## Threat Model

The policy layer defends against four classes of accident: writes to dangerous tables that store credentials or key material, leakage of secret-shaped field values through read responses, runaway queries against large platform tables, and untrusted script paths escaping their allowed root. It does NOT defend against a malicious ServiceNow admin who has already configured their instance to expose sensitive data through normal APIs, nor against a malicious operator who holds `SERVICENOW_ENV=dev` credentials and deliberately points them at a production instance.

## Denied Tables

Eight tables are unconditionally blocked from all access - read or write:

| Table | Rationale |
|---|---|
| `sys_user_has_password` | Password hashes |
| `oauth_credential` | OAuth tokens |
| `oauth_entity` | OAuth configuration secrets |
| `sys_certificate` | TLS certificates and private keys |
| `sys_ssh_key` | SSH key material |
| `sys_credentials` | Stored credentials |
| `discovery_credentials` | Discovery/scan credentials |
| `sys_user_token` | API tokens |

`check_table_access(table)` raises `PolicyError` when `table.lower()` appears in this set.

## Sensitive Field Masking

Six case-insensitive regex patterns (matched via `re.search` against field names) identify sensitive fields:

```
password
token
secret
credential
api_key
private_key
```

Matched values are replaced with the constant `MASK_VALUE = "***MASKED***"`.

Two masking paths exist:

- `mask_record(table, record)` dispatches on table name. For most tables it calls `mask_sensitive_fields`, which recurses into nested dicts and lists replacing values whose keys match a sensitive pattern.
- For `sys_audit` rows, `mask_audit_entry(entry)` handles the indirect structure: the sensitive field name lives in the `fieldname`/`field` key, while actual data lives in `oldvalue`/`newvalue`/`old_value`/`new_value`. Standard field masking would miss these because the dict keys are generic.

## Query Safety

`enforce_query_safety(table, query, limit, settings)` applies three constraints in order:

1. Denied-table check via `check_table_access`.
2. Limit capping - the effective limit is floored at 1 and capped at `settings.max_row_limit` (default 100, configurable 1-10000).
3. Large-table guard - tables in `large_table_names` (default: `syslog`, `sys_audit`, `sys_log_transaction`, `sys_email_log`) must carry a date-bounded filter on a recognized date field. Without one, `QuerySafetyError` is raised.

Returns `{"limit": effective_limit}`.

`INTERNAL_QUERY_LIMIT = 1000` caps internal metadata fetches (schema lookups, choice loading) that are not user-facing.

## Identifier Validation

`validate_identifier(name)` enforces:

```
^[a-z0-9_]+(\.[a-z0-9_]+)*$
```

This permits dot-walked references like `change_request.number` while rejecting SQL injection fragments, encoded-query operators smuggled as names, path traversal, and uppercase characters. Every table and field name passes through this validator before reaching the HTTP client.

## Write Gating

Write control is function-based. There is no `WriteGatingError` exception class.

Four public functions:

- `write_blocked_reason(table, settings) -> str | None` - checks in order: (1) denied table, (2) `settings.is_production`. Returns a human-readable reason or `None`.
- `can_write(table, settings, override=False) -> bool` - `override=True` bypasses all checks; otherwise returns `write_blocked_reason(...) is None`.
- `write_gate(table, settings, correlation_id) -> str | None` - calls `write_blocked_reason`; returns a serialized error envelope if blocked, `None` if allowed.
- `production_write_blocked(settings, correlation_id) -> str | None` - environment-level gate for operations where the target table is unknown pre-fetch (e.g. attachment deletion must fetch metadata to learn the owning table).

The combined entry point `gate_write(table, settings, correlation_id)` chains `validate_identifier` then `check_table_access` then `write_gate` and never raises - it returns an error envelope string on any failure.

**In production (`servicenow_env` equals `prod` or `production`), ALL writes are blocked regardless of table.** The `write_blocked_reason` function returns `"Write operations are blocked in production environments"` whenever `settings.is_production` is true, and this check fires after the denied-table check but applies universally.

## Script-Path Hardening

When `record_write` accepts a local file via `script_path`:

1. `script_allowed_root` is resolved and confirmed as a directory before any user path is touched.
2. The user path is resolved and must satisfy four conditions: it exists, it is a regular file, it resides within the allowed root, and it does not escape via symlinks.
3. All four user-path rejections collapse to one opaque error: `"script_path is not readable or is outside the allowed root"`. This prevents path-existence probing.
4. Configuration errors (root unset, root not accessible, root not a directory) and the size error remain verbose.
5. Maximum file size: `MAX_SCRIPT_FILE_BYTES = 1_048_576` (1 MB).
6. The outer payload cap `MAX_PAYLOAD_BYTES = 1 MiB` is enforced on the `data` parameter in `_validate_action_args` before any token creation; `parse_payload_json` enforces a tighter 256 KiB cap downstream as defence in depth.
7. When the resolved target field has `internal_type == "xml"`, content is validated as well-formed XML via `xml.etree.ElementTree.fromstring` before the platform call. Malformed content yields a structured error.

## Script-Field Discovery

`DictionaryRegistry` resolves which fields on a table carry executable script or markup content. The algorithm:

1. Walks `sys_db_object.super_class` child-first, bounded at depth 8, with a cycle guard.
2. Fetches active `sys_dictionary` rows for each table in the chain.
3. Admits a field when its `internal_type` is in `UNAMBIGUOUS_SCRIPT_TYPES`:

```
script, script_plain, script_server, script_client,
email_script, html_script, html_template, css
```

4. For ambiguous types (`html`, `xml`), admits the field only when `_parse_attributes` yields an exact token-boundary match for `tinymce_allow_all=true` or `html_sanitize=false`. The parser splits on commas, partitions on the first `=`, and lowercases both sides. Substring matches like `my_tinymce_allow_all=true` do not admit.

5. Drops anything in `EXCLUDED_ELEMENTS`:

```
translated_html, template_value, glide_var, json, conditions,
condition_string, glide_action_list, variable_conditions,
snapshot_template_value, variable_template_value
```

Exclusion takes precedence over type check.

## What the Policy Layer Does NOT Do

- It does not emulate ServiceNow's ACL engine. If the platform returns a field to the configured user, the server shows it (unless the field name matches a sensitive pattern).
- It does not perform row-level filtering beyond denied tables. The platform's own ACLs remain the final authority on record visibility.
- It does not inspect field values for secrets - masking operates on field names only (except the `sys_audit` value-masking path where the field name is stored indirectly).
- It does not rate-limit requests. A caller can issue many queries in rapid succession.
- It does not validate that the ServiceNow instance URL actually points to the environment declared in `SERVICENOW_ENV`. An operator who sets `dev` while targeting a production instance bypasses the production write gate.

---

See also: [[Architecture]] for where policy checks fire in the request flow, [[Tool-Reference]] for per-tool write-gate behavior.
