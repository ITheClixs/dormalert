from __future__ import annotations

import httpx
import pytest

from src.notifier.base import NotificationEvent, NotificationSeverity
from src.notifier.callmebot import CallMeBotWhatsAppNotifier


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "Message queued. You will receive it soon.") -> None:
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)


class FakeClient:
    def __init__(self, status_code: int = 200, text: str = "Message queued. You will receive it soon.") -> None:
        self.status_code = status_code
        self.text = text
        self.calls: list[dict] = []

    def get(self, url: str, *, timeout: int, params: dict) -> FakeResponse:
        self.calls.append({"url": url, "timeout": timeout, "params": params})
        return FakeResponse(self.status_code, self.text)


def _notifier(client: FakeClient) -> CallMeBotWhatsAppNotifier:
    notifier = CallMeBotWhatsAppNotifier(phone="+41790000000", apikey="123456", timeout_seconds=20)
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


def test_opening_signal_sends_whatsapp() -> None:
    client = FakeClient()
    _notifier(client).send(_event("closed_text_missing_alert"))

    assert len(client.calls) == 1
    params = client.calls[0]["params"]
    assert params["phone"] == "+41790000000"
    assert params["apikey"] == "123456"
    assert "Living Science waitlist may be opening" in params["text"]
    assert "monitored closed text disappeared" in params["text"]
    assert client.calls[0]["timeout"] == 20


def test_availability_change_sends_whatsapp() -> None:
    client = FakeClient()
    _notifier(client).send(_event("availability_change"))

    assert len(client.calls) == 1


def test_heartbeat_sends_whatsapp_as_channel_proof_of_life() -> None:
    client = FakeClient()
    _notifier(client).send(_event("heartbeat"))

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


def test_error_body_with_http_200_is_marked_failed() -> None:
    client = FakeClient(status_code=200, text="<html><body>APIKey is invalid.</body></html>")
    with pytest.raises(RuntimeError):
        _notifier(client).send(_event("closed_text_missing_alert"))


def test_success_body_is_accepted() -> None:
    client = FakeClient(status_code=200, text="<p>Message queued. You will receive it in a few seconds.</p>")
    _notifier(client).send(_event("closed_text_missing_alert"))

    assert len(client.calls) == 1
