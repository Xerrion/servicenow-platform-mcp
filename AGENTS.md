# Project instructions

**IMPORTANT**: When reviewing changes and ServiceNow docs are needed, refer to `docs/ServiceNowDocs` instead of doing websearches.
**IMPORTANT**: Refer to @openai-spec for full API documentation.

<!-- dev-tooling:start -->

## Development tooling

Run these mise tasks from the repository root. Existing project instructions and prerequisite tasks still apply.
Review formatting and safe-fix diffs; do not rewrite unrelated files or change tool versions as part of a routine check.

- Before finishing changes across stacks, run all configured lint, formatting, and type checks. Use `mise run check`.
- To format every selected language, run the aggregate formatter and review the diff. Use `mise run format`.
- After cross-stack behavior changes, run all configured test suites and their prerequisites. Use `mise run test`.
- When project dependencies changed or are missing, sync dependencies for selected projects. Use `mise run deps`.

### Markdown

- After Markdown edits, run Markdownlint and Prettier checks. Use `mise run check:markdown`.
- When Markdown formatting needs correction, run Prettier scoped to Markdown, not JavaScript or TypeScript. Use `mise run format:markdown`.

### Python

- After dependency changes or before checks in a fresh checkout, sync the locked project environment. Use `mise run deps:python`.
- After Python edits, check Ruff lint and formatting. Use `mise run lint:python`.
- When Python formatting needs correction, run the formatter. Use `mise run format:python`.
- When applying safe Ruff lint fixes, review the resulting diff. Use `mise run fix:python`.
- After Python API, annotation, import, or dependency changes, check types with ty. Use `mise run typecheck:python`.
- After behavior changes, run pytest quietly with short tracebacks; project markers, coverage, and plugins still apply. Use `mise run test:python`.

Tool versions, variables, and tasks in higher-precedence mise files override the generated fragment. Check `mise config` and `mise tasks ls` when behavior differs.
Run installation or dependency-sync commands only when needed; they can download packages or execute dependency build scripts.
<!-- dev-tooling:end -->
