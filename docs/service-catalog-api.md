# Service Catalog API coverage

The implementation covers the 24 operations described in
`docs/ServiceNowDocs/markdown/api-reference/rest-apis/c_ServiceCatalogAPI.md`
(Australia, updated 2026-03-12). Reference line numbers below refer to that file.
`openapi-specs/sn_sc_service_catalog_api_latest_spec.yaml` confirms endpoint methods and paths.
It identifies its version as `latest` and its source server as `dev272070.service-now.com`.
It does not establish contracts for every exported operation.

| Coverage classification | Count | Basis |
| --- | --- | --- |
| Public-reference operations | 24 | Each has its own section in the public reference. All are implemented. |
| Preserved baseline operations | 12 | Baseline `2fb39fd`: 11 public-reference operations and one OpenAPI-only variable-definition read. |
| Newly implemented operations | 13 | All 13 have public-reference sections with request semantics or examples. |
| New OpenAPI-only extensions | 0 | No new operation relies only on an exported route with empty schemas. |
| Unsupported exported operations | 26 | Required request, effect, response, and precondition contracts are missing. Completeness remains blocked. |

The disjoint inventory is 24 public operations, one preserved OpenAPI-only operation, and 26 unsupported operations.
Together they account for all 51 exported method/path pairs.

## Implemented operations

Paths below follow `/api/sn_sc/servicecatalog/`. Every action accepts `api_version` to use
`/api/sn_sc/{api_version}/servicecatalog/`. Available versions remain instance-dependent.
Tool results use JSON. XML negotiation is not exposed by this JSON MCP server.

| Method and path | Tool action | Parameters beyond the path | Source classification |
| --- | --- | --- | --- |
| GET `catalogs` | `service_catalog.catalogs_list` | `limit`, `text`, `view` | Public, preserved. Reference L951-L1165. |
| GET `catalogs/{sys_id}` | `service_catalog.catalog_get` | `view` | Public, preserved. Reference L1464-L1752. |
| GET `catalogs/{sys_id}/categories` | `service_catalog.categories_list` | `limit`, `offset`, `top_level_only`, `view` | Public, preserved. Reference L1166-L1463. |
| GET `categories/{sys_id}` | `service_catalog.category_get` | `view` | Public, preserved. Reference L1753-L2086. |
| GET `items` | `service_catalog.items_list` | `limit`, `offset`, `text`, `catalog`, `category`, `item_type`, `view` | Public, preserved. Reference L2087-L2571. |
| GET `items/{sys_id}` | `service_catalog.item_get` | `view` | Public, preserved. Reference L2705-L3317. |
| GET `items/{sys_id}/variables` | `service_catalog.item_variables` | `view`. No request body. | OpenAPI-only, preserved. Export L572-L592. Baseline client and tests establish existing behaviour. |
| GET `cart` | `service_catalog.cart_get` | None | Public, preserved. Reference L240-L831. |
| GET `cart/delivery_address/{user_id}` | `service_catalog.delivery_address` | `user_sys_id` supplies the path. Personal result masked. | Public, new. Reference L832-L950. |
| GET `items/{item_sys_id}/delegation/{user_sys_id}` | `service_catalog.delegation` | None | Public, new. Reference L2572-L2704. |
| GET `wishlist` | `service_catalog.wishlist_get` | None | Public, new. Reference L3318-L3740. |
| GET `wishlist/{cart_item_id}` | `service_catalog.wishlist_item_get` | None | Public, new. Reference L3741-L3946. |
| POST `items/{item_sys_id}/get_invalid_delegated_users` | `service_catalog.get_invalid_delegated_users` | `also_request_for` maps to `sysparm_also_request_for`. Read-only lookup. | Public, new. Reference L5838-L6024, body L5885-L5903. |
| POST `variables/{sys_id}/display_value` | `service_catalog.variable_display_value` | `value` maps to `sysparm_value`. Read-only lookup. Personal result masked. | Public, new. Reference L6486-L6626, body L6529-L6545 and example L6613-L6618. |
| POST `items/{sys_id}/order_now` | `service_catalog_preview.order_now` | `variables`, `quantity`, `requested_for`, `also_request_for` | Public, preserved. Reference L6025-L6262, body L6070-L6121. |
| POST `items/{sys_id}/add_to_cart` | `service_catalog_preview.add_to_cart` | `variables`, `quantity`, `requested_for`, `also_request_for` | Public, preserved. Reference L4812-L5120, body L4855-L4902. |
| POST `items/{sys_id}/submit_producer` | `service_catalog_preview.submit_producer` | `variables`, query `view`. Never sends ordering parameters. | Public, new. Reference L6263-L6485, body L6327-L6329 and example L6462-L6467. |
| POST `items/{sys_id}/add_to_wishlist` | `service_catalog_preview.add_to_wishlist` | `quantity`, as shown in the documented cURL example | Public, new. Reference L5121-L5256, example L5221-L5226. |
| POST `items/{sys_id}/checkout_guide` | `service_catalog_preview.checkout_guide` | `items` array with `sys_id`, string `sysparm_quantity`, and `variables` | Public, new. Reference L5257-L5837, body L5300-L5352. |
| PUT `items/{sys_id}/submit_guide` | `service_catalog_preview.submit_guide` | `variables`. Uses the documented object example. | Public, new. Reference L7319-L7956, example L7673-L7686. |
| PUT `cart/{cart_item_id}` | `service_catalog_preview.cart_item_update` | `quantity`, `variables` | Public, new. Reference L6627-L7318, body L6670-L6694. |
| DELETE `cart/{cart_item_id}` | `service_catalog_preview.cart_item_delete` | None | Public, new. Reference L21-L108. |
| DELETE `cart/{sys_id}/empty` | `service_catalog_preview.cart_empty` | `cart_sys_id` supplies the path | Public, new. Reference L109-L239. |
| POST `cart/submit_order` | `service_catalog_preview.cart_submit` | None | Public, preserved. Reference L4639-L4811. |
| POST `cart/checkout` | `service_catalog_preview.cart_checkout` | None | Public, preserved. Reference L3947-L4638. |

