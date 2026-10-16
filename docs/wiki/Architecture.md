# How the MCP server works

The local Python application receives MCP requests over stdio and calls ServiceNow REST APIs.
ServiceNow applies the signed-in user's roles, API policies, and record and field ACLs.
The MCP application also applies local access policy, bounds, and field masking.

```text
AI app
  -> MCP adapters: native schemas, safe errors, diagnostics
  -> Capabilities: operation validation, orchestration, typed results
  -> Platform: REST API families, dictionary, choices, metadata caches
  -> ServiceNow REST APIs
```

## Module ownership

Paths are relative to `src/servicenow_mcp/`.

| Component                                            | Responsibility                                                                                |
| ---------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `server.py`                                          | Installed stdio command and safe startup errors.                                              |
| `bootstrap/stdio.py`                                 | Explicit dependency wiring and application resource ownership.                                |
| `packages.py`                                        | Data-only presets and custom group selection.                                                 |
| `mcp/inputs.py`                                      | Raw schema and constraint checks before SDK argument coercion.                                |
| `mcp/tools/`                                         | Thin adapters with enumerated actions, native inputs, descriptions, and annotations.          |
| `mcp/handlers.py`, `mcp/errors.py`, `mcp/outputs.py` | Redacted diagnostics and native MCP error translation.                                        |
| `capabilities/`                                      | Records, metadata, attachments, catalog, audit, investigations, flows, CMDB, and code search. |
| `capabilities/mutations/`                            | Typed commands, bounded staging, and single-use application.                                  |
| `platform/client.py`, `platform/_client_*.py`        | Shared REST facade and existing API-family clients.                                           |
| `platform/metadata/`                                 | Dictionary inheritance, field classification, choices, audit metadata, and cache ownership.   |
| `policy/`                                            | Denied tables, query bounds, field masking, and mutation protection.                          |
| `auth.py`, `oauth_callback.py`                       | Local public-client PKCE authorization and memory-only tokens.                                |
| `telemetry.py`, `_http_diagnostics.py`, `sentry.py`  | Request timing, safe evidence, and optional error reporting.                                  |

Capabilities, platform metadata, and policy do not import MCP adapters or bootstrap.
Import-boundary tests enforce that direction.
Bootstrap registers known groups explicitly. A missing component fails startup.
`list_tool_packages` remains available with every package, including `none`.

## Native contracts

Object inputs use JSON objects. Field and section selections use arrays, with `"*"` where supported.
Actions use enumerations. Encoded queries, identifiers, scripts, and compressed Flow values remain strings.
Raw validation rejects unknown arguments and mismatched shapes before upstream requests.
Constraint failures return safe messages without the rejected values.

Capabilities return Pydantic results or raise curated application errors.
Dynamic ServiceNow fields remain JSON-value mappings.
Each tool's output schema accepts native success and error envelopes.
All `structuredContent` validates against that schema and contains `status` and `data`.
Optional pagination, warnings, and truncation describe incomplete results.
Failures set MCP `isError=true` and include a stable error code and safe message.
Text and structured errors contain the same information.

## Construction and cleanup

Bootstrap creates one OAuth provider, shared HTTP pool, client factory, choice registry, dictionary registry, and mutation coordinator.
Constructing the application does not authorize or contact ServiceNow.
The first outbound operation obtains authorization.
Directly constructed clients own their transport. Factory clients share the application pool.

Shutdown closes the shared pool and clears staged mutations.
The installed command also shuts down Sentry.
The temporary loopback HTTP listener receives OAuth callbacks only. It does not receive MCP requests.

## Authorization and mutations

OAuth uses public authorization-code flow with PKCE S256 and no client secret.
Access and refresh tokens stay in memory. Concurrent calls share the provider's lock.
Token refresh retains the authorization generation. Browser authorization creates a new generation.

Every mutation requires a family preview and `mutation_apply`.
Preview reads metadata and current state, then stores an owned copy of the typed command.
Apply atomically consumes the token and checks the enabled family, environment, instance, authorization generation, policy, and detectable state changes.
Header acquisition also checks the authorization generation, including after precondition reads.
Apply cannot authorize in the browser and continue with an existing preview.

Record application refreshes relevant dictionary metadata and checks mandatory and XML fields.
Attachment deletion checks actual ownership and metadata.
Catalog application checks the observed item or cart state.
State checks and the subsequent REST mutation are not atomic.
Catalog previews do not simulate fulfillment or guarantee prices.
A preview token does not prove human approval.

Tokens expire after 300 seconds and are consumed on success or failure.
The store permits 1,000 entries and 64 MiB of retained payloads.
Attachment bytes count once. Consumption, expiration, and shutdown release that capacity.
The byte limit bounds retained payloads, not total Python process memory.
No token or mutation payload is persisted.
Remote failures never trigger automatic mutation replay.

## Metadata and evidence

Metadata caches use configurable TTL, bounded LRU storage, and single-flight loading.
They do not cache target records, query results, flows, attachment content, or live audit counts.
Dictionary inheritance is child-first.

Query schema discovery keeps observed inheritance completion and metadata-read limitations.
It includes inactive definitions separately from active field and script discovery.
Reference paths use bounded, batched lookups of each static target table.
Cached definitions provide positive schema evidence, not proof of access permission.
Missing metadata remains unverified instead of becoming a nonexistent-field claim.
Query discovery caches and ancestry invalidate when a table's metadata is flushed.

Flow loading produces an `InspectionSnapshot` containing rows, probe limits, section selections, and dependency completeness.
`assemble_inspection` projects that evidence without platform I/O.
Submitted-answer loading similarly feeds `SubmittedVariablesSnapshot` to a pure assembler.
Missing definitions, bounded datasets, and incomplete dependencies retain their warnings.
An empty result does not establish absence.

See [[Safety-and-Policy]] for operational limits and [[Development]] for contributor checks.
