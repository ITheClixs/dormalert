from __future__ import annotations

import logging

import httpx

from src.notifier.base import NotificationEvent


class CallMeBotWhatsAppNotifier:
    """Delivers alerts to WhatsApp through the free CallMeBot relay.

    CallMeBot exposes a simple authenticated GET endpoint
    (https://api.callmebot.com/whatsapp.php) that forwards a text message to a
    single pre-authorized WhatsApp number. It needs no paid plan or server, so
    it is the durable WhatsApp channel for GitHub Actions runs where email/SMTP
    is not configured.
    """

    delivery_kind = "whatsapp"
    endpoint = "https://api.callmebot.com/whatsapp.php"

    # CallMeBot reports some failures as HTTP 200 with the error in the HTML
    # body, so status-code checks alone would count a lost message as sent.
    _FAILURE_BODY_MARKERS = (
        "apikey is invalid",
        "api key is invalid",
        "missing parameters",
        "you are not registered",
    )

    # Only the alerts that mean "go look at the site now" or "the monitor is
    # broken" are worth a WhatsApp ping, plus the daily heartbeat as proof the
    # flaky CallMeBot relay itself still works. Everything else stays on
    # console/log so the phone does not get noisy.
    _ALLOWED_EVENT_TYPES = {
        "opening_alert",
        "opening_reminder",
        "closed_text_missing_alert",
        "availability_change",
        "manual_action_required",
        "repeated_failure",
        "heartbeat",
        "whatsapp_test",
    }

    def __init__(
        self,
        *,
        phone: str,
        apikey: str,
        timeout_seconds: int = 15,
        logger: logging.Logger | None = None,
    ) -> None:
        self.phone = phone
        self.apikey = apikey
        self.timeout_seconds = timeout_seconds
        self.logger = logger or logging.getLogger("dormalert.notifier.whatsapp")
        self.client = httpx.Client()

    def send(self, event: NotificationEvent) -> None:
        if event.event_type not in self._ALLOWED_EVENT_TYPES:
            return

        response = self.client.get(
            self.endpoint,
            timeout=self.timeout_seconds,
            params={
                "phone": self.phone,
                "apikey": self.apikey,
                "text": self._text(event),
            },
        )
        response.raise_for_status()
        body_lower = response.text.lower()
        failure_marker = next(
            (marker for marker in self._FAILURE_BODY_MARKERS if marker in body_lower),
            None,
        )
        if failure_marker is not None:
            raise RuntimeError(
                f"CallMeBot rejected the WhatsApp message ('{failure_marker}' in response body)."
            )
        self.logger.info(
            "WhatsApp notification sent",
            extra={
                "event": "notification_whatsapp",
                "notification_type": event.event_type,
                "site_id": event.site_id,
                "status_code": response.status_code,
                "response_snippet": response.text[:120],
            },
        )

    def _text(self, event: NotificationEvent) -> str:
        return f"DormAlert: {event.title}\n\n{event.message}"

    def close(self) -> None:
        self.client.close()