`variables/{sys_id}/display_value` is publicly documented. Its request body does not rely on the empty OpenAPI schema.
Its section heading uses `/sn/sc`, but the URL formats and cURL example specify `/api/sn_sc/servicecatalog/`.
The unsupported `variables/display_value` route has no variable ID segment and is a different operation.

`quantity`, `requested_for`, and `also_request_for` map to their `sysparm_` body fields.
`view`, `item_type`, and browse parameters map to their `sysparm_` query fields.
The MCP tools retain the existing default page size of 20.
Ordering accepts up to 50 additional users. The delegated-user validation lookup accepts a non-empty user array within the payload limit.

All mutation actions require `mutation_apply`. Preview uses GET endpoints only.
Preview does not execute producers or order-guide rules. Apply rechecks state, policy,
connection, and authorization. Tokens expire after 300 seconds and permit one application attempt.
Failures, cancellation, and uncertain outcomes consume the token. The server never replays a mutation.
Check ServiceNow before another preview if the remote outcome is unknown.
`checkout_guide` checks both `sc_request` and `sc_cart_item` policy, including immediately before dispatch.
The public reference documents a branch that adds cart items at L5404-L5405.

Producer preview requires read access to `sc_cat_item_producer` target metadata.
It fingerprints the visible scripts, template, and update timestamp without displaying the producer scripts.
The configured target table passes local policy at preview and apply.
Scripts can cause additional effects that preview cannot predict.
Cart item updates and deletions also read the exact `sc_cart_item` record.
Cart item updates with submitted variables also read definitions for the referenced catalog item when its ID is available.
Those definitions participate in the state fingerprint.
`cart_empty` observes the exact target cart and up to 999 item timestamps.
It rejects larger carts because it cannot establish a complete state fingerprint.

