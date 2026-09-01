# ServiceNow Platform MCP installation guide

This guide is for operators who install the ServiceNow Platform MCP server and
connect it to an MCP client. It covers the shortest safe path to a working
stdio server, then explains package selection, permissions, and troubleshooting.

For the complete product reference, see the [root README](README.md). For
encoded-query examples, see [Agent Recipes](docs/agent-recipes.md).

## 1. Choose the installation mode

### Local source checkout

The repository supports Python 3.12, 3.13, and 3.14. Install it with `uv`:

```bash
git clone https://github.com/Xerrion/servicenow-platform-mcp.git
cd servicenow-platform-mcp
uv sync --group dev
uv run servicenow-platform-mcp
```

The server uses stdio. An MCP client must launch the process. Do not configure
it as an HTTP endpoint.

The command reads `.env` and `.env.local` from its current working directory.
Use the cloned repository as the working directory, or place the intended
dotenv files in the working directory used by the client. A client that starts
the process elsewhere does not read dotenv files from the repository.

### Published package

If the package is available from the package index used by your environment,
your MCP client may launch its published console entry point with its package
runner. Verify the package name and release before using a command such as
`uvx`; this repository does not make `uvx` availability a requirement. A
published-package launch also changes the working-directory and dotenv-file
considerations described above.

## 2. Configure authentication

`SERVICENOW_INSTANCE_URL` is required. It must start with lowercase
`https://`, for example `https://your-instance.service-now.com`. Trailing `/`
characters are removed at startup.

Use exactly one authentication mode:

- **API key:** Set `SERVICENOW_API_KEY`. The server sends the exact
  `x-sn-apikey` header. A usable API key takes precedence over Basic Auth.
- **Basic Auth:** If the API key is empty, set both `SERVICENOW_USERNAME` and
  `SERVICENOW_PASSWORD`. The server sends HTTP Basic Auth.

When an API key is usable, username and password are not required and the
server does not send an `Authorization` header. Do not configure both modes as
if they were combined. Keep credentials in the MCP client's environment,
environment-variable forwarding, or a secret store. Do not put real secrets
in source-controlled workspace configuration, `.env` files committed to the
repository, or logs.

Settings load at startup. Restart the full MCP server process after any
environment or dotenv change.

## 3. Select a tool package

The server has 13 tool groups. It always exposes `list_tool_packages`, so the
public-tool counts include that tool.

| Package | Public tools | Use |
| --- | ---: | --- |
| `full` | 15 | All groups, including writes. |
| `readonly` | 11 | Read, investigation, analysis, audit, Flow, and Code Search tools. |
| `core_readonly` | 4 | `query`, `describe`, and read-only `attachment`. |
| `none` | 1 | Only `list_tool_packages`. |

`analysis` is included in `full` and `readonly`, but not in
`core_readonly`. The `attachment` group is read-only. `attachment_write` is a
separate explicit opt-in group and is included in `full` only among the preset
packages. A custom package can add it, but upload and delete remain subject to
write gating and ServiceNow authorization.

Custom packages use comma-separated group names:

```bash
MCP_TOOL_PACKAGE=query,describe,record_read,attachment
```

Valid groups are:

`query`, `describe`, `record_write`, `record_read`, `attachment`,
`attachment_write`, `investigate`, `resolve_choice`, `service_catalog`,
`analysis`, `audit`, `flow`, and `code_search`.

For a normal read workflow, use `readonly` or a smaller custom package. The
package controls which tools are loaded. It is not an authorization boundary.

## 4. Configure the server

The following examples use placeholders. `${...}` expansion depends on the
MCP client. Prefer the client's documented environment forwarding or a secret
store. Do not replace placeholders with secrets in a committed file.

The API-key and Basic Auth examples below run from a local source checkout.
They require `uv sync` first and set `cwd` to that checkout.

### API key

```json
{
  "command": "uv",
  "args": ["run", "servicenow-platform-mcp"],
  "cwd": "/path/to/servicenow-platform-mcp",
  "env": {
    "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
    "SERVICENOW_API_KEY": "${SERVICENOW_API_KEY}",
    "MCP_TOOL_PACKAGE": "readonly",
    "SERVICENOW_ENV": "prod"
  }
}
```

### Basic Auth

```json
{
  "command": "uv",
  "args": ["run", "servicenow-platform-mcp"],
  "cwd": "/path/to/servicenow-platform-mcp",
  "env": {
    "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
    "SERVICENOW_USERNAME": "${SERVICENOW_USERNAME}",
    "SERVICENOW_PASSWORD": "${SERVICENOW_PASSWORD}",
    "MCP_TOOL_PACKAGE": "readonly",
    "SERVICENOW_ENV": "prod"
  }
}
```

`cwd` is a common client setting, not an MCP protocol field. Use the equivalent
working-directory field for your client. If the client does not support it,
use an absolute command or arrange for the process environment and dotenv files
to be available from its launch directory.

### Managed published package

