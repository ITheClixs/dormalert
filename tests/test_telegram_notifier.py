from __future__ import annotations

import httpx
import pytest

from src.notifier.base import NotificationEvent, NotificationSeverity
from src.notifier.telegram import TelegramNotifier


class FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)


class FakeClient:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.calls: list[dict] = []

    def get(self, url: str, *, timeout: int, params: dict) -> FakeResponse:
        self.calls.append({"url": url, "timeout": timeout, "params": params})
        return FakeResponse(self.status_code)


def _notifier(client: FakeClient) -> TelegramNotifier:
    notifier = TelegramNotifier(bot_token="123:ABC", chat_id="555", timeout_seconds=20)
    notifier.client = client
    return notifier


def _event(event_type: str) -> NotificationEvent:
    return NotificationEvent(
        event_type=event_type,
        site_id="livingscience",
        title="Living Science waitlist may be opening",
        message="The monitored closed text disappeared.",
        severity=NotificationSeverity.CRITICAL,
    )


def test_opening_signal_sends_telegram() -> None:
    client = FakeClient()
    _notifier(client).send(_event("closed_text_missing_alert"))

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"].endswith("/bot123:ABC/sendMessage")
    assert call["params"]["chat_id"] == "555"
    assert "Living Science waitlist may be opening" in call["params"]["text"]
    assert "monitored closed text disappeared" in call["params"]["text"]
    assert call["timeout"] == 20


def test_availability_change_sends_telegram() -> None:
    client = FakeClient()
    _notifier(client).send(_event("availability_change"))

    assert len(client.calls) == 1


def test_noisy_event_types_are_skipped() -> None:
    client = FakeClient()
    notifier = _notifier(client)
    for event_type in ("submission_result", "verification_result", "monitor_started"):
        notifier.send(_event(event_type))

    assert client.calls == []


def test_http_error_propagates_so_delivery_is_marked_failed() -> None:
    client = FakeClient(status_code=500)
    with pytest.raises(httpx.HTTPStatusError):
        _notifier(client).send(_event("closed_text_missing_alert"))
