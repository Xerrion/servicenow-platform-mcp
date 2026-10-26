# Safety and permissions

The server adds local limits to ServiceNow access. Your ServiceNow account and
its permissions still decide which records and fields you can use.

## ServiceNow permissions

Ask an administrator for the roles, API access, and record permissions needed
for your work. A tool can be listed even when your account cannot use it on a
particular record. A 401 or 403 requires investigation on the connection and
ServiceNow side.

## Before making a change

1. Confirm the instance address and the record you intend to change.
2. Ask for a preview and review the proposed values.
3. Approve the change in your own workflow, then apply the preview token.
4. Check the result in ServiceNow when the outcome is uncertain.

The server does not record or enforce human approval. A preview token alone is
not proof of approval. Tokens expire after 300 seconds and can be used once. A
token is consumed even if applying it fails. Start with a fresh preview after
any failure, but first check the target if the result says `UNKNOWN_OUTCOME`.
Do not repeat a write automatically when its result is unknown.

Before applying a preview, the server checks its policy, authorization, and
detectable changes to the target. These checks cannot prevent every concurrent
change. For scripts, review the complete replacement. The server does not
validate JavaScript syntax.

## Write protection

The default `SERVICENOW_ENV=prod` blocks writes through this server. The values
`dev` and `test` permit writes only when the relevant tool and ServiceNow
permissions also allow them. The environment setting does not detect which
instance the URL names. Check the URL separately.

Read-only tools can still access data when writes are blocked. Select only the
tool groups needed for your work. See [Tool Packages](Tool-Packages.md).

## Queries and sensitive data

Queries return at most `MAX_ROW_LIMIT` records. The default is 100. Some large
tables require a date filter. Add a time period when asking for logs or audit
history. Your administrator can change the configured limit and table list.

The server blocks access to certain credential and token tables. It masks values
for some sensitive field names in supported record responses. This is not a
complete secret filter. Sensitive information may still appear in free text,
scripts, attachments, or fields with other names. Check information before
sharing it.

Keep tokens, passwords, and personal data out of examples, logs, and support
requests. See [Telemetry](Telemetry.md) for diagnostics.
