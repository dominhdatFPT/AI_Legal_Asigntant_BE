import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from common_auth.context import request_id_var
from common_auth.events import EventPublisher, make_event


def test_envelope_has_four_keys_without_request_id():
    event = make_event("test.event", {"value": 1})
    assert set(event) == {"event_id", "event_type", "timestamp", "payload"}
    assert event["event_id"].startswith("evt-")


def test_envelope_uses_context_request_id():
    token = request_id_var.set("req-context")
    try:
        event = make_event("test.event", {})
        override = make_event("test.event", {}, request_id="req-explicit")
    finally:
        request_id_var.reset(token)
    assert event["request_id"] == event["correlation_id"] == "req-context"
    assert override["request_id"] == override["correlation_id"] == "req-explicit"


def test_envelope_preserves_explicit_event_id():
    assert make_event("test.event", {}, event_id="evt-existing")["event_id"] == "evt-existing"


def test_publish_delegates_to_publish_envelope(monkeypatch):
    publisher = EventPublisher()
    publish_envelope = AsyncMock(return_value=True)
    monkeypatch.setattr(publisher, "publish_envelope", publish_envelope)
    assert asyncio.run(publisher.publish("test.event", {"value": 1})) is True
    publish_envelope.assert_awaited_once()
    routing_key, event = publish_envelope.await_args.args
    assert routing_key == event["event_type"] == "test.event"
    assert event["payload"] == {"value": 1}


def test_publish_envelope_without_url_returns_false(monkeypatch):
    monkeypatch.delenv("RABBITMQ_URL", raising=False)
    assert asyncio.run(EventPublisher().publish_envelope("test.event", make_event("test.event", {}))) is False


def test_non_outbox_publish_defaults_to_non_mandatory_and_sanitizes_older_envelopes(monkeypatch):
    monkeypatch.setenv("RABBITMQ_ENABLED", "true")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://broker.test/")
    exchange = SimpleNamespace(publish=AsyncMock(return_value=True))
    publisher = EventPublisher()
    monkeypatch.setattr(publisher, "_get_exchange", AsyncMock(return_value=exchange))
    event = make_event("user.logged_out", {})
    event.update(request_id="x\r\ny\0" + "a" * 150, correlation_id="req\x01-id")
    assert asyncio.run(publisher.publish_envelope("user.logged_out", event))
    assert exchange.publish.await_args.kwargs == {"routing_key": "user.logged_out", "mandatory": False}
    (message,) = exchange.publish.await_args.args
    assert message.correlation_id == "req-id"
    assert message.headers["x-request-id"] == "xy" + "a" * 126
