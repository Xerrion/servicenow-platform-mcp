"""Bounded operational telemetry for ServiceNow HTTP traffic."""

import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx

from servicenow_mcp.sentry import set_sentry_context


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HttpTelemetrySnapshot:
    """Immutable aggregate ServiceNow HTTP measurements."""

    request_count: int
    completed_request_count: int
    failed_request_count: int
    response_bytes: int
    total_duration_ms: float
    shared_pool_request_count: int


class HttpTelemetry:
    """Keep fixed-size aggregate ServiceNow HTTP measurements."""

    def __init__(self) -> None:
        self._request_count = 0
        self._completed_request_count = 0
        self._failed_request_count = 0
        self._response_bytes = 0
        self._total_duration_ms = 0.0
        self._shared_pool_request_count = 0

    def record_started(self, *, is_shared_pool: bool) -> None:
        """Count a started HTTP request and its pool mode."""
        self._request_count += 1
        if is_shared_pool:
            self._shared_pool_request_count += 1

    def record_completed(self, *, response_bytes: int, duration_ms: float) -> None:
        """Add measurements for a completed HTTP request."""
        self._completed_request_count += 1
        self._response_bytes += max(response_bytes, 0)
        self._total_duration_ms += max(duration_ms, 0.0)

    def record_failed(self, *, duration_ms: float) -> None:
        """Add measurements for a failed HTTP request."""
        self._failed_request_count += 1
        self._total_duration_ms += max(duration_ms, 0.0)

    def snapshot(self) -> HttpTelemetrySnapshot:
        """Return the current fixed-size aggregate measurements."""
        return HttpTelemetrySnapshot(
            request_count=self._request_count,
            completed_request_count=self._completed_request_count,
            failed_request_count=self._failed_request_count,
            response_bytes=self._response_bytes,
            total_duration_ms=self._total_duration_ms,
            shared_pool_request_count=self._shared_pool_request_count,
        )

    def sentry_context(self) -> dict[str, int | float]:
        """Return bounded aggregate values for Sentry context."""
        snapshot = self.snapshot()
        return {
            "request_count": snapshot.request_count,
            "completed_request_count": snapshot.completed_request_count,
            "failed_request_count": snapshot.failed_request_count,
            "response_bytes": snapshot.response_bytes,
            "total_duration_ms": round(snapshot.total_duration_ms, 3),
            "shared_pool_request_count": snapshot.shared_pool_request_count,
        }


class TelemetryAsyncClient(httpx.AsyncClient):
    """HTTPX client that records bounded request aggregates."""

    def __init__(self, *, telemetry: HttpTelemetry, is_shared_pool: bool, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._telemetry = telemetry
        self._is_shared_pool = is_shared_pool

    async def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        """Send one request and record safe operational measurements."""
        self._telemetry.record_started(is_shared_pool=self._is_shared_pool)
        started = perf_counter()
        try:
            response = await super().send(request, **kwargs)
        except BaseException:
            duration_ms = (perf_counter() - started) * 1000
            self._telemetry.record_failed(duration_ms=duration_ms)
            set_sentry_context("http_telemetry", self._telemetry.sentry_context())
            logger.info(
                "ServiceNow HTTP request failed method=%s duration_ms=%.3f shared_pool=%s",
                request.method,
                duration_ms,
                self._is_shared_pool,
            )
            raise

        duration_ms = (perf_counter() - started) * 1000
        response_bytes = response.num_bytes_downloaded or len(response.content)
        self._telemetry.record_completed(response_bytes=response_bytes, duration_ms=duration_ms)
        set_sentry_context("http_telemetry", self._telemetry.sentry_context())
        logger.info(
            "ServiceNow HTTP request completed method=%s status_code=%d duration_ms=%.3f response_bytes=%d shared_pool=%s",
            request.method,
            response.status_code,
            duration_ms,
            response_bytes,
            self._is_shared_pool,
        )
        return response