## Documentation differences

- The prose describes `submit_guide.variables` as an array, but its cURL example sends an object.
  The implementation follows that example and the existing native variables contract.
- The export marks browse `sysparm_text` as required but supplies an empty example.
  The prose and existing API contract permit browsing without text. That behaviour stays unchanged.
- `sysparm_type` examples differ between sources. The implementation passes `item_type` unchanged.
- The prose mentions `sysparm_fields` precedence but defines no endpoint-specific field-selection contract.
  The tools do not add an undocumented selector.
- Address and variable display-value lookups can return unlabelled personal text.
  Their successful results are fully masked. Other results check sensitive keys, variable names, labels, and common personal fields.
  Personal text in arbitrary free-text fields cannot be identified automatically.
- Submitted variable maps use observed definition labels, including nested definitions and each guide item's own definitions.
  Preview and apply responses use masked copies. Staged values and outgoing apply payloads retain their original values.
  Missing or unusable labels cause the corresponding map answers to be masked, including cart and wish-list reads without definitions.
- Delegated-user validation masks each name in `invalidUsers` while preserving the array length.
  An empty array remains empty. A non-empty array still identifies failed validation without disclosing names.

## Unsupported OpenAPI-only operations

The export has mostly empty descriptions and no request or response schemas for these operations.
Body declarations contain media types only. Nominal HTTP 200 declarations do not establish success semantics.
Paths establish route existence, but not safe payloads, effects, or state preconditions.
Full API completeness remains blocked for all 26 operations below.
The supplied local contract investigation found no sufficient contracts in the export or public local corpus.
Implementation requires authoritative request fields and types, required inputs, effects, permissions, and response/error contracts.
Mutation operations also need enough state semantics to define safe preview and apply checks.
No generic passthrough or guessed payload is exposed.
They are not treated as covered by the 24-operation public reference.

| Method | Path after `/api/sn_sc/servicecatalog/` |
| --- | --- |
| GET | `catalog_builder/validate_catalog_categories` |
| GET | `catalog_client_script/on_change_choices/{entity_id}` |
| DELETE | `item/{record_id}` |
| POST | `items/{sys_id}/price` |
| POST | `items/{sys_id}/save_catalog_item` |
| POST | `items/{sys_id}/save_producer` |
| PUT | `items/{sys_id}/versioning/cancel_review` |
| POST | `items/{sys_id}/versioning/checkout` |
| GET | `producer/{producer_id}/record/{record_id}` |
| POST | `question_designer/create_resource/{action}` |
| POST | `question_designer/delete_resource/{action}` |
| POST | `question_designer/update_resource/{action}/{sys_id}` |
| GET | `record_id/{record_id}/wizard/{wizard_id}` |
| GET | `service_fulfillment/generateStagePoolIds/{quantity}` |
| POST | `service_fulfillment/step` |
| PUT | `service_fulfillment/step/{sys_id}` |
| DELETE | `service_fulfillment/step/{sys_id}` |
| GET | `service_fulfillment/step_configs` |
| POST | `template/{sys_id}/duplicate` |
| POST | `variableSetLayout` |
| POST | `variables/display_value` |
| POST | `variables/{sys_id}/dynamic_value` |
| POST | `variables/{sys_id}/validate_regex` |
| PUT | `variables/{table_name}/{sys_id}` |
| GET | `wizard/{sys_id}` |
| POST | `wizard/{sys_id}/record_id/{record_id}` |

## Offline verification

`tests/test_catalog_api_contracts.py` checks payloads, methods, query parameters,
preview isolation, tool exposure, masking, and one-shot failure behaviour with mocked HTTP.
It does not validate live producer scripts, routing, fulfilment, or instance-specific API versions.

Regenerate tool schema fixtures from the server with:

```sh
UPDATE_TOOL_SCHEMAS=1 uv run pytest tests/test_native_wire.py::test_native_schema_snapshot
```
