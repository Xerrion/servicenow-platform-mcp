"""Bounded operational telemetry for ServiceNow HTTP traffic."""

import asyncio
import hashlib
import json
import logging
import marshal
import os
import stat
import sys
from contextlib import ExitStack, contextmanager, suppress
from contextvars import ContextVar
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, ClassVar, Literal, override
from uuid import uuid4

import httpx2
from pydantic import BaseModel, ConfigDict

from servicenow_mcp._http_diagnostics import request_diagnostics
from servicenow_mcp.sentry import add_sentry_breadcrumb, set_sentry_context

if TYPE_CHECKING:
    from collections.abc import Generator
    from types import CodeType


logger = logging.getLogger(__name__)


class ToolTrace(BaseModel):
    """Local identity and HTTP count for one tool invocation, including child tasks."""

    trace_id: str
    tool: str
    request_count: int = 0


_tool_trace: ContextVar[ToolTrace | None] = ContextVar(
    "servicenow_tool_trace", default=None
)
_flow_preview_trace: ContextVar[str | None] = ContextVar(
    "flow_preview_trace", default=None
)
# Temporary local evidence sink. Remove it with the preview instrumentation after diagnosis.
_FLOW_TRACE_ROOT = Path(
    "/private/var/folders/7n/70rkdwfs0v53vjm2cm0vf3xw0000gp/T/opencode"
)
_FLOW_TRACE_DIRECTORY = "servicenow-flow-preview-trace"
_FLOW_TRACE_FILENAME = "boundaries.jsonl"
_FLOW_TRACE_MAX_BYTES = 256 * 1024


