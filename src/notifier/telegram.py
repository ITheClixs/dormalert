from __future__ import annotations

import logging

import httpx

from src.notifier.base import NotificationEvent


class TelegramNotifier:
    """Delivers alerts through the official Telegram Bot API.

    Telegram's Bot API is free, instant, and reliable, with no third-party relay
    and no business onboarding. It is the durable channel for GitHub Actions runs
    where email/SMTP is not configured.
    """

    delivery_kind = "telegram"
    api_base = "https://api.telegram.org"

    # Only the alerts that mean "go look at the site now" or "the monitor is
    # broken" are worth a ping. Everything else stays on console/log.
    _ALLOWED_EVENT_TYPES = {
        "opening_alert",
        "opening_reminder",
        "closed_text_missing_alert",
        "availability_change",
        "manual_action_required",
        "repeated_failure",
        "telegram_test",
    }

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        timeout_seconds: int = 15,
        logger: logging.Logger | None = None,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout_seconds = timeout_seconds
        self.logger = logger or logging.getLogger("dormalert.notifier.telegram")
        self.client = httpx.Client()

    def send(self, event: NotificationEvent) -> None:
        if event.event_type not in self._ALLOWED_EVENT_TYPES:
            return

        response = self.client.get(
            f"{self.api_base}/bot{self.bot_token}/sendMessage",
            timeout=self.timeout_seconds,
            params={
                "chat_id": self.chat_id,
                "text": self._text(event),
                "disable_web_page_preview": "true",
            },
        )
        response.raise_for_status()
        self.logger.info(
            "Telegram notification sent",
            extra={
                "event": "notification_telegram",
                "notification_type": event.event_type,
                "site_id": event.site_id,
                "status_code": response.status_code,
            },
        )

    def _text(self, event: NotificationEvent) -> str:
        return f"DormAlert: {event.title}\n\n{event.message}"

    def close(self) -> None:
        self.client.close()
