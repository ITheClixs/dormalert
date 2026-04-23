from __future__ import annotations

import logging
import time

import httpx

from src.detector.models import ProbeResult, ProbeTarget
from src.utils.time import utcnow_iso


class ProbeError(RuntimeError):
    def __init__(self, target: ProbeTarget, reason: str) -> None:
        super().__init__(f"Probe {target.name} failed: {reason}")
        self.target = target
        self.reason = reason


class HttpProbeClient:
    def __init__(self, user_agent: str, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("dormalert.detector.http")
        self._client = httpx.Client(
            follow_redirects=True,
            headers={
                "User-Agent": user_agent,
                "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
            },
        )

    def fetch(self, target: ProbeTarget, *, timeout_seconds: int, max_retries: int) -> ProbeResult:
        last_error: Exception | None = None

        for attempt in range(1, max_retries + 2):
            started = time.perf_counter()
            try:
                response = self._client.get(target.url, timeout=timeout_seconds)
                duration_ms = int((time.perf_counter() - started) * 1000)
                self.logger.info(
                    "Probe completed",
                    extra={
                        "event": "probe_completed",
                        "site_target": target.name,
                        "url": target.url,
                        "status_code": response.status_code,
                        "duration_ms": duration_ms,
                    },
                )
                return ProbeResult(
                    target_name=target.name,
                    requested_url=target.url,
                    final_url=str(response.url),
                    status_code=response.status_code,
                    headers={key: value for key, value in response.headers.items()},
                    text=response.text,
                    duration_ms=duration_ms,
                    fetched_at=utcnow_iso(),
                )
            except Exception as exc:  # pragma: no cover - exercised in live operation
                last_error = exc
                self.logger.warning(
                    "Probe attempt failed",
                    extra={
                        "event": "probe_attempt_failed",
                        "site_target": target.name,
                        "url": target.url,
                        "attempt": attempt,
                        "max_attempts": max_retries + 1,
                        "error": str(exc),
                    },
                )

        raise ProbeError(target, str(last_error))

    def close(self) -> None:
        self._client.close()

