# Contributing

Thanks for considering a contribution to **servicenow-platform-mcp** - an MCP server (Model Context Protocol) that gives AI clients structured access to ServiceNow instances.

## Security Vulnerabilities

Do **not** file a public issue for security bugs. See [SECURITY.md](SECURITY.md) for responsible disclosure instructions.

## Reporting a Bug

Open a GitHub issue with:

- What you expected vs. what happened
- The `correlation_id` from the error response (if available)
- MCP client name and version
- Tool package in use (`MCP_TOOL_PACKAGE` value)
- Python version and OS
- Redacted environment (instance URL domain is fine; never include credentials)

## Proposing a Feature

Open an issue to discuss before writing code. Describe the use case, not just the solution.

## Submitting a Pull Request

### Setup

```bash
git clone https://github.com/Xerrion/servicenow-platform-mcp.git
cd servicenow-platform-mcp
uv sync --group dev
```

### Workflow

1. Create a feature branch from `main` (never commit to `main` directly).
2. Make your changes - one concern per PR.
3. Run the full check suite before pushing:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src/
uv run pytest
```

4. Commit with conventional messages: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`.
5. Open the PR via `gh pr create` or the GitHub UI.
6. Update documentation alongside any behavior change.

### CI

CI runs lint, type-check, and tests on Python 3.12, 3.13, and 3.14. All jobs must pass.

## Code Style

- **Formatter/linter**: Ruff - line length 120, double quotes, Python 3.12 target
- **Type annotations**: all function signatures fully typed (`disallow_untyped_defs`)
- **Imports**: absolute only, stdlib then third-party then local
- **Naming**: `snake_case` functions, `PascalCase` classes, `UPPER_SNAKE_CASE` constants
- **Package manager**: uv (not pip, not poetry)

## Deeper Development Guide

For architecture details, the tool-authoring recipe, testing patterns, and the release process, see [docs/wiki/Development.md](docs/wiki/Development.md).

## Conduct

Be respectful and constructive. Disagreements happen; keep them about the work.