def _write_flow_preview_trace(record: str) -> None:
    """Append only the sanitized boundary record to one fixed, owner-only bounded file."""
    encoded = (record + "\n").encode("utf-8")
    if len(encoded) > 4096:
        return
    try:
        import fcntl

        with ExitStack() as resources:
            root = os.open(
                _FLOW_TRACE_ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            resources.callback(os.close, root)
            root_info = os.fstat(root)
            if (
                root_info.st_uid != os.getuid()
                or stat.S_IMODE(root_info.st_mode) & 0o022
            ):
                return
            with suppress(FileExistsError):
                os.mkdir(_FLOW_TRACE_DIRECTORY, mode=0o700, dir_fd=root)
            directory = os.open(
                _FLOW_TRACE_DIRECTORY,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=root,
            )
            resources.callback(os.close, directory)
            info = os.fstat(directory)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                return
            target = os.open(
                _FLOW_TRACE_FILENAME,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=directory,
            )
            resources.callback(os.close, target)
            info = os.fstat(target)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
            ):
                return
            fcntl.flock(target, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if os.fstat(target).st_size + len(encoded) <= _FLOW_TRACE_MAX_BYTES:
                os.write(target, encoded)
    except ImportError, OSError:
        # File errors must not fail the MCP request. Never wait for another writer's lock.
        return


@contextmanager
def trace_flow_preview() -> Generator[None]:
    """Correlate one native preview across SDK, command, planner and output boundaries."""
    token = _flow_preview_trace.set(str(uuid4()))
    try:
        yield
    finally:
        _flow_preview_trace.reset(token)


@lru_cache(maxsize=32)
def _loaded_code_hash(code: CodeType) -> str:
    # Hash the loaded code, not source that could change after process startup.
    return hashlib.sha256(marshal.dumps(code)).hexdigest()


def trace_flow_preview_boundary(
    boundary: Literal[
        "protocol",
        "handler",
        "command",
        "planner.in",
        "planner.out",
        "handler.out",
        "wire.structured",
        "wire.text",
    ],
    *,
    arguments: Any = None,
    action: Any = None,
    edits: Any = None,
    flow: Any = None,
    summary: Any = None,
) -> None:
    """Emit fixed metadata only. Never serialize arguments, platform values or arbitrary keys."""
    trace_id = _flow_preview_trace.get()
    if trace_id is None:
        return
    caller = sys._getframe(1).f_code
    metadata: dict[str, Any] = {
        "marker": "flow-preview-boundaries-v1",
        "trace_id": trace_id,
        "pid": os.getpid(),
        "boundary": boundary,
        "module_path": caller.co_filename,
        "loaded_code_sha256": _loaded_code_hash(caller),
    }
    if isinstance(arguments, dict):
        declared = (
            "action",
            "sys_id",
            "name",
            "scope",
            "description",
            "action_type_sys_id",
            "subflow_sys_id",
            "inputs",
            "edits",
            "expected_scope",
            "expected_update_set",
            "expected_revision",
        )
        metadata["argument_keys"] = [key for key in declared if key in arguments]
        action, edits = arguments.get("action"), arguments.get("edits")
    if boundary in {"protocol", "handler", "command", "planner.in"}:
        metadata["action"] = (
            action
            if isinstance(action, str)
            and action in {"create", "add_action", "add_subflow", "edit"}
            else "other"
        )
        metadata["edit_count"] = (
            len(edits) if isinstance(edits, list) else 0 if edits is None else -1
        )
    if isinstance(flow, dict):
        metadata["node_counts"] = {
            kind: len(flow[key]) if isinstance(flow.get(key), list) else -1
            for kind, key in (
                ("action", "actionInstances"),
                ("flowlogic", "flowLogicInstances"),
                ("subflow", "subFlowInstances"),
            )
        }
    if boundary in {"planner.out", "handler.out", "wire.structured", "wire.text"}:
        for key, count in (
            ("changes", "change_count"),
            ("before", "before_count"),
            ("after", "after_count"),
            ("data_pills", "pill_count"),
        ):
            value = summary.get(key) if isinstance(summary, dict) else None
            metadata[count] = len(value) if isinstance(value, list) else -1
    record = json.dumps(metadata, sort_keys=True)
    _write_flow_preview_trace(record)
    logger.info("Flow preview boundary %s", record)


def configure_diagnostic_logging() -> None:
    """Write bounded diagnostics to stderr without enabling raw HTTP request logs."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    logger.setLevel(logging.INFO)
    for name in ("httpx2", "httpcore2"):
        logging.getLogger(name).setLevel(logging.WARNING)


def current_tool_trace() -> ToolTrace | None:
    """Return the invoking tool's trace, or None outside a tool invocation."""
    return _tool_trace.get()


@contextmanager
def trace_tool_call(tool: str) -> Generator[None]:
    """Trace a tool's total duration and preserve cancellation and nested context."""
    trace = ToolTrace(trace_id=str(uuid4()), tool=tool)
    token = _tool_trace.set(trace)
    started = perf_counter()
    outcome = "returned"
    logger.info(
        "ServiceNow tool started trace_id=%s tool=%s", trace.trace_id, trace.tool
    )
    try:
        yield
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except BaseException:
        outcome = "failed"
        raise
    finally:
        logger.info(
            "ServiceNow tool finished trace_id=%s tool=%s outcome=%s duration_ms=%.3f http_requests=%d",
            trace.trace_id,
            trace.tool,
            outcome,
            (perf_counter() - started) * 1000,
            trace.request_count,
        )
        _tool_trace.reset(token)


@contextmanager
def trace_authorization_wait() -> Generator[None]:
    """Measure header authorization, including lock and browser waits, without inputs."""
    trace = current_tool_trace()
    trace_id = trace.trace_id if trace else "-"
    started = perf_counter()
    outcome = "failed"
    logger.info("ServiceNow authorization started trace_id=%s", trace_id)
    try:
        yield
        outcome = "completed"
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    finally:
        logger.info(
            "ServiceNow authorization finished trace_id=%s outcome=%s duration_ms=%.3f",
            trace_id,
            outcome,
            (perf_counter() - started) * 1000,
        )


def request_operation(request: httpx2.Request) -> str:
    """Classify a request into fixed operation labels without exposing URL values."""
    path = request.url.path
    if path.startswith("/api/now/table/"):
        table = path.split("/")[4]
        metadata = {
            "sys_db_object": "table_metadata",
            "sys_dictionary": "dictionary",
            "sys_choice": "choices",
            "sys_documentation": "documentation",
        }
        return metadata.get(table, "records")
    if path.startswith("/api/now/stats/"):
        return "aggregate"
    if path.startswith("/api/now/attachment"):
        return "attachment"
    if path == "/oauth_token.do":
        return "oauth"
    return "other"


def timeout_phase(error: httpx2.TimeoutException) -> str:
    """Classify HTTPX timeouts without including exception text or request data."""
    for error_type, phase in (
        (httpx2.ConnectTimeout, "connect"),
        (httpx2.ReadTimeout, "read"),
        (httpx2.WriteTimeout, "write"),
        (httpx2.PoolTimeout, "pool"),
    ):
        if isinstance(error, error_type):
            return phase
    return "unknown"


class CacheName(StrEnum):
    """Fixed metadata cache names used by bounded telemetry."""

    CHOICES = "choices"
    DICTIONARY_CHAINS = "dictionary_chains"
    DICTIONARY_FIELDS = "dictionary_fields"
    DICTIONARY_SCRIPT_FIELDS = "dictionary_script_fields"
    AUDIT_TABLE_CONFIG = "audit_table_config"
    AUDIT_FIELD_CONFIG = "audit_field_config"


CacheEvent = Literal["hit", "miss", "expiration", "reload", "invalidation"]


class CacheTelemetrySnapshot(BaseModel):
    """Immutable measurements for one fixed metadata cache domain."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    hits: int = 0
    misses: int = 0
    expirations: int = 0
    reloads: int = 0
    invalidations: int = 0


class HttpTelemetrySnapshot(BaseModel):
    """Immutable aggregate ServiceNow HTTP measurements."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    request_count: int
    completed_request_count: int
    failed_request_count: int
    http_error_count: int
    response_bytes: int
    total_duration_ms: float
    shared_pool_request_count: int
    metadata_caches: dict[str, CacheTelemetrySnapshot]


class HttpTelemetry:
    """Keep fixed-size aggregate ServiceNow HTTP measurements."""

    def __init__(self) -> None:
        self._request_count = 0
        self._completed_request_count = 0
        self._failed_request_count = 0
        self._http_error_count = 0
        self._response_bytes = 0
        self._total_duration_ms = 0.0
        self._shared_pool_request_count = 0
        self._metadata_caches = {name: CacheTelemetrySnapshot() for name in CacheName}

    def record_started(self, *, is_shared_pool: bool) -> None:
        """Count a started HTTP request and its pool mode."""
        self._request_count += 1
        if is_shared_pool:
            self._shared_pool_request_count += 1

    def record_completed(
        self, *, response_bytes: int, duration_ms: float, status_code: int
    ) -> None:
        """Add measurements for a completed HTTP request."""
        self._completed_request_count += 1
        if status_code >= 400:
            self._http_error_count += 1
        self._response_bytes += max(response_bytes, 0)
        self._total_duration_ms += max(duration_ms, 0.0)

    def record_failed(self, *, duration_ms: float) -> None:
        """Add measurements for a failed HTTP request."""
        self._failed_request_count += 1
        self._total_duration_ms += max(duration_ms, 0.0)

    def record_cache_event(self, name: CacheName, event: CacheEvent) -> None:
        """Count one metadata cache event under a fixed cache name."""
        current = self._metadata_caches[name]
        values = {
            "hits": current.hits,
            "misses": current.misses,
            "expirations": current.expirations,
            "reloads": current.reloads,
            "invalidations": current.invalidations,
        }
        event_fields = {
            "hit": "hits",
            "miss": "misses",
            "expiration": "expirations",
            "reload": "reloads",
            "invalidation": "invalidations",
        }
        event_field = event_fields[event]
        values[event_field] += 1
        self._metadata_caches[name] = CacheTelemetrySnapshot(**values)
        set_sentry_context("cache_telemetry", self.cache_sentry_context())

    def snapshot(self) -> HttpTelemetrySnapshot:
        """Return the current fixed-size aggregate measurements."""
        return HttpTelemetrySnapshot(
            request_count=self._request_count,
            completed_request_count=self._completed_request_count,
            failed_request_count=self._failed_request_count,
            http_error_count=self._http_error_count,
            response_bytes=self._response_bytes,
            total_duration_ms=self._total_duration_ms,
            shared_pool_request_count=self._shared_pool_request_count,
            metadata_caches={
                name.value: value for name, value in self._metadata_caches.items()
            },
        )

    def cache_sentry_context(self) -> dict[str, dict[str, int]]:
        """Return fixed-name metadata cache counters for Sentry context."""
        return {
            name.value: {
                "hits": value.hits,
                "misses": value.misses,
                "expirations": value.expirations,
                "reloads": value.reloads,
                "invalidations": value.invalidations,
            }
            for name, value in self._metadata_caches.items()
        }

    def sentry_context(self) -> dict[str, int | float]:
        """Return bounded aggregate values for Sentry context."""
        snapshot = self.snapshot()
        return {
            "request_count": snapshot.request_count,
            "completed_request_count": snapshot.completed_request_count,
            "failed_request_count": snapshot.failed_request_count,
            "http_error_count": snapshot.http_error_count,
            "response_bytes": snapshot.response_bytes,
            "total_duration_ms": round(snapshot.total_duration_ms, 3),
            "shared_pool_request_count": snapshot.shared_pool_request_count,
        }


class TelemetryAsyncClient(httpx2.AsyncClient):
    """HTTPX client that records bounded request aggregates."""

    def __init__(
        self, *, telemetry: HttpTelemetry, is_shared_pool: bool, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self._telemetry = telemetry
        self._is_shared_pool = is_shared_pool

    @override
    async def send(self, request: httpx2.Request, **kwargs: Any) -> httpx2.Response:
        """Send one request and record safe operational measurements."""
        trace = current_tool_trace()
        trace_id = trace.trace_id if trace else "-"
        request_number = 0
        if trace is not None:
            trace.request_count += 1
            request_number = trace.request_count
        operation = request_operation(request)
        method = (
            request.method
            if request.method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}
            else "OTHER"
        )
        self._telemetry.record_started(is_shared_pool=self._is_shared_pool)
        started = perf_counter()
        diagnostic: dict[str, object] = {
            "trace_id": trace_id,
            "request_number": request_number,
            "operation": operation,
            "method": method,
            "shared_pool": self._is_shared_pool,
            **request_diagnostics(request),
        }
        logger.info(
            "ServiceNow HTTP request started trace_id=%s request=%d operation=%s method=%s",
            trace_id,
            request_number,
            operation,
            method,
        )
        try:
            response = await super().send(request, **kwargs)
        except BaseException as exc:
            duration_ms = (perf_counter() - started) * 1000
            self._telemetry.record_failed(duration_ms=duration_ms)
            set_sentry_context("http_telemetry", self._telemetry.sentry_context())
            outcome = (
                "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            )
            phase = (
                timeout_phase(exc)
                if isinstance(exc, httpx2.TimeoutException)
                else "none"
            )
            diagnostic.update(
                {
                    "outcome": outcome,
                    "timeout_phase": phase,
                    "duration_ms": round(duration_ms, 3),
                },
            )
            set_sentry_context("http_request", diagnostic)
            add_sentry_breadcrumb("servicenow.http", diagnostic, is_error=True)
            logger.info(
                "ServiceNow HTTP request %s trace_id=%s request=%d operation=%s method=%s "
                "timeout_phase=%s duration_ms=%.3f shared_pool=%s",
                outcome,
                trace_id,
                request_number,
                operation,
                method,
                phase,
                duration_ms,
                self._is_shared_pool,
            )
            raise

        duration_ms = (perf_counter() - started) * 1000
        # Buffered reads do not update num_bytes_downloaded, so buffered responses
        # rely on the content length. .content raises ResponseNotRead for unread
        # streams, so guard it the way httpx2's own content property does.
        if hasattr(response, "_content"):
            response_bytes = response.num_bytes_downloaded or len(response.content)
        else:
            response_bytes = response.num_bytes_downloaded
        self._telemetry.record_completed(
            response_bytes=response_bytes,
            duration_ms=duration_ms,
            status_code=response.status_code,
        )
        set_sentry_context("http_telemetry", self._telemetry.sentry_context())
        diagnostic.update(
            {
                "outcome": "http_error" if response.status_code >= 400 else "completed",
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 3),
                "response_bytes": response_bytes,
            },
        )
        set_sentry_context("http_request", diagnostic)
        add_sentry_breadcrumb(
            "servicenow.http", diagnostic, is_error=response.status_code >= 400
        )
        logger.info(
            "ServiceNow HTTP request completed trace_id=%s request=%d operation=%s method=%s "
            "status_code=%d duration_ms=%.3f response_bytes=%d shared_pool=%s",
            trace_id,
            request_number,
            operation,
            method,
            response.status_code,
            duration_ms,
            response_bytes,
            self._is_shared_pool,
        )
        return response
