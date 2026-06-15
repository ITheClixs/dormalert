from __future__ import annotations

import logging
import smtplib
from collections.abc import Mapping
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from src.notifier.base import NotificationEvent


class SMTPEmailNotifier:
    delivery_kind = "email"
    _ALLOWED_EVENT_TYPES = {
        "opening_alert",
        "opening_reminder",
        "email_test",
        "monitor_started",
        "closed_text_missing_alert",
        "repeated_failure",
        "heartbeat",
    }
    _PAYLOAD_FIELDS = (
        ("event_id", "Event ID"),
        ("confidence", "Confidence"),
        ("observed_status", "Observed status"),
        ("expected_text", "Expected monitored text"),
        ("state_reason", "State reason"),
        ("signals", "Signals"),
        ("facts", "Facts"),
        ("inferences", "Inferences"),
        ("anti_bot", "Anti-bot signals"),
        ("page_urls", "Page URLs"),
        ("evidence_paths", "Evidence paths"),
        ("monitored_sites", "Monitored sites"),
        ("detector_only", "Detector-only mode"),
        ("poll_intervals_seconds", "Poll intervals seconds"),
    )

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
        message["Subject"] = self._clean_header(event.title)
        message["Date"] = formatdate(localtime=False, usegmt=True)
        message["Message-ID"] = self._message_id()
        message["Reply-To"] = self.email_from
        message["Auto-Submitted"] = "auto-generated"
        message["X-Auto-Response-Suppress"] = "All"
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
        reason_by_type = {
            "email_test": "DormAlert sent this because the SMTP test command was run.",
            "monitor_started": "DormAlert sent this because the continuous monitor process started.",
            "closed_text_missing_alert": (
                "DormAlert sent this because a monitored closed/waitlist text disappeared or changed."
            ),
            "repeated_failure": (
                "DormAlert sent this because the detector failed several cycles in a row and may be "
                "blind to a real opening. Check the site and the GitHub Actions logs."
            ),
            "heartbeat": (
                "DormAlert sent this as its scheduled heartbeat. As long as these arrive on schedule, "
                "the monitor and the email channel are both working. If they stop, investigate."
            ),
        }
        reason = reason_by_type.get(
            event.event_type,
            "DormAlert sent this because the monitor emitted a confirmed opening notification.",
        )
        lines = [
            event.message,
            "",
            reason,
            "",
            f"Site: {event.site_id}",
            f"Timestamp (UTC): {event.timestamp_utc}",
        ]
        for key, label in self._PAYLOAD_FIELDS:
            self._append_payload(lines, label, event.payload.get(key))
        return "\n".join(lines)

    def _append_payload(self, lines: list[str], label: str, value: object) -> None:
        if value in (None, (), [], "", {}):
            return

        if isinstance(value, Mapping):
            lines.append(f"{label}:")
            for key in sorted(value):
                lines.append(f"- {key}: {self._format_value(value[key])}")
            return

        if isinstance(value, (list, tuple, set)):
            lines.append(f"{label}:")
            for item in value:
                lines.append(f"- {self._format_value(item)}")
            return

        lines.append(f"{label}: {self._format_value(value)}")

    def _format_value(self, value: object) -> str:
        if isinstance(value, Mapping):
            return ", ".join(f"{key}={item}" for key, item in sorted(value.items()))
        if isinstance(value, (list, tuple, set)):
            return ", ".join(str(item) for item in value)
        return str(value)

    def _message_id(self) -> str:
        _, address = parseaddr(self.email_from)
        if "@" in address:
            return make_msgid(domain=address.rsplit("@", 1)[1])
        return make_msgid()

    def _clean_header(self, value: str) -> str:
        return " ".join(value.split())
