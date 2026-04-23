from __future__ import annotations

import logging

import httpx

from src.notifier.base import NotificationEvent


class WebhookNotifier:
    def __init__(self, webhook_url: str, timeout_seconds: int, logger: logging.Logger | None = None) -> None:
        self.webhook_url = webhook_url
        self.timeout_seconds = timeout_seconds
        self.logger = logger or logging.getLogger("dormalert.notifier.webhook")
        self.client = httpx.Client()

    def send(self, event: NotificationEvent) -> None:
        response = self.client.post(
            self.webhook_url,
            timeout=self.timeout_seconds,
            json={
                "event_type": event.event_type,
                "site_id": event.site_id,
                "title": event.title,
                "message": event.message,
                "severity": event.severity.value,
                "payload": event.payload,
                "timestamp_utc": event.timestamp_utc,
            },
        )
        response.raise_for_status()
        self.logger.info(
            "Webhook notification sent",
            extra={
                "event": "notification_webhook",
                "notification_type": event.event_type,
                "site_id": event.site_id,
                "status_code": response.status_code,
            },
        )

    def close(self) -> None:
        self.client.close()

