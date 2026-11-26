import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.rag_indexing_queue import publish_indexing_job
from app.rag_indexing_worker import handle


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_publish_indexing_job_no_url(monkeypatch):
    monkeypatch.delenv("RABBITMQ_URL", raising=False)
    monkeypatch.setenv("RABBITMQ_ENABLED", "false")
    assert publish_indexing_job({"action": "approve", "document_id": str(uuid.uuid4())}) is False


def test_publish_indexing_job_success(monkeypatch):
    monkeypatch.setenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
    monkeypatch.setenv("RABBITMQ_ENABLED", "true")

    mock_connection = MagicMock()
    mock_channel = MagicMock()
    mock_connection.channel.return_value = mock_channel

    with patch("pika.BlockingConnection", return_value=mock_connection):
        doc_id = str(uuid.uuid4())
        success = publish_indexing_job({"action": "approve", "document_id": doc_id})
        assert success is True
        mock_channel.basic_publish.assert_called_once()
        _, kwargs = mock_channel.basic_publish.call_args
        assert kwargs["routing_key"] == "q.rag.indexing"
        body = json.loads(kwargs["body"].decode())
        assert body["action"] == "approve"
        assert body["document_id"] == doc_id
        mock_connection.close.assert_called_once()


def test_publish_indexing_job_no_consumer_falls_back(monkeypatch):
    monkeypatch.setenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
    monkeypatch.setenv("RABBITMQ_ENABLED", "true")
    connection = MagicMock()
    channel = connection.channel.return_value
    channel.queue_declare.return_value.method.consumer_count = 0
    with patch("pika.BlockingConnection", return_value=connection):
        assert publish_indexing_job({"action": "approve", "document_id": str(uuid.uuid4())}) is False
    channel.basic_publish.assert_not_called()
    connection.close.assert_called_once()


@pytest.mark.anyio
async def test_worker_handle_approve_success():
    doc_id = str(uuid.uuid4())
    msg = MagicMock()
    msg.body = json.dumps({"action": "approve", "document_id": doc_id, "actor_user_id": "u1", "actor_role": "admin"}).encode()
    msg.headers = {}
    msg.ack = AsyncMock()
    msg.reject = AsyncMock()
    channel = MagicMock()

    with patch("app.rag_indexing_worker.approve_document_background") as mock_approve:
        await handle(msg, channel)
        mock_approve.assert_called_once_with(uuid.UUID(doc_id), actor_user_id="u1", actor_role="admin")
        msg.ack.assert_awaited_once()
        msg.reject.assert_not_called()


@pytest.mark.anyio
async def test_worker_handle_replace_success():
    doc_id = str(uuid.uuid4())
    msg = MagicMock()
    msg.body = json.dumps({
        "action": "replace",
        "document_id": doc_id,
        "new_content": "Luat moi ban hanh",
        "actor_user_id": "u1",
        "actor_role": "admin",
    }).encode()
    msg.headers = {}
    msg.ack = AsyncMock()
    msg.reject = AsyncMock()
    channel = MagicMock()

    with patch("app.rag_indexing_worker.replace_document_background") as mock_replace:
        await handle(msg, channel)
        mock_replace.assert_called_once_with(uuid.UUID(doc_id), "Luat moi ban hanh", actor_user_id="u1", actor_role="admin")
        msg.ack.assert_awaited_once()


@pytest.mark.anyio
async def test_worker_handle_poison_message_to_dlq():
    msg = MagicMock()
    msg.body = b"not a valid json"
    msg.headers = {}
    msg.ack = AsyncMock()
    msg.reject = AsyncMock()
    channel = MagicMock()

    await handle(msg, channel)
    msg.reject.assert_awaited_once_with(requeue=False)
    msg.ack.assert_not_called()


@pytest.mark.anyio
async def test_worker_handle_invalid_action_to_dlq():
    msg = MagicMock()
    msg.body = json.dumps({"action": "destroy_database", "document_id": str(uuid.uuid4())}).encode()
    msg.headers = {}
    msg.ack = AsyncMock()
    msg.reject = AsyncMock()
    channel = MagicMock()

    await handle(msg, channel)
    msg.reject.assert_awaited_once_with(requeue=False)
    msg.ack.assert_not_called()


@pytest.mark.anyio
async def test_worker_handle_retry_logic():
    doc_id = str(uuid.uuid4())
    msg = MagicMock()
    msg.body = json.dumps({"action": "approve", "document_id": doc_id}).encode()
    msg.headers = {"x-retry-count": 0}
    msg.content_type = "application/json"
    msg.message_id = "msg-123"
    msg.ack = AsyncMock()
    msg.reject = AsyncMock()

    channel = MagicMock()
    channel.default_exchange = MagicMock()
    channel.default_exchange.publish = AsyncMock()

    with patch("app.rag_indexing_worker.approve_document_background", side_effect=RuntimeError("DB temporarily dead")):
        await handle(msg, channel)
        channel.default_exchange.publish.assert_awaited_once()
        _, kwargs = channel.default_exchange.publish.call_args
        assert kwargs["routing_key"] == "q.rag.indexing.retry.1"
        assert kwargs["mandatory"] is True
        msg.ack.assert_awaited_once()
        msg.reject.assert_not_called()


@pytest.mark.anyio
async def test_worker_handle_max_retries_exceeded_to_dlq():
    doc_id = str(uuid.uuid4())
    msg = MagicMock()
    msg.body = json.dumps({"action": "approve", "document_id": doc_id}).encode()
    msg.headers = {"x-retry-count": 3}
    msg.content_type = "application/json"
    msg.message_id = "msg-123"
    msg.ack = AsyncMock()
    msg.reject = AsyncMock()

    channel = MagicMock()
    channel.default_exchange = MagicMock()
    channel.default_exchange.publish = AsyncMock()

    with patch("app.rag_indexing_worker.approve_document_background", side_effect=RuntimeError("Permanent failure")):
        await handle(msg, channel)
        channel.default_exchange.publish.assert_not_called()
        msg.reject.assert_awaited_once_with(requeue=False)
        msg.ack.assert_not_called()
