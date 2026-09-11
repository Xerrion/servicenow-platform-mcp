# Changelog

## Unreleased

### Breaking changes

- Removed the unused Python compatibility method `ServiceNowClient.download_attachment_by_name`.
  Python callers must resolve attachment metadata, then use `download_attachment(sys_id)`.
  The MCP `attachment(action="download_by_name", ...)` path is unchanged.
- Internal tool registration functions now declare only consumed dependencies. The loader
  injects them by parameter name; direct callers must omit removed parity-only arguments.
- Response envelopes no longer include the server-internal `correlation_id`.
  Tool handling no longer generates or injects it. Sentry tool context and
  exception capture remain enabled. ServiceNow record fields named
  `correlation_id` and outbound HTTP tracing headers are unchanged.
- Removed redundant `selection.omitted` metadata. Other selection and
  continuation metadata are unchanged.
- Optional `query` string inputs now default to null, not empty strings.
  Omit unused arguments; `fields` is still required in list mode. Empty or
  null filters are not sent to ServiceNow. Zero offsets, false display-value
  flags, and valid limits are preserved. Refresh cached MCP tool schemas.

- Outbound ServiceNow calls now use only public OAuth authorization-code PKCE S256. Remove
  `SERVICENOW_API_KEY`, `SERVICENOW_USERNAME`, and
  `SERVICENOW_PASSWORD`; non-empty legacy settings are rejected. Configure
  `SERVICENOW_OAUTH_CLIENT_ID` and an exact registered loopback redirect URI.
  Set `SERVICENOW_OAUTH_SCOPE=useraccount`, Public Client=true, and PKCE S256 on
  the application. The browser and stdio process must run on the same machine.
  Authorization sends a random `state` and S256 challenge. The callback validates
  state; code exchange sends the verifier and omits state. Only access tokens
  are kept, in memory. Restart, expiry, or REST rejection requires browser
  authorization on the next call. REST calls use Bearer headers, never token URLs,
  and are never replayed.
- Python callers must replace `BasicAuthProvider` with `OAuthPKCEProvider`.
  `create_auth()` remains the factory; `get_headers()` can now open the local
  browser and raise `AuthError`. See README authentication setup.
- Removed `record_write.script_path` and `record_write.script_field`. Supply all
  field values through the JSON string `data`, including complete script or
  markup strings under their field names. For example, replace file input with
  `data='{"script":"run();\\n"}'`. Multiple fields can be written together;
  omitted fields stay unchanged on update. Refresh cached MCP tool schemas.
- Removed `script_allowed_root` / `SCRIPT_ALLOWED_ROOT`. Remove this setting
  from client and environment configuration. The server no longer loads local
  script files. If a client reads a file, it must send the content in `data`.
- The existing inline limit remains **256 KiB of UTF-8 JSON**, including keys
  and escaping. The former 1 MiB file allowance is gone; do not send partial
  script chunks to work around the limit because field writes replace values.
- XML well-formedness validation now applies to inline dictionary-confirmed XML
  fields, including inherited fields. Empty, null, non-string, and malformed
  XML are rejected before preview creation or mutation. Writes query types only
  for supplied fields and fail on metadata request errors. Dictionary visibility
  still depends on ServiceNow ACLs. Script discovery through `record_read` and
  `describe`, preview/apply safety, and masking remain unchanged.

## [2.0.0](https://github.com/Xerrion/servicenow-platform-mcp/compare/v1.0.0...v2.0.0) (2026-09-11)


### ⚠ BREAKING CHANGES

