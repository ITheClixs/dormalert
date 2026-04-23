from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from src.notifier.base import NotificationEvent


class SMTPEmailNotifier:
    delivery_kind = "email"
    _ALLOWED_EVENT_TYPES = {"opening_alert", "opening_reminder"}

    def __init__(
        self,
        *,
        smtp_host: str,
        smtp_port: int,
        smtp_username: str | None,
        smtp_password: str | None,
        smtp_starttls: bool,
        email_from: str,
        email_to: tuple[str, ...],
        logger: logging.Logger | None = None,
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.smtp_starttls = smtp_starttls
        self.email_from = email_from
        self.email_to = email_to
        self.logger = logger or logging.getLogger("dormalert.notifier.email")

    def send(self, event: NotificationEvent) -> None:
        if event.event_type not in self._ALLOWED_EVENT_TYPES:
            return

        message = EmailMessage()
        message["From"] = self.email_from
        message["To"] = ", ".join(self.email_to)
        message["Subject"] = event.title
        message.set_content(self._body(event))

        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as client:
                    client.ehlo()
                    if self.smtp_starttls:
                        client.starttls()
                        client.ehlo()
                    if self.smtp_username:
                        client.login(self.smtp_username, self.smtp_password or "")
                    client.send_message(message)
                self.logger.info(
                    "SMTP email sent",
                    extra={
                        "event": "notification_email",
                        "notification_type": event.event_type,
                        "site_id": event.site_id,
                        "attempt": attempt,
                    },
                )
                return
            except Exception as exc:  # pragma: no cover - exercised in live operation
                last_error = exc
                self.logger.warning(
                    "SMTP email send attempt failed",
                    extra={
                        "event": "notification_email_retry",
                        "notification_type": event.event_type,
                        "site_id": event.site_id,
                        "attempt": attempt,
                        "error": str(exc),
                    },
                )
        raise RuntimeError(f"SMTP delivery failed after retries: {last_error}")

    def _body(self, event: NotificationEvent) -> str:
        lines = [
            event.message,
            "",
            f"Site: {event.site_id}",
            f"Timestamp (UTC): {event.timestamp_utc}",
        ]
        for label in ("confidence", "event_id", "facts", "anti_bot", "page_urls", "evidence_paths"):
            value = event.payload.get(label)
            if value in (None, (), [], "", {}):
                continue
            lines.append(f"{label}: {value}")
        return "\n".join(lines)
