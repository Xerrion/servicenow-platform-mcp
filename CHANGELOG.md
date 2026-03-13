# Changelog

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
