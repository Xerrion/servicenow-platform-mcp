"""Sentry error tracking for the ServiceNow MCP server.

Provides opt-in error capture with graceful degradation when
sentry-sdk is not installed. All public functions are safe to call
regardless of whether Sentry is available - they silently no-op
when the package is missing or no DSN is configured.
"""

import logging
from importlib.metadata import version as pkg_version
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from servicenow_mcp.config import Settings


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dynamic release version
# ---------------------------------------------------------------------------


def _load_release() -> str:
    try:
        return f"servicenow-platform-mcp@{pkg_version('servicenow-platform-mcp')}"
    except Exception:
        return "servicenow-platform-mcp@unknown"


_RELEASE = _load_release()

# ---------------------------------------------------------------------------
# Try-import sentry-sdk
# ---------------------------------------------------------------------------


def _load_sentry_sdk() -> tuple[Any, bool]:
    try:
        import sentry_sdk
    except ImportError:
        return None, False
    return sentry_sdk, True


sentry_sdk, HAS_SENTRY = _load_sentry_sdk()


def _load_mcp_integration() -> tuple[Any, bool]:
    if not HAS_SENTRY:
        return None, False
    try:
        from sentry_sdk.integrations.mcp import MCPIntegration
    except ImportError:
        return None, False
    return MCPIntegration, True


MCPIntegration, _HAS_MCP_INTEGRATION = _load_mcp_integration()

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_initialized: bool = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def setup_sentry(settings: "Settings") -> None:
    """Initialize Sentry error tracking with the configured DSN.

    No-ops when sentry-sdk is not installed or ``settings.sentry_dsn``
    is empty. Safe to call multiple times; only the first call takes effect.

    Args:
        settings: Application settings containing Sentry configuration.
    """
    global _initialized  # noqa: PLW0603

    if _initialized:
        return

    if not HAS_SENTRY:
        logger.debug("Sentry disabled (sentry-sdk not installed)")
        _initialized = True
        return

    dsn = settings.sentry_dsn.strip()
    if not dsn:
        logger.debug("Sentry disabled (no DSN configured)")
        _initialized = True
        return

    environment = settings.sentry_environment.strip() or settings.servicenow_env

    integrations: list[Any] = []
    if _HAS_MCP_INTEGRATION:
        integrations.append(MCPIntegration())

    from sentry_sdk.integrations.stdlib import StdlibIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=_RELEASE,
        send_default_pii=False,
        include_local_variables=False,
        disabled_integrations=[StdlibIntegration()],
        integrations=integrations,
        traces_sample_rate=0.1,
        profiles_sample_rate=None,
    )

    _initialized = True
    logger.info("Sentry initialized (environment=%s)", environment)


def capture_exception(exc: BaseException | None = None) -> None:
    """Capture an exception and send it to Sentry.

    No-ops when sentry-sdk is not installed or Sentry has not been
    initialized (no DSN configured).

    Args:
        exc: The exception to capture. If ``None``, captures the
            current exception from ``sys.exc_info()``.
    """
    if not HAS_SENTRY or not _initialized:
        return
    sentry_sdk.capture_exception(exc)


def set_sentry_tag(key: str, value: str) -> None:
    """Set a tag on the current Sentry isolation scope.

    Tags are indexed key-value pairs used for filtering and searching
    in the Sentry UI.

    No-ops when sentry-sdk is not installed or not initialized.

    Args:
        key: The tag name.
        value: The tag value.
    """
    if not HAS_SENTRY or not _initialized:
        return
    sentry_sdk.set_tag(key, value)


def set_sentry_context(key: str, data: dict[str, Any]) -> None:
    """Set structured context on the current Sentry isolation scope.

    Context provides additional structured data attached to events
    but is not indexed for searching (unlike tags).

    No-ops when sentry-sdk is not installed or not initialized.

    Args:
        key: The context name (e.g. ``"tool"``, ``"servicenow"``).
        data: Structured data dict to attach.
    """
    if not HAS_SENTRY or not _initialized:
        return
    sentry_sdk.set_context(key, data)


def shutdown_sentry() -> None:
    """Flush pending events and shut down the Sentry client.

    Safe to call multiple times or when Sentry was never initialized.
    """
    global _initialized  # noqa: PLW0603

    if HAS_SENTRY and _initialized:
        try:
            client = sentry_sdk.get_client()
            if client.is_active():
                client.flush(timeout=2.0)
                client.close(timeout=2.0)
            logger.info("Sentry shut down")
        except Exception:
            logger.warning("Error shutting down Sentry", exc_info=True)

    _initialized = False
