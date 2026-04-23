from __future__ import annotations

import logging

from src.notifier.base import NotificationEvent


class StdoutNotifier:
    delivery_kind = "console"

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("dormalert.notifier.console")

    def send(self, event: NotificationEvent) -> None:
        self.logger.info(
            "Notification event",
            extra={
                "event": "notification_console",
                "notification_type": event.event_type,
                "site_id": event.site_id,
                "severity": event.severity.value,
                "title": event.title,
                "notification_message": event.message,
                "payload": event.payload,
            },
        )
