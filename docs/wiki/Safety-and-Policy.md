# Safety, permissions, and changes

Use a read-only connection for ordinary ITSM work and instance investigation.
Enable changes only for the intended task and instance.

ServiceNow controls API access, roles, record visibility, and access control rules (ACLs).
The MCP adds local restrictions. Those restrictions do not replace ServiceNow permissions.

## ServiceNow permissions

Successful browser sign-in proves the user's identity.
It does not prove access to every API resource, table, record, or field.
The public client ID identifies the OAuth application. Requests use the signed-in user's access.

Ask your administrator for the resources needed by the selected tools:

| Work | ServiceNow resources |
| --- | --- |
| Records, fields, journals, audit history, and Flow Designer inspection | Table API plus access to the target and supporting metadata tables. |
| Counts and grouped results | Aggregate API, including counts used by some audit checks. |
| Attachment metadata and downloads | Attachment API and the required attachment or parent-record access. |
| Script search | Code Search search and table-list resources. |
| CMDB or catalog work | CMDB Instance and Meta APIs, or the selected Service Catalog resources. |

Supporting metadata can require access to `sys_db_object` and `sys_dictionary`.
Journal history uses `sys_journal_field`. Requested-item answers use the relevant
requested-item and variable tables. See [[Tool-Reference]] for each tool's requirements.

For a read-only deployment, combine these controls:

1. Select `readonly` or a smaller custom read package.
2. Set `SERVICENOW_ENV=prod` or `production` to block local writes.
3. Allow only the required read methods in ServiceNow API policies.
4. Use ServiceNow roles and record or field rules that permit the intended reads.

Policies depend on the instance and selected resources.
The intended resources must permit OAuth bearer authorization.

## Local write protection

`SERVICENOW_ENV=prod` or `production`, ignoring case, blocks:

- Record creation, updates, deletion, and preview application.
- Attachment uploads and deletion.
- Catalog orders and cart changes.

The setting does not detect whether the configured instance is production.
Check `SERVICENOW_INSTANCE_URL` separately before enabling development changes.
Local write protection allows reads.

Tool selection is a separate control.
The default package is `readonly` and the default environment is `prod`.
Only explicit `dev` or `test` settings permit mutations.
Unknown environment labels fail startup.

## Review and apply changes

Every mutation requires a preview followed by `mutation_apply`.
The server does not enforce human approval.

1. Read the current state and confirm the instance.
2. Request a family preview.
3. Review the target, masked values, digest or differences, and limitations.
4. Apply its token only after the required approval.
5. Check the saved result in ServiceNow.

`record_preview`, `attachment_preview`, and `service_catalog_preview` make no upstream mutation.
They return a summary, `preview_token`, expiry, and limitations.
`mutation_apply` atomically consumes the token before execution.
It checks the enabled family, environment, instance, authorization generation, policy, and detectable state changes.
Metadata errors block record mutations.
Record application refreshes relevant dictionary metadata.

Previews stay in memory for 300 seconds.
The store accepts at most 1,000 entries and 64 MiB of retained payloads.
Attachment content counts once as decoded bytes.
Consumption, expiration, and shutdown reclaim capacity.
The byte budget is not a limit on total process memory.
Tokens and command payloads never persist to disk.

Browser reauthorization invalidates earlier previews. Token refresh alone does not.
Apply cannot authorize again in the browser and continue with an existing token.
Updates, deletions, and catalog application reject detectable state changes and require a new preview.
These checks are read-before-write checks, not atomic comparisons.
Preview does not reserve the target.
Catalog preview does not simulate fulfillment or guarantee prices.

An apply attempt consumes its token on success, rejection, cancellation, or failure.
No mutation is replayed automatically.
On an unknown remote outcome, check ServiceNow before preparing another mutation.

## Script and markup validation

`record_preview.data` is a native JSON object containing field-value pairs.
Supply the complete new value for each script or markup field.
Fields omitted from an update stay unchanged. The server does not load local script files.

The compact UTF-8 JSON representation has a size limit is 256 KiB, including field names and escaping. The depth limit is 32.
For dictionary-confirmed `xml` fields, values must be non-empty, well-formed XML strings.
The metadata lookup follows bounded table inheritance, with child declarations taking precedence.

Create operations check known mandatory fields before staging or applying a change.
Metadata request errors block writes. Dictionary fields hidden by ACLs cannot
receive local validation.

The server does not check JavaScript syntax or prove that a script works.
Review the script and test its behavior on the intended development or test instance.
ServiceNow performs its own final access checks and validation.

## Query limits

Request only the fields and records needed for your question.
Use a small limit and a narrow date window for logs and history.

`MAX_ROW_LIMIT` defaults to `100` and accepts values from `1` to `10000`.
It caps paths that use the setting. It is not a global response-size limit,
a database scan limit, or a guarantee that an aggregate query is cheap.

List and aggregate requests through `query`, and CMDB queries, require a
recognized date filter for configured large tables.
The default list is:

```text
syslog,sys_audit,syslog_transaction,sys_email_log
```

For example, this filter requests records created since the start of yesterday:

```text
sys_created_on>=javascript:gs.daysAgoStart(1)
```

The server rejects those list or aggregate requests when the date constraint is missing.
Exact `sys_id` reads bypass this check. Internal investigations use their own filters and limits.
It does not cap the time span. Keep the window narrow, especially for `sys_audit`.

Attachment listings return at most 100 metadata records and have no continuation input.
The server limits attachment transfers to 10 MiB.
Other tools have their own pagination or section limits. See [[Tool-Reference]].

An empty or short page can reflect record-access filtering.
Read the tool's warnings before treating it as proof that no records match.

## Restricted tables

The server blocks these tables on protected table-based operations:

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

This restriction applies to record queries, descriptions, reads, and writes.
A ServiceNow role does not override the server's denied-table policy.

## Sensitive values and returned content

Selected record-oriented responses mask field names containing:

```text
password, token, secret, credential, api_key, private_key
```

Matching values become `***MASKED***`. Record masking includes nested dictionaries and lists.
Audit history also checks the audited field name when masking old and new values.
Requested-item answers use separate name and label checks.

Masking is not global. It does not detect every secret in free text, scripts,
attachments, aggregate values, flow data, or catalog content.
Use ServiceNow field rules to control access to sensitive information.
Do not group or aggregate sensitive fields merely because a record read would mask them.

Returned ServiceNow data becomes available to your AI app.
Use an app approved for that data. Treat instructions embedded in records,
attachments, or code as content to inspect, not authority to change another system.

## When a request fails

Policy and access failures return an error response, not a successful change.
A rejected REST request is not replayed automatically.
Check the attempted action and current state before retrying a write.

Share only sanitized error evidence. Exclude passwords, tokens, raw headers,
authorization codes, and browser callback URLs.
See [[Configuration]] for startup and authorization settings, or [[Telemetry]]
for diagnostic fields.