* replace ServiceNow legacy auth with OAuth PKCE ([#162](https://github.com/Xerrion/servicenow-platform-mcp/issues/162))

### Features

* replace ServiceNow legacy auth with OAuth PKCE ([#162](https://github.com/Xerrion/servicenow-platform-mcp/issues/162)) ([619e583](https://github.com/Xerrion/servicenow-platform-mcp/commit/619e58351e66ede8abac7e4c7fbde895807c27ed))


### Bug Fixes

* translate query ordering into encoded clauses ([#164](https://github.com/Xerrion/servicenow-platform-mcp/issues/164)) ([29c5e5a](https://github.com/Xerrion/servicenow-platform-mcp/commit/29c5e5a0c21fef7c0fffc936abe4ea8ea494bf0d))

## [1.0.0](https://github.com/Xerrion/servicenow-platform-mcp/compare/v0.12.1...v1.0.0) (2026-09-10)


### ⚠ BREAKING CHANGES

* migrate scripts to record_write.data; remove script_path/script_field and SCRIPT_ALLOWED_ROOT; retain XML validation ([#160](https://github.com/Xerrion/servicenow-platform-mcp/issues/160))

### Features

* migrate scripts to record_write.data; remove script_path/script_field and SCRIPT_ALLOWED_ROOT; retain XML validation ([#160](https://github.com/Xerrion/servicenow-platform-mcp/issues/160)) ([21aa006](https://github.com/Xerrion/servicenow-platform-mcp/commit/21aa00685c58f283711a368cc6075eefa224d092))

## [0.12.1](https://github.com/Xerrion/servicenow-platform-mcp/compare/v0.12.0...v0.12.1) (2026-09-08)


### Bug Fixes

* harden ServiceNow introspection lookups ([#158](https://github.com/Xerrion/servicenow-platform-mcp/issues/158)) ([5438171](https://github.com/Xerrion/servicenow-platform-mcp/commit/5438171c17e92dad486a2772a5b576b2d93074d7))

## [0.12.0](https://github.com/Xerrion/servicenow-platform-mcp/compare/v0.11.0...v0.12.0) (2026-09-01)

### Features

* add read-only record analysis ([#153](https://github.com/Xerrion/servicenow-platform-mcp/issues/153)) ([ffc6126](https://github.com/Xerrion/servicenow-platform-mcp/commit/ffc61262dc9621c86392c43c9d6182e7b3af9b6b))

### Documentation

* update README and installation guide ([#155](https://github.com/Xerrion/servicenow-platform-mcp/issues/155)) ([adb3655](https://github.com/Xerrion/servicenow-platform-mcp/commit/adb3655a7db9c308163fe66cc25c613563890023))

## [0.11.0](https://github.com/Xerrion/servicenow-platform-mcp/compare/v0.10.0...v0.11.0) (2026-08-31)

### Features

* add API key auth and safer table querying ([#122](https://github.com/Xerrion/servicenow-platform-mcp/issues/122)) ([fb1394a](https://github.com/Xerrion/servicenow-platform-mcp/commit/fb1394a38c04cd578c5d32f5a11e26a901ec44f7))
* add artifact_create and artifact_update tools with script_path support ([#69](https://github.com/Xerrion/servicenow-platform-mcp/issues/69)) ([58327a8](https://github.com/Xerrion/servicenow-platform-mcp/commit/58327a8f5ea8940103757de6ea752eb3900952a3))
* add attachment MCP tools ([#64](https://github.com/Xerrion/servicenow-platform-mcp/issues/64)) ([aadfc20](https://github.com/Xerrion/servicenow-platform-mcp/commit/aadfc2099a0c5c5c7df79111dd187990cbf819c0))
* add domain-specific tool packages with composable presets ([#45](https://github.com/Xerrion/servicenow-platform-mcp/issues/45)) ([29ff75e](https://github.com/Xerrion/servicenow-platform-mcp/commit/29ff75e0d76ea782bdbc95f6c6c79af54387ded4))
* add legacy workflow introspection tools ([#50](https://github.com/Xerrion/servicenow-platform-mcp/issues/50)) ([f211fef](https://github.com/Xerrion/servicenow-platform-mcp/commit/f211fef4530556c77f108180357f813ea2df118d))
* add Service Catalog API domain tools ([#48](https://github.com/Xerrion/servicenow-platform-mcp/issues/48)) ([9a81b28](https://github.com/Xerrion/servicenow-platform-mcp/commit/9a81b281614dce00184833515981bac754e96e80))
* add ServiceNowQuery builder and wire time-filtering across all modules ([#35](https://github.com/Xerrion/servicenow-platform-mcp/issues/35)) ([ef9ffe4](https://github.com/Xerrion/servicenow-platform-mcp/commit/ef9ffe4a955916748ba35101aa38fa50ee7fc08d))
* **atf:** add ServiceNow ATF testing tool group ([#43](https://github.com/Xerrion/servicenow-platform-mcp/issues/43)) ([6777254](https://github.com/Xerrion/servicenow-platform-mcp/commit/67772542a5eece47f04308c1f303f9b83e4a0ed5))
* Phase 3 — developer actions, investigations, and documentation tools ([5749dd8](https://github.com/Xerrion/servicenow-platform-mcp/commit/5749dd85740772248afe1a5534e9613af0bc0fca))
* record CRUD tools, shared safe_tool_call, mandatory field validation ([#39](https://github.com/Xerrion/servicenow-platform-mcp/issues/39)) ([dd05435](https://github.com/Xerrion/servicenow-platform-mcp/commit/dd054359117c5c8680fddae1e69d0eae71ed1f1f))
* ServiceNow MCP server Phase 1+2 complete ([cd67149](https://github.com/Xerrion/servicenow-platform-mcp/commit/cd6714977083c78701478dde27a0a6b5aa533e6d))
* structured error format and workflow hardening ([#52](https://github.com/Xerrion/servicenow-platform-mcp/issues/52)) ([3bc3302](https://github.com/Xerrion/servicenow-platform-mcp/commit/3bc3302482b234e60cf8cd39280d26ab1421ccc5))

### Bug Fixes

* align release-please workflow with manifest config and correct token ([#20](https://github.com/Xerrion/servicenow-platform-mcp/issues/20)) ([84cf1a9](https://github.com/Xerrion/servicenow-platform-mcp/commit/84cf1a99b6b52a9f58c3fbe6be849054f5b6bb45))
* exhaustive security, correctness, and performance improvements ([#29](https://github.com/Xerrion/servicenow-platform-mcp/issues/29)) ([5fac553](https://github.com/Xerrion/servicenow-platform-mcp/commit/5fac5537791854de4356f082006d17a8b7d5aab9))
* harden input validation in utility, documentation, and knowledge tools ([#61](https://github.com/Xerrion/servicenow-platform-mcp/issues/61)) ([05cbebc](https://github.com/Xerrion/servicenow-platform-mcp/commit/05cbebc3313e4ea1a7e335211fda1068b4699cf5))
* migrate release-please to manifest config for semver tags ([#18](https://github.com/Xerrion/servicenow-platform-mcp/issues/18)) ([cd6b6ed](https://github.com/Xerrion/servicenow-platform-mcp/commit/cd6b6ed371c095444f037b328c17c8635b09eb48))
* pass display_values through to query_records and table_query tool ([#41](https://github.com/Xerrion/servicenow-platform-mcp/issues/41)) ([ade440d](https://github.com/Xerrion/servicenow-platform-mcp/commit/ade440d817c9f444c93e6f1f9cbaa62b20e5433f))
* remvoe the workflow_dispatch that came in by mistake ([#23](https://github.com/Xerrion/servicenow-platform-mcp/issues/23)) ([67f058d](https://github.com/Xerrion/servicenow-platform-mcp/commit/67f058d65d69377b9c135be205606f71d6d8989e))
* resolve 22 SonarQube quick-win issues ([#59](https://github.com/Xerrion/servicenow-platform-mcp/issues/59)) ([6399ba2](https://github.com/Xerrion/servicenow-platform-mcp/commit/6399ba264b8959c6b27ea097aab60d0f71de662d))
* resolve flow map snapshot linkage ([8e5ce9f](https://github.com/Xerrion/servicenow-platform-mcp/commit/8e5ce9f8bfd7c4c367f120f2179921414b35e898))
* update publish step to use uv publish command with environment variable ([#25](https://github.com/Xerrion/servicenow-platform-mcp/issues/25)) ([1fc8b5c](https://github.com/Xerrion/servicenow-platform-mcp/commit/1fc8b5ceae31189e0ab85b152a6544a2b5440157))
* use token in command ([#27](https://github.com/Xerrion/servicenow-platform-mcp/issues/27)) ([07b6efe](https://github.com/Xerrion/servicenow-platform-mcp/commit/07b6efe5c8caf865ff02c3ccc527e05c2fb7d03c))

### Documentation

* add GitHub Copilot custom instructions ([#15](https://github.com/Xerrion/servicenow-platform-mcp/issues/15)) ([7b18159](https://github.com/Xerrion/servicenow-platform-mcp/commit/7b181595aebbf41ebe4b4d71a5776d39326ee4e3))
* add safety disclaimer, AI install playbook, and raw URL fetch pattern ([#74](https://github.com/Xerrion/servicenow-platform-mcp/issues/74)) ([592008b](https://github.com/Xerrion/servicenow-platform-mcp/commit/592008be8bbbd1fbc6cca14349c2dcbd59de4867))
* complete documentation overhaul ([#72](https://github.com/Xerrion/servicenow-platform-mcp/issues/72)) ([5f385d9](https://github.com/Xerrion/servicenow-platform-mcp/commit/5f385d933d697539dd387daeb06360ea325a581b))
* complete rewrite of AGENTS.md from codebase audit ([#53](https://github.com/Xerrion/servicenow-platform-mcp/issues/53)) ([46f526b](https://github.com/Xerrion/servicenow-platform-mcp/commit/46f526b402902781e7495e7a489455c37a16dbbb))
* comprehensive README.md rewrite with all 86 tools ([#55](https://github.com/Xerrion/servicenow-platform-mcp/issues/55)) ([ab5c498](https://github.com/Xerrion/servicenow-platform-mcp/commit/ab5c4987ef340118d1eb51b4e9668dabbaa78c88))
* update README and add banner SVG for improved presentation ([#16](https://github.com/Xerrion/servicenow-platform-mcp/issues/16)) ([37b1213](https://github.com/Xerrion/servicenow-platform-mcp/commit/37b1213a4564dbbff46f5daf9e7254a996d7b560))
* update README for artifact_write tools, remove OTel section, refresh counts ([#71](https://github.com/Xerrion/servicenow-platform-mcp/issues/71)) ([7980d38](https://github.com/Xerrion/servicenow-platform-mcp/commit/7980d38d518b6dbc62b7598b44a9153f3c78afea))

## [0.10.0](https://github.com/Xerrion/servicenow-platform-mcp/compare/v0.9.1...v0.10.0) (2026-07-20)

### Features

* add API key auth and safer table querying ([#122](https://github.com/Xerrion/servicenow-platform-mcp/issues/122)) ([fb1394a](https://github.com/Xerrion/servicenow-platform-mcp/commit/fb1394a38c04cd578c5d32f5a11e26a901ec44f7))

## [Unreleased]

### Changed

* Repeated calls now reuse a server-lifetime shared HTTP connection pool with bounded request telemetry. Direct `ServiceNowClient` instances retain independent transport ownership.
* Common reads now use explicit field projections and bounded sections. List-mode `query` requires `fields`; use `fields="*"` only for intentional full records. `record_read`, `describe`, and `flow` responses expose selection and truncation metadata for continuation.
* Added `METADATA_CACHE_TTL_SECONDS` (default 300, valid range 1-86400) for bounded choices, dictionary, script-field, and audit-configuration metadata caching. Mutable records, query results, flows, attachments, previews, and audit row counts remain uncached.
* Investigation findings now include provenance, and `investigate(action="explain", name=..., element_id=...)` supports direct dispatch while preserving legacy trial dispatch when `name` is omitted.

### Removed

* Removed the public `build_query` MCP tool. Pass ServiceNow encoded query strings directly to `query(encoded_query=...)`; custom packages that name `build_query` are invalid.

### Added

* `flow` unified tool for read-only Flow Designer inspection (Washington DC V2 + V1 fallback). Five actions: `inspect`, `find_by_table`, `decode_values`, `list_triggers`, `describe`. Available in `full` and `readonly` presets.

## [0.10.0](https://github.com/Xerrion/servicenow-platform-mcp/compare/v0.9.1...v0.10.0) (2026-05-08)

### Breaking Changes

* **tool surface consolidation**: ~50 specialized tools collapsed into 12 unified action-dispatchers: `list_tool_packages`, `query`, `build_query`, `describe`, `record_read`, `record_write`, `record_apply`, `attachment`, `attachment_write`, `investigate`, `resolve_choice`, `service_catalog`. All previous domain-specific tools (`incident_*`, `change_*`, `problem_*`, `cmdb_*`, `sc_req_*`, `knowledge_*`, etc.) and helper tool families (`changes_*`, `debug_*`, `docs_*`, `workflow_*`, `flow_*`, `meta_*`) were removed.
* **artifact tools folded into `record_write`**: the standalone `artifact_create` and `artifact_update` tools were removed; create/update script-bearing records (Business Rules, Script Includes, UI Policies, etc.) by calling `record_write` with the standard `table` parameter and optional `script_path`.
* **encoded queries are now first-class**: the `QueryTokenStore` was removed. `build_query` is retained but reshaped to be stateless - it returns the encoded query string directly in `data.query`, which agents pass straight to the `query` tool. Agents may also pass ServiceNow encoded query strings directly to `query` without going through `build_query` (e.g. `active=true^priority<=2`).
* **wire format change**: TOON serialization replaced with JSON. `serialize()` in `utils.py` now returns JSON; `resolve_query_token` was deleted.
* **package registry collapse**: 14 preset packages reduced to 4: `full`, `readonly`, `core_readonly`, `none`. Custom packages remain supported via comma-syntax (`MCP_TOOL_PACKAGE=query,describe,attachment`). Note that `service_catalog` is now a tool group name rather than a package — `MCP_TOOL_PACKAGE=service_catalog` resolves through the custom-package code path.
* **ATF removed**: all Automated Test Framework tools and the corresponding `ServiceNowClient` ATF methods were deleted.
* **Sentry tag values changed**: `tool.name` tag values now reflect the unified tool surface (e.g. `query`, `record_write`) instead of the previous specialized tool names.
* **loader simplification**: every tool group module now uses the unconditional 4-arg `register_tools(mcp, settings, auth_provider, choices=choices)` signature. The `domain_` prefix branching in `server.py` was removed.
* **state management**: `QueryTokenStore` was removed; only `PreviewTokenStore` and `_BaseTokenStore` remain in `state.py`.

Migration: see [`docs/agent-recipes.md`](docs/agent-recipes.md) for the canonical migration patterns and worked recipes covering the unified tool surface.

### Added

* unified action-dispatcher tools: `query`, `describe`, `record_read`, `record_write`, `record_apply`, `attachment`, `attachment_write`, `investigate`, `resolve_choice`, `service_catalog`, `list_tool_packages`.
* `docs/agent-recipes.md` with 10 worked recipes covering common ServiceNow workflows on the unified tool surface.
* `script_path` parameter on `record_write` for writing local script files to any table that has at least one script-bearing field (Business Rules, Script Includes, UI Policies, UI Actions, Client Scripts, Widgets, UI Pages, UI Macros, ACLs, etc.). Script fields are discovered at runtime from `sys_dictionary` via the new `DictionaryRegistry` — there is no hardcoded `artifact_type` enum or `SCRIPT_FIELD_MAP`. The registry walks the `sys_db_object.super_class` chain so fields inherited from parent tables (e.g. `catalog_script_client` inheriting from `sys_script_client`) are admitted automatically.
* `script_field` parameter on `record_write` for tables with multiple script-bearing fields (e.g. `sys_ui_policy.script_true`/`script_false`, `sp_widget.client_script`/`template`/`css`, `sys_ui_page.html`/`client_script`/`processing_script`). Defaults to the first field returned by `DictionaryRegistry.get_script_fields(table)`.
* `record_read` tool - read-only counterpart to `record_write`. Returns the masked record plus the `script_fields` list resolved from `sys_dictionary` for discovery-driven multi-field edits. Included in both the `full` and `readonly` presets.
* `describe(action='list_script_fields', table='<table>')` - returns the resolved super_class chain and the dictionary-driven script-bearing fields for any table at runtime.
* When the resolved script field has `internal_type == 'xml'` (e.g. `sys_ui_macro.xml`), `record_write` validates that the rendered XML parses (`xml.etree.ElementTree.fromstring`) before any platform call; malformed content is rejected with a structured error.

### Changed

* `build_query` retained from v0.9.x but scoped to the `full` package only and reshaped to be stateless — returns the encoded query string directly in `data.query`, no token store. Agents pass that string as the `query` parameter to the `query` tool on the next call.

### Removed

* ~50 specialized domain and helper tools (`incident_*`, `change_*`, `problem_*`, `cmdb_*`, `sc_req_*`, `knowledge_*`, `changes_*`, `debug_*`, `docs_*`, `workflow_*`, `flow_*`, `meta_*`).
* `artifact_create` and `artifact_update` tools (use `record_write` with `table` and `script_path`).
* `QueryTokenStore` (pass encoded queries directly to `query`, or use the stateless `build_query` helper in the `full` package).
* All ATF tools and `ServiceNowClient` ATF methods.
* TOON serialization helpers and `resolve_query_token`.
* 10 preset packages (kept: `full`, `readonly`, `core_readonly`, `none`).
* `domain_` prefix branching in `server.py` loader.

## [0.9.1](https://github.com/Xerrion/servicenow-devtools-mcp/compare/v0.9.0...v0.9.1) (2026-03-26)

### Documentation

* add safety disclaimer, AI install playbook, and raw URL fetch pattern ([#74](https://github.com/Xerrion/servicenow-devtools-mcp/issues/74)) ([592008b](https://github.com/Xerrion/servicenow-devtools-mcp/commit/592008be8bbbd1fbc6cca14349c2dcbd59de4867))
* complete documentation overhaul ([#72](https://github.com/Xerrion/servicenow-devtools-mcp/issues/72)) ([5f385d9](https://github.com/Xerrion/servicenow-devtools-mcp/commit/5f385d933d697539dd387daeb06360ea325a581b))

## [0.9.0](https://github.com/Xerrion/servicenow-devtools-mcp/compare/v0.8.0...v0.9.0) (2026-03-24)

### Features

* add artifact_create and artifact_update tools with script_path support ([#69](https://github.com/Xerrion/servicenow-devtools-mcp/issues/69)) ([58327a8](https://github.com/Xerrion/servicenow-devtools-mcp/commit/58327a8f5ea8940103757de6ea752eb3900952a3))

### Documentation

* update README for artifact_write tools, remove OTel section, refresh counts ([#71](https://github.com/Xerrion/servicenow-devtools-mcp/issues/71)) ([7980d38](https://github.com/Xerrion/servicenow-devtools-mcp/commit/7980d38d518b6dbc62b7598b44a9153f3c78afea))

## [0.8.0](https://github.com/Xerrion/servicenow-devtools-mcp/compare/v0.7.1...v0.8.0) (2026-03-11)

### Features

* add attachment MCP tools ([#64](https://github.com/Xerrion/servicenow-devtools-mcp/issues/64)) ([aadfc20](https://github.com/Xerrion/servicenow-devtools-mcp/commit/aadfc2099a0c5c5c7df79111dd187990cbf819c0))

### Bug Fixes

* resolve flow map snapshot linkage ([8e5ce9f](https://github.com/Xerrion/servicenow-devtools-mcp/commit/8e5ce9f8bfd7c4c367f120f2179921414b35e898))

## [0.7.1](https://github.com/Xerrion/servicenow-devtools-mcp/compare/v0.7.0...v0.7.1) (2026-03-06)

### Bug Fixes

* harden input validation in utility, documentation, and knowledge tools ([#61](https://github.com/Xerrion/servicenow-devtools-mcp/issues/61)) ([05cbebc](https://github.com/Xerrion/servicenow-devtools-mcp/commit/05cbebc3313e4ea1a7e335211fda1068b4699cf5))
* resolve 22 SonarQube quick-win issues ([#59](https://github.com/Xerrion/servicenow-devtools-mcp/issues/59)) ([6399ba2](https://github.com/Xerrion/servicenow-devtools-mcp/commit/6399ba264b8959c6b27ea097aab60d0f71de662d))

### Documentation

* comprehensive README.md rewrite with all 86 tools ([#55](https://github.com/Xerrion/servicenow-devtools-mcp/issues/55)) ([ab5c498](https://github.com/Xerrion/servicenow-devtools-mcp/commit/ab5c4987ef340118d1eb51b4e9668dabbaa78c88))

## [0.7.0](https://github.com/Xerrion/servicenow-devtools-mcp/compare/v0.6.0...v0.7.0) (2026-03-05)

### Features

* structured error format and workflow hardening ([#52](https://github.com/Xerrion/servicenow-devtools-mcp/issues/52)) ([3bc3302](https://github.com/Xerrion/servicenow-devtools-mcp/commit/3bc3302482b234e60cf8cd39280d26ab1421ccc5))

### Documentation

* complete rewrite of AGENTS.md from codebase audit ([#53](https://github.com/Xerrion/servicenow-devtools-mcp/issues/53)) ([46f526b](https://github.com/Xerrion/servicenow-devtools-mcp/commit/46f526b402902781e7495e7a489455c37a16dbbb))

## [0.6.0](https://github.com/Xerrion/servicenow-devtools-mcp/compare/v0.5.0...v0.6.0) (2026-03-04)

### Features

* add legacy workflow introspection tools ([#50](https://github.com/Xerrion/servicenow-devtools-mcp/issues/50)) ([f211fef](https://github.com/Xerrion/servicenow-devtools-mcp/commit/f211fef4530556c77f108180357f813ea2df118d))
* add Service Catalog API domain tools ([#48](https://github.com/Xerrion/servicenow-devtools-mcp/issues/48)) ([9a81b28](https://github.com/Xerrion/servicenow-devtools-mcp/commit/9a81b281614dce00184833515981bac754e96e80))

## [0.5.0](https://github.com/Xerrion/servicenow-devtools-mcp/compare/v0.4.1...v0.5.0) (2026-03-03)

### Features

* add domain-specific tool packages with composable presets ([#45](https://github.com/Xerrion/servicenow-devtools-mcp/issues/45)) ([29ff75e](https://github.com/Xerrion/servicenow-devtools-mcp/commit/29ff75e0d76ea782bdbc95f6c6c79af54387ded4))
* add ServiceNowQuery builder and wire time-filtering across all modules ([#35](https://github.com/Xerrion/servicenow-devtools-mcp/issues/35)) ([ef9ffe4](https://github.com/Xerrion/servicenow-devtools-mcp/commit/ef9ffe4a955916748ba35101aa38fa50ee7fc08d))
* **atf:** add ServiceNow ATF testing tool group ([#43](https://github.com/Xerrion/servicenow-devtools-mcp/issues/43)) ([6777254](https://github.com/Xerrion/servicenow-devtools-mcp/commit/67772542a5eece47f04308c1f303f9b83e4a0ed5))
* Phase 3 — developer actions, investigations, and documentation tools ([5749dd8](https://github.com/Xerrion/servicenow-devtools-mcp/commit/5749dd85740772248afe1a5534e9613af0bc0fca))
* record CRUD tools, shared safe_tool_call, mandatory field validation ([#39](https://github.com/Xerrion/servicenow-devtools-mcp/issues/39)) ([dd05435](https://github.com/Xerrion/servicenow-devtools-mcp/commit/dd054359117c5c8680fddae1e69d0eae71ed1f1f))
* ServiceNow MCP server Phase 1+2 complete ([cd67149](https://github.com/Xerrion/servicenow-devtools-mcp/commit/cd6714977083c78701478dde27a0a6b5aa533e6d))

### Bug Fixes

* align release-please workflow with manifest config and correct token ([#20](https://github.com/Xerrion/servicenow-devtools-mcp/issues/20)) ([84cf1a9](https://github.com/Xerrion/servicenow-devtools-mcp/commit/84cf1a99b6b52a9f58c3fbe6be849054f5b6bb45))
* exhaustive security, correctness, and performance improvements ([#29](https://github.com/Xerrion/servicenow-devtools-mcp/issues/29)) ([5fac553](https://github.com/Xerrion/servicenow-devtools-mcp/commit/5fac5537791854de4356f082006d17a8b7d5aab9))
* migrate release-please to manifest config for semver tags ([#18](https://github.com/Xerrion/servicenow-devtools-mcp/issues/18)) ([cd6b6ed](https://github.com/Xerrion/servicenow-devtools-mcp/commit/cd6b6ed371c095444f037b328c17c8635b09eb48))
* pass display_values through to query_records and table_query tool ([#41](https://github.com/Xerrion/servicenow-devtools-mcp/issues/41)) ([ade440d](https://github.com/Xerrion/servicenow-devtools-mcp/commit/ade440d817c9f444c93e6f1f9cbaa62b20e5433f))
* remvoe the workflow_dispatch that came in by mistake ([#23](https://github.com/Xerrion/servicenow-devtools-mcp/issues/23)) ([67f058d](https://github.com/Xerrion/servicenow-devtools-mcp/commit/67f058d65d69377b9c135be205606f71d6d8989e))
* update publish step to use uv publish command with environment variable ([#25](https://github.com/Xerrion/servicenow-devtools-mcp/issues/25)) ([1fc8b5c](https://github.com/Xerrion/servicenow-devtools-mcp/commit/1fc8b5ceae31189e0ab85b152a6544a2b5440157))
* use token in command ([#27](https://github.com/Xerrion/servicenow-devtools-mcp/issues/27)) ([07b6efe](https://github.com/Xerrion/servicenow-devtools-mcp/commit/07b6efe5c8caf865ff02c3ccc527e05c2fb7d03c))

### Documentation

* add GitHub Copilot custom instructions ([#15](https://github.com/Xerrion/servicenow-devtools-mcp/issues/15)) ([7b18159](https://github.com/Xerrion/servicenow-devtools-mcp/commit/7b181595aebbf41ebe4b4d71a5776d39326ee4e3))
* update README and add banner SVG for improved presentation ([#16](https://github.com/Xerrion/servicenow-devtools-mcp/issues/16)) ([37b1213](https://github.com/Xerrion/servicenow-devtools-mcp/commit/37b1213a4564dbbff46f5daf9e7254a996d7b560))

## [0.4.1](https://github.com/Xerrion/servicenow-devtools-mcp/compare/v0.4.0...v0.4.1) (2026-03-02)

### Bug Fixes

* pass display_values through to query_records and table_query tool ([#41](https://github.com/Xerrion/servicenow-devtools-mcp/issues/41)) ([08427fb](https://github.com/Xerrion/servicenow-devtools-mcp/commit/08427fb773a755afbe50d4dc561d69442d6c9f21))

## [0.4.0](https://github.com/Xerrion/servicenow-devtools-mcp/compare/v0.3.0...v0.4.0) (2026-03-01)

### Features

* record CRUD tools, shared safe_tool_call, mandatory field validation ([#39](https://github.com/Xerrion/servicenow-devtools-mcp/issues/39)) ([2e2cd61](https://github.com/Xerrion/servicenow-devtools-mcp/commit/2e2cd6128a89b55755dfa4342af09a57cb109fcf))

## [0.3.0](https://github.com/Xerrion/servicenow-devtools-mcp/compare/v0.2.4...v0.3.0) (2026-02-27)

### Features

* add ServiceNowQuery builder and wire time-filtering across all modules ([#35](https://github.com/Xerrion/servicenow-devtools-mcp/issues/35)) ([eae49eb](https://github.com/Xerrion/servicenow-devtools-mcp/commit/eae49ebb316949f02042fc56521f34c17bf69d4b))

## [0.2.4](https://github.com/Xerrion/servicenow-devtools-mcp/compare/v0.2.3...v0.2.4) (2026-02-24)

### Bug Fixes

* exhaustive security, correctness, and performance improvements ([#29](https://github.com/Xerrion/servicenow-devtools-mcp/issues/29)) ([154174c](https://github.com/Xerrion/servicenow-devtools-mcp/commit/154174c2f36490b21aa24a32e61138633a70d1f4))

## [0.2.3](https://github.com/Xerrion/servicenow-devtools-mcp/compare/v0.2.2...v0.2.3) (2026-02-20)

### Bug Fixes

* use token in command ([#27](https://github.com/Xerrion/servicenow-devtools-mcp/issues/27)) ([9e46c71](https://github.com/Xerrion/servicenow-devtools-mcp/commit/9e46c7106aad7cad5f7baef4def2591237b2fe12))

## [0.2.2](https://github.com/Xerrion/servicenow-devtools-mcp/compare/v0.2.1...v0.2.2) (2026-02-20)

### Bug Fixes

* update publish step to use uv publish command with environment variable ([#25](https://github.com/Xerrion/servicenow-devtools-mcp/issues/25)) ([d1caea5](https://github.com/Xerrion/servicenow-devtools-mcp/commit/d1caea5f696ce66b8b45ddc65176b305ef3ceb6d))

## [0.2.1](https://github.com/Xerrion/servicenow-devtools-mcp/compare/v0.2.0...v0.2.1) (2026-02-20)

### Bug Fixes

* remvoe the workflow_dispatch that came in by mistake ([#23](https://github.com/Xerrion/servicenow-devtools-mcp/issues/23)) ([9598214](https://github.com/Xerrion/servicenow-devtools-mcp/commit/959821451fa2d050bc747d6f1bdfef35482fb396))

## [0.2.0](https://github.com/Xerrion/servicenow-devtools-mcp/compare/v0.1.0...v0.2.0) (2026-02-20)

### Features

* Phase 3 — developer actions, investigations, and documentation tools ([5749dd8](https://github.com/Xerrion/servicenow-devtools-mcp/commit/5749dd85740772248afe1a5534e9613af0bc0fca))
* ServiceNow MCP server Phase 1+2 complete ([cd67149](https://github.com/Xerrion/servicenow-devtools-mcp/commit/cd6714977083c78701478dde27a0a6b5aa533e6d))

### Bug Fixes

* align release-please workflow with manifest config and correct token ([#20](https://github.com/Xerrion/servicenow-devtools-mcp/issues/20)) ([b25e2bb](https://github.com/Xerrion/servicenow-devtools-mcp/commit/b25e2bb225702fcba57eeaf1542de5ca13a10bf9))
* migrate release-please to manifest config for semver tags ([#18](https://github.com/Xerrion/servicenow-devtools-mcp/issues/18)) ([f5b450d](https://github.com/Xerrion/servicenow-devtools-mcp/commit/f5b450d2daa05c894b313fe7080cb6e3227aa722))

### Documentation

* add GitHub Copilot custom instructions ([#15](https://github.com/Xerrion/servicenow-devtools-mcp/issues/15)) ([350ff80](https://github.com/Xerrion/servicenow-devtools-mcp/commit/350ff80740a264bd97c0453a3520e52c6060541d))
* update README and add banner SVG for improved presentation ([#16](https://github.com/Xerrion/servicenow-devtools-mcp/issues/16)) ([c1179ad](https://github.com/Xerrion/servicenow-devtools-mcp/commit/c1179ad5d1b2c42dc67c4803f48d496a27d72236))

## 0.1.0 (2026-02-20)

### Features

* Phase 3 — developer actions, investigations, and documentation tools ([5749dd8](https://github.com/Xerrion/servicenow-devtools-mcp/commit/5749dd85740772248afe1a5534e9613af0bc0fca))
* ServiceNow MCP server Phase 1+2 complete ([cd67149](https://github.com/Xerrion/servicenow-devtools-mcp/commit/cd6714977083c78701478dde27a0a6b5aa533e6d))

### Documentation

* add GitHub Copilot custom instructions ([#15](https://github.com/Xerrion/servicenow-devtools-mcp/issues/15)) ([350ff80](https://github.com/Xerrion/servicenow-devtools-mcp/commit/350ff80740a264bd97c0453a3520e52c6060541d))
