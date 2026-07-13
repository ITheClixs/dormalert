from __future__ import annotations

import pytest

from src.notifier.base import NotificationEvent, NotificationSeverity
from src.notifier.whatsapp_cloud import MetaWhatsAppCloudNotifier


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {"messages": [{"id": "wamid.TEST"}]}
        self.text = text or str(self._payload)

    def json(self) -> dict:
        return self._payload


class FakeClient:
    def __init__(self, response: FakeResponse | None = None) -> None:
        self.response = response or FakeResponse()
        self.calls: list[dict] = []

    def post(self, url: str, *, timeout: int, headers: dict, json: dict) -> FakeResponse:
        self.calls.append({"url": url, "timeout": timeout, "headers": headers, "json": json})
        return self.response


def _notifier(client: FakeClient) -> MetaWhatsAppCloudNotifier:
    notifier = MetaWhatsAppCloudNotifier(
        access_token="EAAG-token",
        phone_number_id="123456789012345",
        to="41790000000",
        template_name="dormalert_alert",
        template_language="en",
        timeout_seconds=20,
    )
    notifier.client = client
    return notifier


def _event(event_type: str, message: str = "The monitored closed text disappeared.") -> NotificationEvent:
    return NotificationEvent(
        event_type=event_type,
        site_id="livingscience",
        title="Living Science waitlist may be open - check now",
        message=message,
        severity=NotificationSeverity.CRITICAL,
    )


def test_opening_alert_sends_template_message() -> None:
    client = FakeClient()
    _notifier(client).send(_event("opening_alert"))

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"] == "https://graph.facebook.com/v21.0/123456789012345/messages"
    assert call["headers"]["Authorization"] == "Bearer EAAG-token"
    payload = call["json"]
    assert payload["messaging_product"] == "whatsapp"
    assert payload["to"] == "41790000000"
    assert payload["type"] == "template"
    assert payload["template"]["name"] == "dormalert_alert"
    assert payload["template"]["language"] == {"code": "en"}
    parameter = payload["template"]["components"][0]["parameters"][0]
    assert parameter["type"] == "text"
    assert "Living Science waitlist may be open" in parameter["text"]
    assert "monitored closed text disappeared" in parameter["text"]


def test_template_parameter_is_flattened_and_bounded() -> None:
    client = FakeClient()
    long_message = "line one\nline two\ttabbed    wide gap " + "x" * 2000
    _notifier(client).send(_event("opening_alert", message=long_message))

    text = client.calls[0]["json"]["template"]["components"][0]["parameters"][0]["text"]
    assert "\n" not in text
    assert "\t" not in text
    assert "    " not in text
    assert len(text) <= 900


def test_noisy_event_types_are_skipped() -> None:
    client = FakeClient()
    notifier = _notifier(client)
    for event_type in ("submission_result", "verification_result", "monitor_started"):
        notifier.send(_event(event_type))

    assert client.calls == []


def test_heartbeat_sends_as_channel_proof_of_life() -> None:
    client = FakeClient()
    _notifier(client).send(_event("heartbeat"))

    assert len(client.calls) == 1


def test_http_error_raises_so_delivery_is_marked_failed() -> None:
    client = FakeClient(
        FakeResponse(
            status_code=401,
            payload={"error": {"message": "Invalid OAuth access token"}},
            text='{"error": {"message": "Invalid OAuth access token"}}',
        )
    )
    with pytest.raises(RuntimeError):
        _notifier(client).send(_event("opening_alert"))


def test_missing_message_id_in_response_raises() -> None:
    client = FakeClient(FakeResponse(status_code=200, payload={"messages": []}))
    with pytest.raises(RuntimeError):
        _notifier(client).send(_event("opening_alert"))
