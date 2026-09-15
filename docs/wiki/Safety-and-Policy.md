# Safety and Policy

Server-side policy limits accidental exposure, unbounded reads, and local
mutations. ServiceNow remains the authority for REST policies, roles, ACLs, and
row visibility.

## Policy layers

| Layer | Effect |
| --- | --- |
| Denied tables | Blocks access to security-sensitive tables. |
| Sensitive-field masking | Masks selected field names in record-oriented responses. |
| Query safety | Applies limits and date requirements to protected large tables. |
| Write gating | Blocks local mutations in production environments. |
| Input validation | Validates identifiers, sys_ids, payload size, and dictionary-confirmed XML fields. |

## Denied tables

These tables are blocked on table-based operations, including `query`,
`describe`, `record_read`, and `record_write`:

```text
sys_user_has_password
oauth_credential
oauth_entity
sys_certificate
sys_ssh_key
sys_credentials
discovery_credentials
sys_user_token
```

## Sensitive values

Record-oriented masking matches field names containing:

```text
password, token, secret, credential, api_key, private_key
```

Matching values become `***MASKED***`, including values nested in dictionaries
and lists. Masking is not global. Aggregate values and arbitrary content from
Code Search, Flow, Service Catalog, and other surfaces still require suitable
ServiceNow ACLs.

## Query safety

`MAX_ROW_LIMIT` defaults to `100` and accepts `1`-`10000`. It caps paths that
use the setting. It is not a global response or egress cap.

Tables in `LARGE_TABLE_NAMES_CSV` require a structural date constraint. The
default tables are:

```text
syslog, sys_audit, sys_log_transaction, sys_email_log
```

Example encoded query:

```text
sys_created_on>=2026-01-01
```

Missing date constraints return a query-safety error. Keep date windows narrow,
especially for `sys_audit`.

## Write gating

Set `SERVICENOW_ENV=prod` or `SERVICENOW_ENV=production` to block:

- `record_write` and `record_apply`.
- `attachment_write` upload and delete.
- `service_catalog` order and cart mutations.

Reads remain available. This local gate does not replace ServiceNow
authorization.

Record mutations use a preview/apply flow by default:

1. Call `record_write` with `preview=true`.
2. Inspect the returned single-use `preview_token`.
3. Call `record_apply` with that token.

Preview tokens stay in memory, expire after five minutes, and are consumed when
applied. Set `preview=false` only when an immediate write is intentional.

## Inline write validation

`record_write.data` is a JSON string containing complete field values. The
server does not read local script files. The UTF-8 JSON input limit is 256 KiB,
including field names and escaping.

For supplied dictionary fields with `internal_type == 'xml'`, values must be
non-empty, well-formed XML strings. Dictionary metadata follows the bounded
`sys_db_object.super_class` chain, with child declarations taking precedence.
Metadata request errors block writes. Script syntax is not checked.

ServiceNow still performs final authorization and validation.

## Error responses

Operational tools return a JSON error envelope for policy, query-safety, and
ACL failures. Expected policy failures do not become successful writes. See
[[Tool-Reference]] for tool-specific limits and [[Configuration]] for
environment settings.
