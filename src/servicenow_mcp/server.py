"""Installed command for the local MCP stdio application."""

from pydantic import ValidationError

from servicenow_mcp.bootstrap.stdio import create_mcp_server
from servicenow_mcp.sentry import shutdown_sentry
from servicenow_mcp.telemetry import configure_diagnostic_logging


def _format_configuration_error(exc: ValidationError) -> str:
    """Describe invalid settings without including submitted values."""
    details = []
    for error in exc.errors(
        include_url=False, include_context=False, include_input=False
    ):
        location = error["loc"]
        setting = (
            ".".join(str(part).upper() for part in location)
            if location
            else "CONFIGURATION"
        )
        details.append(f"- {setting}: {error['msg']}")
    return "\n".join(
        [
            "Invalid ServiceNow MCP configuration:",
            *details,
            "Set required values in the process environment or in .env/.env.local in the working directory.",
        ],
    )


def main() -> None:
    """Run the stdio application and release telemetry on exit."""
    configure_diagnostic_logging()
    try:
        server = create_mcp_server()
    except ValidationError as exc:
        raise SystemExit(_format_configuration_error(exc)) from None
    try:
        server.run(transport="stdio")
    finally:
        shutdown_sentry()


if __name__ == "__main__":
    main()