After you confirm that the required release exists on the Python package index
configured for your environment, `uvx` can resolve the distribution and run its
console entry point without a source checkout:

```json
{
  "command": "uvx",
  "args": ["servicenow-platform-mcp"],
  "env": {
    "SERVICENOW_INSTANCE_URL": "https://your-instance.service-now.com",
    "SERVICENOW_API_KEY": "${SERVICENOW_API_KEY}",
    "MCP_TOOL_PACKAGE": "readonly",
    "SERVICENOW_ENV": "prod"
  }
}
```

Do not add the source-checkout `cwd` only to make this managed launch work.
Forward the required environment through the client or its secret store. If
you intentionally use dotenv files instead, they are still resolved from the
managed process's working directory, not from a repository that `uvx` manages.

## 5. Configuration reference

All settings are environment variables. The server reads `.env`, then
`.env.local`; later dotenv values override earlier values, and process
environment variables override both.

| Variable | Required | Default | Range or values | Purpose |
| --- | --- | --- | --- | --- |
| `SERVICENOW_INSTANCE_URL` | Yes | None | Must start with lowercase `https://` | ServiceNow instance base URL. Trailing `/` characters are removed. |
| `SERVICENOW_API_KEY` | Conditional | Empty | Must contain a non-whitespace character when used | API-key authentication. Takes precedence over Basic Auth. |
| `SERVICENOW_USERNAME` | Conditional | Empty | Required when the API key is empty | Basic Auth username. |
| `SERVICENOW_PASSWORD` | Conditional | Empty | Required when the API key is empty | Basic Auth password. |
| `MCP_TOOL_PACKAGE` | No | `full` | Preset or comma-separated groups | Selects loaded tool groups. |
| `SERVICENOW_ENV` | No | `dev` | Any string | `prod` and `production` block writes. |
| `MAX_ROW_LIMIT` | No | `100` | `1`-`10000` | Cap for bounded generic and query-oriented paths that use it. Not a global response cap. |
| `LARGE_TABLE_NAMES_CSV` | No | `syslog,sys_audit,sys_log_transaction,sys_email_log` | Comma-separated names | Tables that require date-bounded queries. |
| `SCRIPT_ALLOWED_ROOT` | No | Empty | Filesystem path | Required root for `record_write` `script_path`; empty disables that input. |
| `HTTPX_TIMEOUT_SECONDS` | No | `30.0` | `1.0`-`600.0`, finite | ServiceNow HTTP timeout. |
| `METADATA_CACHE_TTL_SECONDS` | No | `300` | `1`-`86400` | Freshness window for choice, dictionary, and audit-configuration metadata. |
| `SENTRY_DSN` | No | Empty | Sentry DSN | Optional error reporting. |
| `SENTRY_ENVIRONMENT` | No | Empty | Any string | Sentry environment; empty uses `SERVICENOW_ENV`. |

Sentry is optional. `SERVICENOW_INSTANCE_URL` and usable authentication are
validated at startup even when the selected package has no operational tools.

## 6. Public tool inventory

The public tools are:

- `list_tool_packages` - reports preset packages and groups. Always available.
- `query` - reads records and Aggregate API results. List mode requires an
  explicit `fields` projection; encoded queries are passed to ServiceNow.
- `describe` - describes tables, fields, inherited metadata, and discovered
  script fields.
- `record_read` - reads one record by `sys_id` or `name`, with masked output and
  discovered `script_fields`.
- `record_write` and `record_apply` - stage and apply create, update, and
  delete operations. Preview mode is the default.
- `attachment` - lists, gets, downloads, or downloads attachments by name.
- `attachment_write` - uploads or deletes attachments.
- `investigate` - runs or explains registered investigations, or describes them
  (`run`, `explain`, `describe`).
- `resolve_choice` - maps choice labels to stored values.
- `service_catalog` - browses catalogs, categories, items, variables, and
  carts (`catalogs_list`, `catalog_get`, `categories_list`, `category_get`,
  `items_list`, `item_get`, `item_variables`, `cart_get`); ordering and
  state-changing cart actions (`order_now`, `add_to_cart`, `cart_submit`,
  `cart_checkout`) are write-gated.
- `analysis` - composes fulfilled RITM variables or reads journal history
  (`ritm_variables`, `journal_history`, `describe`).
- `audit` - checks audit configuration or reads date-bounded audit history
  (`check_field`, `check_fields`, `check_table`, `history`, `describe`).
- `flow` - inspects Flow Designer records and decodes V1/V2 flow data
  (`contract`, `inspect`, `find_by_table`, `decode_values`, `list_triggers`,
  `describe`).
- `code_search` - searches script-bearing artifacts or lists Code Search tables
  (`search`, `list_tables`, `describe`).

Use each tool's `describe` action where available. The runtime tool schema is
the authoritative input contract.

### Analysis actions

