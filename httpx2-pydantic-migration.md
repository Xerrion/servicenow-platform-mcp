---
status: not-started
phase: 1
updated: 2026-09-15
---

# Implementation plan: HTTPX2 and Pydantic

## Goal

Use HTTPX2 for all application-owned outbound HTTP and Pydantic for data models, validation, and JSON boundaries, while preserving public behavior and leaving Sentry unchanged.

## Context & Decisions

| Decision | Rationale | Source |
| --- | --- | --- |
| Use explicit `httpx2` imports throughout production code and tests. No `import httpx2 as httpx` compatibility layer. | Keep client types, exception types, instrumentation, and mock transports consistent. | `ref:6b1c3635-22a6-45c9-acbf-8796518a7b43`; `src/servicenow_mcp/auth.py`, `client.py`, `_client_transport.py`, `telemetry.py`. |
| Start with `httpx2>=2.12.0,<3`, retaining 2.12.0 in the migration lockfile. | The existing MCP dependency already resolves this version. Do not combine consolidation with an unrelated version upgrade. | `ref:6b1c3635-22a6-45c9-acbf-8796518a7b43`; `uv.lock`; [HTTPX2 exports](https://github.com/pydantic/httpx2/blob/v2.12.0/src/httpx2/httpx2/__init__.py). |
| Replace RESPX with HTTPX2-native `MockTransport` fixtures. | RESPX depends on and patches the old HTTPX/httpcore stack. A compatibility plugin that retains RESPX does not meet the consolidation goal. | `ref:6b1c3635-22a6-45c9-acbf-8796518a7b43`; [RESPX mock implementation](https://github.com/lundberg/respx/blob/0.23.1/respx/mocks.py); [HTTPX2 transports](https://pydantic.dev/docs/httpx2/advanced/transports/). |
| Leave Sentry configuration, transport, and dependencies unchanged. | The user explicitly approved this exception. Sentry's urllib3 dependency is not part of this migration. | User decision, 2026-09-15: "Lad sentry være som den er." |
| Use Pydantic `BaseModel` for application-owned data models and `TypeAdapter` for dynamic JSON values. | One modeling approach without imposing a fixed schema on arbitrary ServiceNow tables. | Confirmed user scope; `ref:6b1c3635-22a6-45c9-acbf-8796518a7b43`; [Pydantic JSON](https://pydantic.dev/docs/validation/latest/concepts/json/). |
| Keep standard Python runtime machinery. | Pydantic does not replace asyncio, locks, ContextVar, logging, clocks, collections, regex, compression, or binary processing. | Confirmed user scope; `metadata_cache.py`, `state.py`, `oauth_callback.py`. |
| Preserve timeout diagnostics, tool signatures, and response shapes. | This migration must not discard the current work or silently change agent-visible behavior. HTTPX2 is not a proven fix for the reported timeouts. | `ref:6b1c3635-22a6-45c9-acbf-8796518a7b43`; `tests/test_timeout_diagnostics.py`, `tests/test_response_contract.py`. |

## Verified scope

The planning inventory found:

- Eight production files importing HTTPX; OAuth has a separately constructed client and must not be missed.
- Twenty-one HTTP-related test files, including eighteen using RESPX. These counts overlap.
- Fourteen standard-library dataclasses across eight production files.
- Thirty-two executable JSON parsing/serialization calls across eleven production files, plus three outbound `json=` sites.
- Fourteen registered tools and seven investigation implementations with additional application-owned schemas.

Inventory source: `ref:6b1c3635-22a6-45c9-acbf-8796518a7b43`. Refresh counts and line references before implementation because this checkout is shared and changes externally.

### Existing model conversion map

| Owner under `src/servicenow_mcp/` | Models | Protected behavior |
| --- | --- | --- |
| `auth.py` | `AccessToken` | Frozen value, secret-free repr/serialization, monotonic expiry. |
| `telemetry.py` | `ToolTrace`, `CacheTelemetrySnapshot`, `HttpTelemetrySnapshot` | Mutable shared trace identity; frozen snapshots; request counts. |
| `metadata_cache.py` | `_CacheEntry` | Frozen generic entry; cached value identity; TTL and invalidation. |
| `tools/_dictionary_models.py` | `ScriptField`, `DictionaryField` | Frozen values, independent metadata defaults, unknown fields retained. |
| `tools/_query_parsing.py` | `AggregatePlan`, `LabelPair`, `Projection` | Frozen values, independent lists, derived properties, projection shape. |
| `tools/_query_preparation.py` | `AggregateRequest`, `ListRequest` | Mode validation and limit behavior. |
| `tools/_record_write_validation.py` | `WriteRequest` | Action constraints and write-policy ordering. |
| `tools/_audit.py` | `FieldAudit` | Missing evidence remains distinct from false; no-audit precedence. |

Service objects such as clients, registries, query builders, caches, and token stores remain service objects. Their structured entries or payloads can be models; their locks and algorithms are not replaced.

## Phase 1: Protect contracts and isolate tests [PENDING]

- [ ] **1.1 Refresh git status, staged/unstaged diffs, and untracked diagnostics tests; identify and preserve existing work.** ← CURRENT
- [ ] 1.2 Establish fail-closed test networking before changing imports. Block external HTTP, browser authorization, real environment files, and remote telemetry. Allow loopback only in explicitly scoped callback/TLS/proxy fixtures.
- [ ] 1.3 Characterize tool schemas, response envelopes, masking, timeout diagnostics, OAuth behavior, and write previews before migration.
- [ ] 1.4 Record existing JSON/coercion behavior: false/null/zero/empty values, string choices, unknown keys, large integers, non-finite values, duplicate keys, BOM/encoding, malformed JSON, depth limits, and `default=str` behavior.

**Acceptance:** an offline baseline exists; compatibility differences have explicit expected outcomes rather than silent corrections.

## Phase 2: Migrate HTTPX and test mocks together [PENDING]

Depends on Phase 1.

- [ ] 2.1 Add a small test fixture around `httpx2.MockTransport`: exact request matching, callbacks/queued responses, exceptions, request history, expected-call assertions, and rejection of unexpected requests. Do not recreate RESPX's complete interface.
- [ ] 2.2 Port HTTP-related tests and all production client construction, annotations, exception checks, and patch targets together. Cover shared clients, fallback clients, and OAuth.
- [ ] 2.3 Preserve `TelemetryAsyncClient.send()` instrumentation. Update suppression of raw HTTP logs for `httpx2` and `httpcore2`; retain structured timeout phases and cancellation distinctions.
- [ ] 2.4 Replace the direct HTTPX dependency, remove RESPX after its final usage, and regenerate the lockfile without unrelated upgrades. Verify reverse dependencies: old `httpx` and `httpcore` must disappear unless a specific new transitive blocker is reported. Sentry's urllib3 is explicitly allowed.
- [ ] 2.5 Verify TLS, proxies, binary transfer, URL encoding, headers, client ownership, pooling, and cancellation. HTTPX2 system trust is intentional; keep certificate verification enabled. Exercise custom CA settings, explicit SSL contexts, proxy environment variables, `NO_PROXY`, and `trust_env` with controlled local fixtures.

**Acceptance:** all application-owned HTTP uses HTTPX2; tests intercept every relevant path; Sentry remains unchanged; no dual-client runtime switch or silent fallback is introduced.

## Phase 3: Establish safe Pydantic models and validation [PENDING]

Depends on Phase 2.

- [ ] 3.1 Add safe Pydantic validation-error translation before existing `ValueError` handling. Never expose or send raw validation errors, error inputs, dynamic locations, or validator context to logs/Sentry/tool output.
- [ ] 3.2 Convert the fourteen dataclasses to `BaseModel`. Use explicit `ConfigDict`, `frozen=True` where required, and `Field(default_factory=...)`. Convert positional constructors to keyword arguments and sweep every call site.
- [ ] 3.3 Keep `ToolTrace` mutable and shared through ContextVar. Preserve cache value identity, default-container independence, equality semantics, TTL, single-flight loads, invalidation races, shielding, and cancellation. Do not assume Pydantic reproduces slots or deep immutability.
- [ ] 3.4 Model OAuth responses, callback parameters, and preview payload/storage entries. Use secret fields and explicit exclusion; unwrap only at the authentication boundary. Preserve one-shot preview consumption and write-policy ordering.
- [ ] 3.5 Keep Settings names, environment parsing, and `HTTPX_TIMEOUT_SECONDS` unchanged. Retain normal string/query/HTTP parsing machinery where the format is not JSON.

**Acceptance:** no standard-library dataclasses remain in production. Models validate at boundaries without copying trusted shared state or changing existing normalization.

## Phase 4: Move JSON boundaries to Pydantic [PENDING]

Depends on Phase 3.

- [ ] 4.1 Replace response JSON reads with endpoint-appropriate `model_validate_json()` or `TypeAdapter.validate_json()` over bytes. Use typed envelopes for known shapes and adapters for open record maps. Distinguish missing `result` from `result: null`.
- [ ] 4.2 Replace payload parsing while preserving the 256 KiB byte limit, depth 32, object-only root, identifier checks, and existing exceptions for arbitrary catalog/investigation keys.
- [ ] 4.3 Replace Flow JSON parsing after existing base64/gzip validation. Preserve compressed/decompressed size limits, UTF-8 handling, gzip completeness, accepted root shapes, and plain-text behavior.
- [ ] 4.4 Migrate response envelopes and serialization through `model_dump_json()`/`TypeAdapter.dump_json()`. Preserve nested null/false/zero/empty values, unknown ServiceNow fields, conditional metadata, structured timeout errors, and the safe serialization-failure envelope. Do not apply recursive `exclude_none`/`exclude_defaults`.
- [ ] 4.5 Serialize outbound JSON once with Pydantic and send via `content=` with the correct content type. Remove application-owned `json=` serialization sites. OAuth remains form-encoded; attachments remain bytes.
- [ ] 4.6 Preserve REST-401 evidence allowlists, masking, limits, and omission markers. Update JSON test helpers independently of the production serializer.

**Acceptance:** application JSON boundaries use Pydantic; no production stdlib JSON parsing/serialization or outbound `json=` sites remain. Dependency internals are outside this assertion. Unsupported values fail safely rather than being silently coerced.

## Phase 5: Model remaining application-owned contracts [PENDING]

Depends on Phase 4. Work in small vertical slices with passing tests.

- [ ] 5.1 Query and record tools: mode/action inputs, projection metadata, write previews/results, and diff entries.
- [ ] 5.2 Describe, choices, and audit: curated field metadata, selection, choice results, audit verdicts, evidence and counts.
- [ ] 5.3 Attachments and catalog: known request/result metadata; dynamic catalog variables and platform-owned records remain typed JSON maps.
- [ ] 5.4 Flow: stages, nodes, triggers, bindings, contracts, ordering, truncation and provenance; preserve decoded dynamic values.
- [ ] 5.5 Analysis and all seven investigations: parameter models, curated findings/results, windows, pagination, provenance and completeness warnings.
- [ ] 5.6 Remaining curated tool descriptions/results and bounded telemetry contexts. Keep Sentry's transport and settings unchanged.

**Acceptance:** all application-owned structured data contracts have Pydantic owners. Arbitrary ServiceNow records remain open and retain unknown fields. Public MCP primitive argument signatures and string results do not change. Ordinary dictionaries remain valid for collections, indexes and dynamic JSON.

## Phase 6: Verify and prepare rollout [PENDING]

Depends on all previous phases.

- [ ] 6.1 Run focused tests after each slice, then the complete non-integration suite. Include OAuth/callbacks, timeout phases, stderr privacy, real stdio framing, model identity, cache races, preview safety, JSON limits, binary/compressed data and exact response contracts.
- [ ] 6.2 Run Ruff, formatting, mypy, basedpyright and lockfile consistency checks. Compare pre-existing warnings separately from migration regressions.
- [ ] 6.3 Audit imports, dependency paths, constructors and JSON boundaries. No old client imports, compatibility aliases, RESPX interception, unclassified model dictionaries or new unsafe validation-error paths.
- [ ] 6.4 Update relevant installation, development, configuration, telemetry, recipe and troubleshooting documentation. State the Sentry exception and unchanged timeout behavior.
- [ ] 6.5 Review the full diff against the refreshed baseline. Prepare atomic migration milestones and a release/rollback note; commit, push or publish only on explicit user request.

**Acceptance:** offline contracts pass, the dependency graph matches the agreed scope, and existing diagnostics/user work remain intact. No live ServiceNow validation is claimed.

## Verification commands

Use the existing tooling. Install/sync only after approved manifest changes; validation itself must not silently change dependencies.

```bash
uv run --no-sync --offline ruff check .
uv run --no-sync --offline ruff format --check .
uv run --no-sync --offline mypy src/
uv run --no-sync --offline basedpyright
uv run --no-sync --offline pytest -m "not integration"
uv lock --check --offline
```

Mock transports do not prove TLS/proxy/connection-pool behavior. Those require controlled local fixtures, not live ServiceNow requests.

## Risks and rollout boundaries

- A partial mock migration could reach the network. Network blocking precedes import changes.
- Pydantic defaults can change coercion, omission, equality, identity and datetime/UUID serialization. Characterization tests define the contract; strictness is selected per boundary.
- `hide_input_in_errors=True` is not sufficient protection for `.errors()` or dynamic error locations. Translate failures before logging or returning them.
- Memory/performance changes from replacing slotted dataclasses need representative measurements; no slot-equivalence claim.
- Keep each milestone internally consistent. Roll back only migration-owned code/dependency/test changes, not the shared checkout.
- Release an application version and its matching lockfile together. Restart requires fresh OAuth authorization and regeneration of memory-only previews.

## Notes

- 2026-09-15: User confirmed Pydantic scope: models, validation, and JSON boundaries; ordinary Python runtime tools remain.
- 2026-09-15: User explicitly excluded Sentry from HTTP consolidation.
- 2026-09-15: Inventory and upstream compatibility research came from `ref:6b1c3635-22a6-45c9-acbf-8796518a7b43`.
- 2026-09-15: This is a plan. HTTPX2/Pydantic migration has not been implemented.
