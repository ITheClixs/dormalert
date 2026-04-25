from __future__ import annotations

from email.message import EmailMessage

from src.notifier.base import NotificationEvent, NotificationSeverity
from src.notifier.email_smtp import SMTPEmailNotifier


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in = False
        self.messages: list[EmailMessage] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def ehlo(self) -> None:
        return None

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.logged_in = True

    def send_message(self, message: EmailMessage) -> None:
        self.messages.append(message)


def _notifier() -> SMTPEmailNotifier:
    return SMTPEmailNotifier(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="alerts@example.com",
        smtp_password="secret",
        smtp_starttls=True,
        email_from="alerts@example.com",
        email_to=("demirguven178@gmail.com",),
    )


def test_opening_email_uses_transactional_headers(monkeypatch) -> None:
    FakeSMTP.instances.clear()
    monkeypatch.setattr("src.notifier.email_smtp.smtplib.SMTP", FakeSMTP)
    event = NotificationEvent(
        event_type="opening_alert",
        site_id="studentvillage",
        title="DormAlert: Student Village waitlist is open",
        message="Student Village reached the confirmed open state with confidence 0.95.",
        severity=NotificationSeverity.CRITICAL,
        payload={
            "event_id": 7,
            "confidence": 0.95,
            "state_reason": "consecutive_open_confirmation_satisfied",
            "signals": ("closed_banners_removed", "register_form_present"),
            "facts": ("Register form observed on apply page.",),
            "page_urls": ("https://studentvillage.ch/en/apply/",),
        },
    )

    _notifier().send(event)

    smtp = FakeSMTP.instances[0]
    message = smtp.messages[0]
    assert smtp.started_tls is True
    assert smtp.logged_in is True
    assert message["To"] == "demirguven178@gmail.com"
    assert message["Subject"] == "DormAlert: Student Village waitlist is open"
    assert message["Date"]
    assert message["Message-ID"].endswith("@example.com>")
    assert message["Auto-Submitted"] == "auto-generated"
    assert message["X-Auto-Response-Suppress"] == "All"
    body = message.get_content()
    assert "State reason:" in body
    assert "consecutive_open_confirmation_satisfied" in body
    assert "Page URLs:" in body


def test_non_opening_events_are_not_emailed(monkeypatch) -> None:
    FakeSMTP.instances.clear()
    monkeypatch.setattr("src.notifier.email_smtp.smtplib.SMTP", FakeSMTP)
    event = NotificationEvent(
        event_type="availability_change",
        site_id="studentvillage",
        title="candidate",
        message="Opening candidate only.",
        severity=NotificationSeverity.WARNING,
    )

    _notifier().send(event)

    assert FakeSMTP.instances == []


def test_manual_email_test_event_is_allowed(monkeypatch) -> None:
    FakeSMTP.instances.clear()
    monkeypatch.setattr("src.notifier.email_smtp.smtplib.SMTP", FakeSMTP)
    event = NotificationEvent(
        event_type="email_test",
        site_id="system",
        title="DormAlert email test",
        message="Test message.",
        severity=NotificationSeverity.INFO,
    )

    _notifier().send(event)

    assert len(FakeSMTP.instances) == 1
    assert FakeSMTP.instances[0].messages[0]["Subject"] == "DormAlert email test"


def test_monitor_started_event_is_emailed(monkeypatch) -> None:
    FakeSMTP.instances.clear()
    monkeypatch.setattr("src.notifier.email_smtp.smtplib.SMTP", FakeSMTP)
    event = NotificationEvent(
        event_type="monitor_started",
        site_id="system",
        title="DormAlert monitor is running",
        message="DormAlert continuous monitoring has started.",
        severity=NotificationSeverity.INFO,
        payload={
            "monitored_sites": ("livingscience", "studentvillage"),
            "detector_only": True,
            "poll_intervals_seconds": {
                "livingscience": 300,
                "studentvillage": 180,
            },
        },
    )

    _notifier().send(event)

    assert len(FakeSMTP.instances) == 1
    message = FakeSMTP.instances[0].messages[0]
    assert message["To"] == "demirguven178@gmail.com"
    assert message["Subject"] == "DormAlert monitor is running"
    body = message.get_content()
    assert "continuous monitor process started" in body
    assert "Monitored sites:" in body
    assert "livingscience" in body
    assert "Detector-only mode: True" in body
