from __future__ import annotations

import logging
import re

import httpx

from src.notifier.base import NotificationEvent


class MetaWhatsAppCloudNotifier:
    """Delivers alerts to WhatsApp through Meta's official Cloud API.

    Business-initiated messages outside the 24-hour customer-service window
    must be template messages, so every alert is sent through one pre-approved
    utility template with a single body parameter carrying the alert text.
    The template body must be created as "DormAlert: {{1}}" (utility category)
    in WhatsApp Manager and approved once.
    """

    delivery_kind = "whatsapp"
    api_version = "v21.0"

    # Only the alerts that mean "go look at the site now" or "the monitor is
    # broken" are worth a WhatsApp ping, plus the daily heartbeat as proof the
    # channel itself still works. Everything else stays on console/log so the
    # phone does not get noisy.
    _ALLOWED_EVENT_TYPES = {
        "opening_alert",
        "opening_reminder",
        "closed_text_missing_alert",
        "availability_change",
        "manual_action_required",
        "repeated_failure",
        "heartbeat",
        "health_alert",
        "whatsapp_test",
    }

    # Template parameters reject newlines, tabs, and 4+ consecutive spaces,
    # and the rendered template body is capped at 1024 characters.
    _MAX_PARAMETER_CHARS = 900

    def __init__(
        self,
        *,
        access_token: str,
        phone_number_id: str,
        to: str,
        template_name: str = "dormalert_alert",
        template_language: str = "en",
        timeout_seconds: int = 15,
        logger: logging.Logger | None = None,
    ) -> None:
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.to = to
        self.template_name = template_name
        self.template_language = template_language
        self.timeout_seconds = timeout_seconds
        self.logger = logger or logging.getLogger("dormalert.notifier.whatsapp_cloud")
        self.client = httpx.Client()

    @property
    def endpoint(self) -> str:
        return f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"

    def send(self, event: NotificationEvent) -> None:
        if event.event_type not in self._ALLOWED_EVENT_TYPES:
            return

        response = self.client.post(
            self.endpoint,
            timeout=self.timeout_seconds,
            headers={"Authorization": f"Bearer {self.access_token}"},
            json={
                "messaging_product": "whatsapp",
                "to": self.to,
                "type": "template",
                "template": {
                    "name": self.template_name,
                    "language": {"code": self.template_language},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": self._template_parameter(event)}
                            ],
                        }
                    ],
                },
            },
        )
        if response.status_code >= 300:
            raise RuntimeError(
                f"WhatsApp Cloud API rejected the message (HTTP {response.status_code}): "
                f"{response.text[:300]}"
            )
        payload = response.json()
        messages = payload.get("messages") or []
        if not messages:
            raise RuntimeError(
                f"WhatsApp Cloud API returned no message id; response: {response.text[:300]}"
            )
        self.logger.info(
            "WhatsApp Cloud notification accepted",
            extra={
                "event": "notification_whatsapp_cloud",
                "notification_type": event.event_type,
                "site_id": event.site_id,
                "message_id": messages[0].get("id"),
            },
        )

    def _template_parameter(self, event: NotificationEvent) -> str:
        text = f"{event.title} - {event.message}"
        text = re.sub(r"\s+", " ", text).strip()
        return text[: self._MAX_PARAMETER_CHARS]

    def close(self) -> None:
        self.client.close()
