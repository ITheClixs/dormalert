from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from src.utils.time import utcnow_iso


class NotificationSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(frozen=True)
class NotificationEvent:
    event_type: str
    site_id: str
    title: str
    message: str
    severity: NotificationSeverity
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp_utc: str = field(default_factory=utcnow_iso)


class Notifier(Protocol):
    def send(self, event: NotificationEvent) -> None:
        ...


class CompositeNotifier:
    def __init__(self, notifiers: list[Notifier], logger: logging.Logger | None = None) -> None:
        self.notifiers = notifiers
        self.logger = logger or logging.getLogger("dormalert.notifier")

    def send(self, event: NotificationEvent) -> None:
        for notifier in self.notifiers:
            try:
                notifier.send(event)
            except Exception as exc:  # pragma: no cover - exercised in live operation
                self.logger.error(
                    "Notifier failed",
                    extra={
                        "event": "notifier_failed",
                        "notifier": notifier.__class__.__name__,
                        "notification_type": event.event_type,
                        "site_id": event.site_id,
                        "error": str(exc),
                    },
                )