`analysis(action="ritm_variables", sys_id="<32-char-sys-id>")` composes
submitted answers for one `sc_req_item` through
`sc_item_option_mtom`, `sc_item_option`, and `item_option_new`. Password,
token, secret, credential, API-key, and private-key answers are masked. Missing
or inaccessible options and definitions are reported as degraded entries or
warnings. Reference and List Collector values retain raw identifiers. List
Collector display values are not fabricated. Multi-row variable set payloads
are not retrieved or decoded; the response only reports bounded presence
metadata. Completeness depends on row ACLs, field ACLs, and instance data.

`analysis(action="journal_history", table="incident",
sys_id="<32-char-sys-id>")` reads dictionary-confirmed `comments`,
`work_notes`, and `close_notes` entries from `sys_journal_field`. The default
window is 90 days and the default fields are `comments,work_notes`. Use
`since` or `window_days` to change the date window, and `limit` plus `offset`
for bounded pagination. The target table, its dictionary metadata, and the
journal table require suitable read access. Row ACLs, field ACLs, and retention
can make the result incomplete. This is journal history, not the field-change
history returned by `audit(action="history")`.

## 7. ServiceNow permissions

Authentication, API-resource policy, and table or field ACLs are separate
controls. A valid API key does not grant table access.

The baseline read-only API families are the Table API, Attachment API, and
Aggregate API (Stats). The `core_readonly` package uses Table and Attachment;
Stats is needed when a selected read tool uses aggregates. The `readonly`
package also needs the resources and read ACLs used by its selected tools:

- **Table API:** records, dictionary metadata, Flow inspection, analysis, and
  other table-backed reads.
- **Aggregate API:** `query` aggregate mode and audit positive-control counts.
- **Attachment API:** attachment metadata and downloads. Upload and delete
  require additional POST or DELETE permissions and `attachment_write`.
- **Code Search:** only when `code_search` is selected; allow its GET search
  and table-list resources.
- **Service Catalog:** only when `service_catalog` is selected; allow the GET
  catalog, category, item, variable, and cart resources needed by the chosen
  actions. State-changing order and cart actions require their POST resources.

Analysis additionally needs Table API access and applicable read ACLs for
`sc_req_item`, `sc_item_option_mtom`, `sc_item_option`, `item_option_new`,
`sc_multi_row_question_answer`, `sys_journal_field`, `sys_db_object`, and
`sys_dictionary`, as well as the target table and fields.

For a true read-only deployment, use all of these controls:

1. `MCP_TOOL_PACKAGE=readonly`, or a smaller custom package with read groups;
2. GET-only ServiceNow API resource policies;
3. read-only ServiceNow table and field ACLs; and
4. `SERVICENOW_ENV=prod` or `production` to block local writes.

Resource policies are instance-specific. They are not universal across all
ServiceNow deployments. Package selection alone is not authorization.

## 8. Safety and limits

The server blocks a set of highly sensitive tables and masks sensitive values
on specific record-oriented paths. Masking is not global. Aggregate values and
arbitrary content from Code Search, Flow, Service Catalog, and other surfaces
are not globally masked. Enforce ServiceNow field ACLs and do not aggregate or
group sensitive fields.

Other important limits include:

- `MAX_ROW_LIMIT` applies only to bounded paths that use it. It is not a global
  response or egress cap.
- `attachment(action="list")` returns at most 100 metadata records and has no
  caller-controlled offset or pagination.
- Attachment transfers are limited to 10 MiB.
- Large tables listed in `LARGE_TABLE_NAMES_CSV` require date-bounded queries.
- Writes are locally blocked in production, but ServiceNow remains the
  authorization authority. Keep write packages and credentials least
  privileged.
- Attachment content, submitted catalog values, and arbitrary platform content
  are untrusted data.

## 9. Verify and troubleshoot

1. Restart the full MCP server process.
2. Call `list_tool_packages`. It is always available and confirms the selected
   package.
3. For an operational read, call `describe` or a small `query` with an
   explicit field projection.

Use the following checks for common failures:

- **Startup says the instance URL is missing:** set
  `SERVICENOW_INSTANCE_URL` to a complete lowercase-HTTPS instance URL. Check
  the MCP client's environment and working directory.
- **401 or `User Not Authenticated`:** check the exact instance URL, the API
  key or both Basic Auth values, the instance authentication policy, and that
  API-key mode uses `x-sn-apikey`. Restart after changes.
- **API key is valid but a request is denied:** check the API-key REST-resource
  policy. This is distinct from table and field ACL denial.
- **A table or field is denied:** check its ServiceNow row and field ACLs. The
  selected MCP package only controls tool exposure.
- **Environment changes have no effect:** restart the full server process;
  settings are loaded at startup.
- **The MCP client reports `-32000`:** this may be a client-level wrapper.
  Inspect the client's stderr and the underlying server-process error before
  assigning the cause to authentication or ACLs.

The server returns correlation IDs in normal tool response envelopes. Include
the correlation ID and the underlying error when reporting a failure. If
Sentry is configured, it provides additional visibility for unexpected errors.
